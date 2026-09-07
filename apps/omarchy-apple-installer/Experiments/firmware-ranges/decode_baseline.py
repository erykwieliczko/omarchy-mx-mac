"""One-time local baseline preparation; never called by the range downloader."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import tempfile

from aea import Archive, prefix_from_file, require
from prototype import verify_local


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('input', 'lock', 'output', 'key-helper'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    lock = json.loads(args.lock.read_text())['system_image']
    require(not args.output.exists(), 'Output already exists')
    verify_local(args.input, lock)
    with args.input.open('rb') as source:
        read = lambda offset, size: os.pread(source.fileno(), size, offset)
        archive = Archive(read, prefix_from_file(read), args.key_helper)
        archive.index()
        result = hashlib.sha256()
        with tempfile.TemporaryDirectory(dir=args.output.parent) as pending:
            image = Path(pending) / 'System.dmg'
            with image.open('xb') as output, ThreadPoolExecutor(max_workers=6) as pool:
                # Python 3.14 buffersize keeps decoding memory bounded.
                for data in pool.map(archive.decode_segment, range(len(archive.segments)), buffersize=12):
                    output.write(data)
                    result.update(data)
            require(image.stat().st_size == lock['decoded']['size_bytes']
                    and result.hexdigest() == lock['decoded']['sha256'], 'Decoded baseline mismatch')
            os.link(image, args.output)
    print('Decoded baseline size and SHA-256 verified.')


if __name__ == '__main__':
    main()
