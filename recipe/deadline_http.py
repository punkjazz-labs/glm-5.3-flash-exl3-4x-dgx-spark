"""HTTP reads with a wall-clock cancellation deadline, including silent prefill."""
import contextlib
import http.client
import socket
import threading
import time
import urllib.parse


class DeadlineExpired(Exception):
    pass


@contextlib.contextmanager
def response(url, payload, timeout, cancel_after_s):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ('http', 'https'):
        raise ValueError('HTTP(S) endpoint required')
    cls = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
    conn = cls(parsed.hostname, parsed.port, timeout=min(timeout, cancel_after_s))
    expired = threading.Event()
    deadline = time.monotonic() + cancel_after_s
    transport = [None]

    def abort():
        expired.set()
        # shutdown wakes a blocked response read; close() alone can leave the
        # buffered reader's duplicate reference waiting for server data.
        sock = conn.sock or transport[0]
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    timer = threading.Timer(cancel_after_s, abort)
    timer.daemon = True
    timer.start()
    try:
        conn.request('POST', urllib.parse.urlunsplit(('', '', parsed.path, parsed.query, '')),
                     body=payload, headers={'Content-Type': 'application/json'})
        transport[0] = conn.sock  # HTTP/1.0 transfers ownership to the response
        if expired.is_set():
            raise DeadlineExpired()
        result = conn.getresponse()
        if result.status >= 400:
            raise RuntimeError(f'HTTP {result.status}: {result.read(300).decode("utf-8", "replace")}')
        with result:
            yield result
        if expired.is_set() or time.monotonic() >= deadline:
            raise DeadlineExpired()
    except (OSError, http.client.HTTPException) as exc:
        if expired.is_set() or time.monotonic() >= deadline:
            raise DeadlineExpired() from exc
        raise
    finally:
        timer.cancel()
        conn.close()
