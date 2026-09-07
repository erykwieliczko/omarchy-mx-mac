"""Build the experimental helpers without changing the production decoder."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = here.parents[1] / 'Engine/cleanroom/restore-image/main.go'
    original = source.read_text()
    if original.count('func main() {') != 1:
        raise ValueError('Production key helper source changed')
    work = output / 'key-helper-source'
    work.mkdir(exist_ok=True)
    (work / 'original.go').write_text(original[:original.index('func main() {')].replace('\t"os/signal"\n', '').replace('\t"syscall"\n', ''))
    (work / 'main.go').write_bytes((here / 'fcs-main.go.txt').read_bytes())
    (work / 'go.mod').write_text('module omarchy.local/firmware-range-key\n\ngo 1.26\n')
    subprocess.run(['go', 'build', '-trimpath', '-o', str(output / 'fcs-key'), '.'], cwd=work, check=True)
    subprocess.run(['go', 'build', '-trimpath', '-mod=readonly', '-o', str(output / 'apfs-reader'), '.'],
                   cwd=here / 'apfs-reader', check=True)
    receipt = {'go': subprocess.check_output(['go', 'version'], text=True).strip(),
               'production_key_source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
               'helpers': {name: hashlib.sha256((output / name).read_bytes()).hexdigest()
                           for name in ('fcs-key', 'apfs-reader')}}
    (output / 'helpers.json').write_text(json.dumps(receipt, indent=2) + '\n')


if __name__ == '__main__':
    main()
