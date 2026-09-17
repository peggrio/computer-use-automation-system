"""Evidence scan negative controls and isolated worker enforcement."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.check_evidence import scan
from tools.validate_contracts import ROOT


class EvidenceTests(unittest.TestCase):
    def check_value(self, value, passed, secrets=()):
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'evidence.json').write_text(json.dumps({'value': value}))
            report = scan(root, secrets)
            self.assertEqual(report['passed'], passed)
            self.assertNotIn(str(value), json.dumps(report))

    def test_secrets_and_fixture_values_rejected(self):
        for value in ['STEP8-SECRET-CANARY', 'PRIVATE-CANARY', 'john', 'demo',
                      'sk-proj-'+'a'*40, 'account=12345', 12256, 'John Smith', 'Check #123']:
            with self.subTest(value_type=type(value).__name__): self.check_value(value, False)
        self.check_value('prefix-unique-local-credential-suffix', False, ['unique-local-credential'])

    def test_valid_timestamp_not_confused_with_fixture_id(self):
        self.check_value('2026-09-17T01:02:03.123450+00:00', True)
        self.check_value('2026-99-17T01:02:03.12345+00:00', False)

    def test_empty_malformed_unknown_and_symlink_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertFalse(scan(root)['passed'])
            path = Path(root, 'bad.json'); path.write_text('not json')
            self.assertFalse(scan(root)['passed'])
            path.unlink()
            path = Path(root, 'raw.txt'); path.write_text('uninspected')
            self.assertFalse(scan(root)['passed'])
            path.unlink(); path.symlink_to('/not/a/real/file')
            self.assertFalse(scan(root)['passed'])

    def test_jsonl_all_records_scanned(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'events.jsonl').write_text('{}\n{"data":"STEP8-SECRET-CANARY"}\n')
            self.assertFalse(scan(root)['passed'])

    def test_worker_blocks_model_and_external_connection(self):
        code = '''
from tools.acceptance_worker import install_guard
counts = install_guard()
import socket
for module in ('openai', 'automation.model', 'automation.discovery'):
    try: __import__(module)
    except RuntimeError: pass
    else: raise AssertionError('model guard failed')
try:
    socket.socket().connect(('203.0.113.1', 443))
except RuntimeError: pass
else: raise AssertionError('network guard failed')
assert counts == {'model_imports': 3, 'external_connections': 1}
'''
        subprocess.run([sys.executable, '-c', code], cwd=ROOT, check=True, capture_output=True)
