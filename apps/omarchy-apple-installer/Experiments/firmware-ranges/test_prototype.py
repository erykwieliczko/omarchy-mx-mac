import hashlib
import hmac
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import lzfse

from aea import Archive, decrypt, derive, digest, prefix_from_file
from prototype import fetch, locate_storage, normalize, range_get


def mac(material, data, salt=b''):
    return hmac.digest(material[:32], salt + data + struct.pack('<Q', len(salt)), 'sha256')


def fixture():
    """Two clusters in ~200 KiB ciphertext, no Apple bytes or release keys."""
    key, salt, auth = b'K' * 32, b'S' * 32, b'test'
    header = b'AEA1' + struct.pack('<II', 1, len(auth))
    main = derive(key, b'AEA_AMK' + header[4:8], salt=salt)
    plain = b'ABCD' * 262144
    compressed = lzfse.compress(plain)
    last = b'last segment' * 79
    final_size = 256 * len(plain) + len(last)
    cluster_parts = []
    for cluster_index in range(2):
        cluster_key = derive(main, b'AEA_CK' + struct.pack('<I', cluster_index))
        descriptions, macs, payload = bytearray(), bytearray(), bytearray()
        for index in range(256):
            if cluster_index == 0:
                original, encoded = plain, compressed
            elif index == 0:
                original, encoded = last, last
            else:
                descriptions.extend(bytes(40)); macs.extend(bytes(32)); continue
            material = derive(cluster_key, b'AEA_SK' + struct.pack('<I', index), 80)
            cipher = decrypt(material, encoded)
            descriptions.extend(struct.pack('<II', len(original), len(cipher)) + hashlib.sha256(original).digest())
            macs.extend(mac(material, cipher))
            payload.extend(cipher)
        material = derive(cluster_key, b'AEA_CHEK', 80)
        cluster_parts.append((material, decrypt(material, descriptions), bytes(macs), bytes(payload)))
    following, clusters = bytes(32), []
    for material, descriptions, macs, payload in reversed(cluster_parts):
        cluster = descriptions + following + macs + payload
        following = mac(material, descriptions, following + macs)
        clusters.insert(0, cluster)
    total = len(header) + len(auth) + 32 + 112 + sum(map(len, clusters))
    root = struct.pack('<QQIIBB22s', final_size, total, 1048576, 256, ord('e'), 2, bytes(22))
    material = derive(main, b'AEA_RHEK', 80)
    encrypted = decrypt(material, root)
    prefix = header + auth + salt + mac(material, encrypted, following + auth) + encrypted + following
    return prefix + b''.join(clusters), key, plain, last


class RangePrototypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.key, cls.plain, cls.last = fixture()

    def archive(self, data=None, reads=None):
        data = self.data if data is None else data
        def read(offset, size):
            if reads is not None:
                reads.append((offset, size))
            return data[offset:offset + size]
        with patch('aea.subprocess.check_output', return_value=self.key):
            return Archive(read, prefix_from_file(read), 'unused')

    def test_selected_cluster_skips_previous_ciphertext_and_handles_partial_final_segment(self):
        complete = self.archive(); complete.index()
        self.assertEqual(complete.decode_segment(0), self.plain)
        reads = []
        sparse = self.archive(reads=reads)
        sparse.index_selected([complete.clusters[1]])
        self.assertEqual(sparse.decode_segment(256), self.last)
        skipped = complete.clusters[0]['offset']
        second = complete.clusters[1]['offset']
        self.assertFalse(any(skipped <= offset < second for offset, size in reads))
        self.assertLess(sum(size for offset, size in reads), 25000)

    def test_tampered_root_cluster_and_ciphertext_are_rejected(self):
        complete = self.archive(); complete.index()
        root_corrupt = bytearray(self.data); root_corrupt[60] ^= 1
        with self.assertRaises(ValueError): self.archive(bytes(root_corrupt))
        cluster_corrupt = bytearray(self.data)
        cluster_corrupt[complete.clusters[1]['offset']] ^= 1
        sparse = self.archive(bytes(cluster_corrupt))
        with self.assertRaises(ValueError): sparse.index_selected([complete.clusters[1]])
        segment_corrupt = bytearray(self.data)
        segment_corrupt[complete.segments[256]['offset']] ^= 1
        sparse = self.archive(bytes(segment_corrupt)); sparse.index_selected([complete.clusters[1]])
        with self.assertRaisesRegex(ValueError, 'authentication'): sparse.decode_segment(256)
        with self.assertRaisesRegex(ValueError, 'SHA-256'): sparse.decode_segment(256, '0' * 64)

    def test_fragmented_storage_deduplicates_already_selected_segments(self):
        left, right = bytes(range(128)), bytes(range(128, 256))
        chosen = {1}
        regions = [(0, left), (1048576, left), (2097152, right)]
        spans = locate_storage(left + right, regions, [100, 100, 100], chosen)
        self.assertEqual(spans, [{'offset': 1048576, 'size': 128}, {'offset': 2097152, 'size': 128}])
        self.assertEqual(chosen, {1, 2})

    def test_normalization_matches_existing_nvram_conversion(self):
        self.assertEqual(normalize(b' first =hello\nsecond= world \n', 'nvram'), b'first=hello\nsecond= world \n')

    def test_wrong_recipe_stops_before_network_and_creates_no_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); recipe = root / 'recipe.json'; output = root / 'output'
            recipe.write_text('{}')
            args = SimpleNamespace(recipe=recipe, recipe_sha256='0' * 64, output=output)
            with patch('prototype.range_get') as get:
                with self.assertRaisesRegex(ValueError, 'Recipe SHA-256'): fetch(args)
                get.assert_not_called()
            self.assertFalse(output.exists())

    def test_corrupt_range_stops_before_key_or_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); recipe = root / 'recipe.json'; output = root / 'output'
            recipe.write_text(json.dumps({'schema': 1, 'source': {'url': 'unused', 'size_bytes': 10},
                'prefix': {'offset': 0, 'size': 1, 'sha256': digest(b'a')}, 'headers': [], 'segments': {},
                'member_offset': 1, 'zip_header': {'offset': 0, 'size': 1, 'sha256': digest(b'a')}}))
            args = SimpleNamespace(recipe=recipe, recipe_sha256=digest(recipe.read_bytes()), output=output, file=None)
            with patch('prototype.range_get', return_value=b'b'), patch('prototype.Archive') as archive:
                with self.assertRaisesRegex(ValueError, 'range SHA-256'): fetch(args)
                archive.assert_not_called()
            self.assertFalse(output.exists())

    def test_server_ignoring_ranges_is_rejected_before_reading_body(self):
        class Response:
            status_code = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
        with patch('prototype.requests.get', return_value=Response()):
            with self.assertRaisesRegex(ValueError, 'byte range'):
                range_get('https://updates.cdn-apple.com/test.ipsw', 100, 4, 8)


if __name__ == '__main__':
    unittest.main()
