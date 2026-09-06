import copy
import http.server
import json
from pathlib import Path
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'recipe'))
import glm_workload as workload
import autoresearch_score as scorer


class SilentServer(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        if self.server.header_delay:
            time.sleep(0.8)
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        self.wfile.flush()
        if self.server.trickle:
            time.sleep(0.1)
            self.wfile.write(b'data: ')
            self.wfile.flush()
        time.sleep(0.8)
        try:
            self.wfile.write(b'data: [DONE]\n\n')
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_):
        pass


class CancellationTests(unittest.TestCase):
    def check_silent_prefill(self, header_delay, trickle=False):
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), SilentServer)
        server.daemon_threads = True
        server.header_delay = header_delay
        server.trickle = trickle
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        old_url = workload.URL
        workload.URL = f'http://127.0.0.1:{server.server_port}/v1'
        try:
            row = workload.stream('cancel', [{'role': 'user', 'content': 'test'}], 4,
                                  cancel_after_s=0.15, timeout=3)
            self.assertTrue(row['cancelled'], row)
            self.assertIsNone(row['error'], row)
            self.assertIsNone(row['ttft_s'], row)
            self.assertLess(row['wall_s'], 0.55, row)
        finally:
            workload.URL = old_url
            server.shutdown()
            server.server_close()

    def test_cancel_before_response_headers(self):
        self.check_silent_prefill(True)

    def test_cancel_after_headers_without_any_sse_line(self):
        self.check_silent_prefill(False)

    def test_partial_sse_line_does_not_extend_deadline(self):
        self.check_silent_prefill(False, True)


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.receipt = json.loads((ROOT / 'evidence/workload-tp4-fat.json').read_text())
        # Historical receipts predate explicit long-prompt totals.
        self.receipt['soak']['long_prompt_total'] = self.receipt['soak']['long_prompt_needle_ok']

    def test_valid_complete_receipt(self):
        _, gates = scorer.extract(self.receipt)
        self.assertTrue(all(gates.values()), gates)

    def test_soak_errors_and_failed_final_sanity_block(self):
        self.receipt['soak']['errors'] = 99
        self.receipt['sanity_end']['all_pass'] = False
        metrics, gates = scorer.extract(self.receipt)
        self.assertEqual(metrics['errors'], 99)
        self.assertFalse(gates['no_errors'])
        self.assertFalse(gates['final_sanity'])
        self.assertFalse(gates['soak'])

    def test_missing_phase_cannot_improve_score(self):
        self.receipt.pop('longgen')
        _, gates = scorer.extract(self.receipt)
        self.assertFalse(gates['complete'])
        self.assertFalse(gates['metrics'])

    def test_unknown_foreign_traffic_fails_closed(self):
        self.receipt['foreign_requests'] = None
        self.assertFalse(scorer.extract(self.receipt)[1]['clean'])

    def test_incomplete_run_is_rejected(self):
        self.receipt.pop('finished')
        self.assertFalse(scorer.extract(self.receipt)[1]['complete'])

    def test_incorrect_needle_is_rejected(self):
        self.receipt['soak']['long_prompt_needle_ok'] -= 1
        self.assertFalse(scorer.extract(self.receipt)[1]['soak'])

    def test_soak_memory_floor_is_not_hidden_by_phase_snapshots(self):
        sample = next(iter(self.receipt['soak']['samples'][0]['mem'].values()))
        sample['MemAvailable_GiB'] = 0.2
        self.assertEqual(scorer.extract(self.receipt)[0]['min_mem_gib'], 0.2)

    def test_missing_memory_is_not_a_number(self):
        sample = next(iter(self.receipt['soak']['samples'][0]['mem'].values()))
        sample.pop('MemAvailable_GiB')
        self.assertIsNone(scorer.extract(self.receipt)[0]['min_mem_gib'])

    def test_nonfinite_measurement_is_rejected(self):
        self.receipt['cold']['primary']['prefill_tps'] = float('nan')
        self.assertFalse(scorer.extract(self.receipt)[1]['metrics'])


if __name__ == '__main__':
    unittest.main()
