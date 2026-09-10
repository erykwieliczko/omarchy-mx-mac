"""Network distribution must never accidentally include an OS in the app."""
import json
import io
import tarfile
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
    def candidate(self, root):
        engine = root / 'engine'
        engine.mkdir()
        profiles = [build_release.load_profile(CLEANROOM / f'profiles/{model}.json')
                    for model in ('j713', 'j700')]
        (engine / 'installer-v-test.tar.gz').write_bytes(b'engine')
        (engine / 'installer_data.json').write_text(json.dumps({'os_list': [{
            'package': 'payload.zip', 'cleanroom': {'sources': profile['sources'],
                                                  'device_identifier': profile['device_identifier']}}
            for profile in profiles]}))
        (engine / 'receipt.json').write_text(json.dumps({
            'version': 'v-test',
            'engine': build_release.descriptor(engine / 'installer-v-test.tar.gz'),
            'metadata': build_release.descriptor(engine / 'installer_data.json')}))
        payload = root / 'payload.zip'
        payload.write_bytes(b'os payload')
        payload.with_suffix('.receipt.json').write_text(json.dumps({
            'payload': build_release.descriptor(payload), 'profiles': profiles}))
        return engine, payload

    def test_release_only_bundles_engine_and_keeps_payload_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            engine, payload = self.candidate(root)
            output = root / 'Release'
            with patch('builtins.print'):
                build_release.build(engine, payload, output, 'https://downloads.example.test/m4',
                                    execution_scratch_bytes=8_589_934_592)
            self.assertEqual([p.name for p in (output / 'Assets').iterdir()], ['installer-v-test.tar.gz'])
            models = json.loads((output / 'catalog.json').read_text())['models']
            self.assertEqual([m['deviceIdentifier'] for m in models], ['apple,j713', 'apple,j700'])
            self.assertEqual(models[0]['payloadDigest'], models[1]['payloadDigest'])
            self.assertEqual(models[0]['payloadArtifact'], models[1]['payloadArtifact'])
            model = models[0]
            self.assertEqual(model['executionScratchBytes'], 8_589_934_592)
            self.assertEqual(model['payloadDigest'], build_release.digest(payload))
            self.assertEqual(model['payloadArtifact']['sourceURL'], 'https://downloads.example.test/m4/payload.zip')
            verifier = CLEANROOM.parents[1] / 'Packaging/verify-bundled-assets.py'
            subprocess.run([sys.executable, str(verifier), str(output), '--engine-only'], check=True, capture_output=True)
            (output / 'Assets/payload.zip').write_bytes(b'os payload')
            result = subprocess.run([sys.executable, str(verifier), str(output), '--engine-only'], capture_output=True)
            self.assertNotEqual(result.returncode, 0)

    def test_reusing_hosted_payload_preserves_signed_byte_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            engine, payload = self.candidate(root)
            with patch('builtins.print'):
                build_release.build(engine, payload, root / 'Release',
                                    'https://downloads.example.test/new',
                                    execution_scratch_bytes=8_589_934_592,
                                    payload_source_url='https://downloads.example.test/old/payload.zip')
            model = json.loads((root / 'Release/catalog.json').read_text())['models'][0]
            self.assertEqual(model['payloadArtifact']['sourceURL'], 'https://downloads.example.test/old/payload.zip')
            self.assertEqual(model['payloadDigest'], build_release.digest(payload))
            self.assertTrue(model['engineArtifact']['sourceURL'].startswith('https://downloads.example.test/new/'))
            payload.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'receipt mismatch'):
                build_release.build(engine, payload, root / 'BadRelease',
                                    'https://downloads.example.test/new',
                                    execution_scratch_bytes=8_589_934_592,
                                    payload_source_url='https://downloads.example.test/old/payload.zip')
            self.assertFalse((root / 'BadRelease').exists())

    def test_reused_payload_url_rejects_wrong_name_or_insecure_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            engine, payload = self.candidate(root)
            for url in ('http://downloads.example.test/payload.zip',
                        'https://u:p@downloads.example.test/payload.zip',
                        'https://downloads.example.test/wrong.zip',
                        'https://downloads.example.test/payload.zip?q=1'):
                with self.assertRaisesRegex(ValueError, 'payload URL'):
                    build_release.build(engine, payload, root / 'Release',
                                        'https://downloads.example.test/new',
                                        execution_scratch_bytes=8_589_934_592, payload_source_url=url)
                self.assertFalse((root / 'Release').exists())

    def test_stale_receipts_and_component_graph_fail_before_signing(self):
        for changed in ('engine', 'metadata', 'payload', 'profile'):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                engine, payload = self.candidate(root)
                if changed == 'profile':
                    receipt = payload.with_suffix('.receipt.json')
                    record = json.loads(receipt.read_text())
                    record['profiles'][0]['sources']['linux'] = '0' * 40
                    receipt.write_text(json.dumps(record))
                else:
                    path = {'engine': engine / 'installer-v-test.tar.gz',
                            'metadata': engine / 'installer_data.json', 'payload': payload}[changed]
                    with path.open('ab') as writer:
                        writer.write(b' ')
                with self.assertRaisesRegex(ValueError, 'mismatch'):
                    build_release.build(engine, payload, root / 'Release',
                                        'https://downloads.example.test/m4',
                                        execution_scratch_bytes=8_589_934_592)
                self.assertFalse((root / 'Release').exists())

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

    def test_catalog_reserves_retained_budget_and_not_preparation_peak(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            engine, payload = self.candidate(root)
            metadata = engine / 'installer_data.json'
            value = json.loads(metadata.read_text())
            value['os_list'][0]['cleanroom']['apple_inputs'] = {'execution_scratch_bytes': 8 * 1024**3, 'preflight_scratch_bytes': 64 * 1024**3}
            metadata.write_text(json.dumps(value))
            receipt_path = engine / 'receipt.json'
            receipt = json.loads(receipt_path.read_text())
            receipt['metadata'] = build_release.descriptor(metadata)
            receipt_path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, 'retained Apple inputs'):
                build_release.build(engine, payload, root / 'output',
                                    'https://downloads.example.test/m4',
                                    execution_scratch_bytes=8 * 1024**3 - 1)
            self.assertFalse((root / 'output').exists())
            with patch('builtins.print'):
                build_release.build(engine, payload, root / 'output',
                                    'https://downloads.example.test/m4',
                                    execution_scratch_bytes=8 * 1024**3)
            model = json.loads((root / 'output/catalog.json').read_text())['models'][0]
            self.assertEqual(model['executionScratchBytes'], 8 * 1024**3)


    def test_development_cache_must_stay_reserved_after_resize(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            engine, payload = self.candidate(root)
            artifact = engine / 'installer-v-test.tar.gz'
            with tarfile.open(artifact, 'w:gz') as archive:
                marker = tarfile.TarInfo('./cleanroom/development-apple-cache')
                marker.size = 3
                archive.addfile(marker, io.BytesIO(b'dev'))
            metadata = engine / 'installer_data.json'
            value = json.loads(metadata.read_text())
            value['os_list'][0]['cleanroom']['apple_inputs'] = json.loads(
                (CLEANROOM / 'profiles/j713-apple-inputs.json').read_text())
            metadata.write_text(json.dumps(value))
            receipt_path = engine / 'receipt.json'
            receipt = json.loads(receipt_path.read_text())
            receipt.update(engine=build_release.descriptor(artifact),
                           metadata=build_release.descriptor(metadata), development_apple_cache=True)
            receipt_path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, 'cache must remain reserved'):
                build_release.build(engine, payload, root / 'output',
                                    'https://downloads.example.test/m4', execution_scratch_bytes=8 * 1024**3)
            self.assertFalse((root / 'output').exists())
            with patch('builtins.print'):
                build_release.build(engine, payload, root / 'output',
                                    'https://downloads.example.test/m4', execution_scratch_bytes=20 * 1024**3)
