"""Network distribution must never accidentally include an OS in the app."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

CLEANROOM = Path(__file__).resolve().parents[1] / 'cleanroom'
sys.path.insert(0, str(CLEANROOM))
import build_release


class DownloadReleaseTests(unittest.TestCase):
    def test_release_only_bundles_engine_and_keeps_payload_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            engine = root / 'engine'
            engine.mkdir()
            (engine / 'receipt.json').write_text(json.dumps({'version': 'v-test'}))
            (engine / 'installer-v-test.tar.gz').write_bytes(b'engine')
            (engine / 'installer_data.json').write_text(json.dumps({'os_list': [{'package': 'payload.zip'}]}))
            payload = root / 'payload.zip'
            payload.write_bytes(b'os payload')
            output = root / 'Release'
            with patch('builtins.print'):
                build_release.build(engine, payload, output, 'https://downloads.example.test/m4',
                                    execution_scratch_bytes=8_589_934_592)
            self.assertEqual([p.name for p in (output / 'Assets').iterdir()], ['installer-v-test.tar.gz'])
            model = json.loads((output / 'catalog.json').read_text())['models'][0]
            self.assertEqual(model['executionScratchBytes'], 8_589_934_592)
            self.assertEqual(model['payloadDigest'], build_release.digest(payload))
            self.assertEqual(model['payloadArtifact']['sourceURL'], 'https://downloads.example.test/m4/payload.zip')
            verifier = CLEANROOM.parents[1] / 'Packaging/verify-bundled-assets.py'
            subprocess.run([sys.executable, str(verifier), str(output), '--engine-only'], check=True, capture_output=True)
            (output / 'Assets/payload.zip').write_bytes(b'os payload')
            result = subprocess.run([sys.executable, str(verifier), str(output), '--engine-only'], capture_output=True)
            self.assertNotEqual(result.returncode, 0)

    def test_http_requires_explicit_private_local_address(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for url, enabled in [('http://100.64.0.1:8765/test', False), ('http://8.8.8.8/test', True)]:
                with self.assertRaises(ValueError):
                    build_release.build(root, root, root / 'output', url, private_http=enabled)
                self.assertFalse((root / 'output').exists())

    def test_mismatched_payload_name_fails_before_release_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'installer_data.json').write_text(json.dumps({'os_list': [{'package': 'payload.zip'}]}))
            with self.assertRaisesRegex(ValueError, 'metadata package'):
                build_release.build(root, root / 'payload-v2.zip', root / 'output',
                                    'https://downloads.example.test/m4',
                                    execution_scratch_bytes=8_589_934_592)
            self.assertFalse((root / 'output').exists())

    def test_missing_or_unsafe_host_fails_before_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for url in [None, 'http://example.test', 'https://u:p@example.test', 'https://example.test?x=1']:
                with self.assertRaises(ValueError):
                    build_release.build(root, root, root / 'output', url)
                self.assertFalse((root / 'output').exists())

    def test_scratch_budget_is_required_before_release_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for budget in (None, 0, -1, True, 2**64):
                with self.assertRaises(ValueError):
                    build_release.build(root, root, root / 'output',
                                        'https://downloads.example.test/m4',
                                        execution_scratch_bytes=budget)
                self.assertFalse((root / 'output').exists())
