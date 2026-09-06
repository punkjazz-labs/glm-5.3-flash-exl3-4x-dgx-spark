#!/usr/bin/env python3
"""Reject a missing IPv4 RoCEv2 path before NCCL can fall back to GID zero."""
import ipaddress
import json
import pathlib
import sys


def validate(hcas, cidr, root=pathlib.Path('/sys/class/infiniband')):
    network = ipaddress.ip_network(cidr, strict=True)
    if network.version != 4:
        raise ValueError('this launcher requires an IPv4 GID range')
    result = []
    for spec in hcas.removeprefix('=').split(','):
        name, _, port = spec.partition(':')
        if not name or name.startswith('^'):
            raise ValueError('explicit HCA names are required')
        path = root / name / 'ports' / (port or '1')
        matches = []
        for gid in sorted((path / 'gids').glob('*')):
            address = ipaddress.IPv6Address(gid.read_text().strip()).ipv4_mapped
            # Empty sysfs slots have a zero GID, but reading their type raises
            # EINVAL. Inspect attributes only for an eligible populated slot.
            if address is None or address not in network:
                continue
            kind = (path / 'gid_attrs/types' / gid.name).read_text().strip()
            if kind == 'RoCE v2':
                netdev = (path / 'gid_attrs/ndevs' / gid.name).read_text().strip()
                matches.append({'gid': int(gid.name), 'ipv4': str(address), 'netdev': netdev})
        if not matches:
            raise ValueError(f'{name}: no IPv4 RoCEv2 GID in {network}; refusing NCCL fallback')
        result.append({'hca': name, 'port': port or '1', 'matches': matches})
    return result


if __name__ == '__main__':
    print(json.dumps(validate(sys.argv[1], sys.argv[2]), sort_keys=True))
