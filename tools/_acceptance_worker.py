"""Internal isolated browser worker used by the complete acceptance command."""
import argparse
import asyncio
import importlib.abc
import ipaddress
import json
import os
import socket
import sys


def install_guard():
    attempts = {'model_imports': 0, 'external_connections': 0}

    class BlockModel(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split('.')[0] == 'openai' or fullname in {'automation.model', 'automation.discovery'}:
                attempts['model_imports'] += 1
                raise RuntimeError('Model dependency prohibited during replay acceptance')

    sys.meta_path.insert(0, BlockModel())
    for key in tuple(os.environ):
        if key.startswith('OPENAI_'):
            del os.environ[key]

    def audit(event, args):
        if event == 'socket.connect':
            sock, address = args
            if sock.family == socket.AF_UNIX:
                return
            try:
                allowed = ipaddress.ip_address(address[0]).is_loopback
            except ValueError:
                allowed = False
            if not allowed:
                attempts['external_connections'] += 1
                raise RuntimeError('External network prohibited during replay acceptance')

    sys.addaudithook(audit)
    return attempts


async def main(root):
    attempts = install_guard()
    from tools.replay import run_demo
    from tools.takeover import run_simulation

    replay_status = await run_demo(root)
    takeover_status = await run_simulation(argparse.Namespace(
        capability='lookup_transaction', version='0.2.0', account='12345', transaction='12256',
        target='http://127.0.0.1:8080', evidence_root=root, headed=False, slow_mo_ms=0,
    ))
    if replay_status or takeover_status or any(attempts.values()):
        raise RuntimeError('Isolated acceptance failed')
    print(json.dumps({'guard_passed': True, 'blocked_attempts': attempts, 'model_calls': 0}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-root', required=True)
    asyncio.run(main(parser.parse_args().evidence_root))
