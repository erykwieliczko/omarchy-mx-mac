"""Experimental profile-1 AEA range reader, deliberately outside the installer.

Format references: https://github.com/kinnay/AEA/blob/main/FORMAT.md and
https://github.com/blacktop/ipsw/blob/master/pkg/aea/decrypt.go.
Only the LZFSE/SHA-256 variant used by the pinned macOS image is admitted.
"""
import hashlib
import hmac
import struct
import subprocess

import lzfse
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def derive(key, info, length=32, salt=None):
    return HKDF(algorithm=hashes.SHA256(), length=length,
                salt=salt, info=info).derive(key)


def authenticate(material, data, expected, salt=b''):
    actual = hmac.digest(material[:32], salt + data + struct.pack('<Q', len(salt)), 'sha256')
    require(hmac.compare_digest(actual, expected), 'AEA authentication failed')


def decrypt(material, data):
    context = Cipher(algorithms.AES(material[32:64]), modes.CTR(material[64:80])).decryptor()
    return context.update(data) + context.finalize()


class Archive:
    def __init__(self, read_at, prefix, key_helper):
        self.read_at = read_at
        require(len(prefix) >= 12 and prefix[:4] == b'AEA1', 'Not an AEA archive')
        profile, auth_length = struct.unpack_from('<II', prefix, 4)
        require(profile == 1 and 0 < auth_length <= 1024 * 1024, 'Unsupported AEA profile')
        self.prefix_size = 12 + auth_length + 32 + 112
        require(len(prefix) == self.prefix_size, 'Wrong AEA prefix length')
        auth_end = 12 + auth_length
        auth = prefix[12:auth_end]
        # Existing production HPKE implementation, key from Apple's FCS service.
        # The key crosses a pipe only; it is never stored in the range recipe.
        key = subprocess.check_output([str(key_helper)], input=prefix[:auth_end])
        require(len(key) == 32, 'Invalid archive key')
        self.main_key = derive(key, b'AEA_AMK' + prefix[4:8], salt=prefix[auth_end:auth_end + 32])
        root = prefix[auth_end + 32:]
        material = derive(self.main_key, b'AEA_RHEK', 80)
        authenticate(material, root[32:80], root[:32], root[80:] + auth)
        fields = struct.unpack('<QQIIBB22s', decrypt(material, root[32:80]))
        self.size, self.encrypted_size, self.segment_size, self.width, compression, checksum, _ = fields
        require(self.segment_size == 1048576 and self.width == 256
                and compression == ord('e') and checksum == 2,
                'Prototype requires 1 MiB / 256 / LZFSE / SHA-256')
        self.header_size = self.width * (40 + 32) + 32
        self.first_mac = root[80:]
        self.clusters = []
        self.segments = {}

    def _cluster(self, cluster_index, offset, expected_mac):
        raw = self.read_at(offset, self.header_size)
        require(len(raw) == self.header_size, 'Truncated cluster header')
        record = {'offset': offset, 'size': len(raw), 'sha256': digest(raw),
                  'cluster': cluster_index, 'expected_mac': expected_mac.hex()}
        self.clusters.append(record)
        cluster_key = derive(self.main_key, b'AEA_CK' + struct.pack('<I', cluster_index))
        material = derive(cluster_key, b'AEA_CHEK', 80)
        split = self.width * 40
        authenticate(material, raw[:split], expected_mac, raw[split:])
        decoded = decrypt(material, raw[:split])
        next_mac = raw[split:split + 32]
        macs = raw[split + 32:]
        offset += self.header_size
        for index in range(self.width):
            number = cluster_index * self.width + index
            plain_offset = number * self.segment_size
            plain_size, size = struct.unpack_from('<II', decoded, index * 40)
            if plain_offset >= self.size:
                require(plain_size == size == 0, 'Invalid trailing segment')
                continue
            require(0 < size <= plain_size <= self.segment_size
                    and plain_size == min(self.segment_size, self.size - plain_offset)
                    and offset + size <= self.encrypted_size, 'Invalid segment extent')
            self.segments[number] = {
                'offset': offset, 'size': size, 'plain_offset': plain_offset,
                'plain_size': plain_size, 'cluster': cluster_index, 'index': index,
                'plain_sha256': decoded[index * 40 + 8:(index + 1) * 40].hex(),
                'mac': macs[index * 32:(index + 1) * 32].hex(),
            }
            offset += size
        return offset, next_mac

    def index(self):
        offset, expected_mac, cluster_index = self.prefix_size, self.first_mac, 0
        while cluster_index * self.width * self.segment_size < self.size:
            offset, expected_mac = self._cluster(cluster_index, offset, expected_mac)
            cluster_index += 1

    def index_selected(self, records):
        # The author verified the full chain during prime(). The consumer's
        # independently pinned recipe authenticates the skipped chain links.
        # This is selected-content verification, not a full-archive hash claim.
        seen = set()
        for record in records:
            number = record['cluster']
            require(type(number) is int and number >= 0 and number not in seen
                    and number * self.width * self.segment_size < self.size,
                    'Invalid selected cluster number')
            seen.add(number)
            expected = bytes.fromhex(record['expected_mac'])
            require(len(expected) == 32, 'Invalid expected cluster MAC')
            if number == 0:
                require(expected == self.first_mac and record['offset'] == self.prefix_size,
                        'Selected cluster differs from authenticated root')
            self._cluster(number, record['offset'], expected)
            require(self.clusters[-1] == record, 'Selected cluster differs from pinned recipe')

    def decode_segment(self, index, encrypted_sha256=None):
        segment = self.segments[index]
        data = self.read_at(segment['offset'], segment['size'])
        if encrypted_sha256 is not None:
            require(digest(data) == encrypted_sha256, 'Encrypted segment SHA-256 mismatch')
        cluster_key = derive(self.main_key, b'AEA_CK' + struct.pack('<I', segment['cluster']))
        material = derive(cluster_key, b'AEA_SK' + struct.pack('<I', segment['index']), 80)
        authenticate(material, data, bytes.fromhex(segment['mac']))
        plain = decrypt(material, data)
        if segment['plain_size'] != len(plain):
            plain = lzfse.decompress(plain)
        require(len(plain) == segment['plain_size'] and digest(plain) == segment['plain_sha256'],
                'Decoded segment size/SHA-256 mismatch')
        return plain


def prefix_from_file(reader):
    header = reader(0, 12)
    length = struct.unpack_from('<I', header, 8)[0]
    require(0 < length <= 1024 * 1024, 'Invalid metadata length')
    return reader(0, 12 + length + 32 + 112)
