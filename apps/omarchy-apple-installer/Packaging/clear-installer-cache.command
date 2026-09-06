#!/bin/bash
# Remove downloaded installation inputs so the next run downloads them again.
set -euo pipefail
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
umask 077
mode=${1:-}
if (( $# > 1 )) || [[ -n $mode && $mode != "--check" ]]; then
  echo "Usage: $0 [--check]" >&2
  exit 64
fi
cache_user=$(id -un)
if (( EUID == 0 )); then
  cache_user=${SUDO_USER:-$(stat -f %Su /dev/console)}
fi
work=$(mktemp -d "${TMPDIR:-/tmp}/omarchy-clear-cache.XXXXXX")
trap 'rm -rf "$work"' EXIT
cat > "$work/clear-cache.py" <<'PYTHON'
import os
from pathlib import Path
import pwd
import subprocess
import sys


def require_idle(processes):
    if any(line.strip().endswith('/OmarchyAppleInstallerApp')
           or '/engine-execution-' in line for line in processes.splitlines()):
        raise RuntimeError('Close the Omarchy installer before clearing its cache.')


def cache_paths(home):
    return [Path(home) / 'Library/Application Support/com.omarchy.mx.installer/staging',
            Path('/private/var/db/com.omarchy.mx.installer-dev-cache')]


def open_directory(path):
    # Anchor every path component. Root cleanup must never follow a replaced
    # cache or user-writable parent into an unrelated directory.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open('/', flags)
    try:
        for component in path.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except FileNotFoundError:
        os.close(descriptor)
        return None
    except OSError as error:
        os.close(descriptor)
        raise RuntimeError('Cache path is not a plain directory: ' + str(path)) from error


def empty_directory(descriptor):
    with os.scandir(descriptor) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptor)
                try:
                    empty_directory(child)
                finally:
                    os.close(child)
                os.rmdir(entry.name, dir_fd=descriptor)
            else:
                os.unlink(entry.name, dir_fd=descriptor)


def clear_caches(paths):
    opened = []
    try:
        for path in paths:
            descriptor = open_directory(path)
            if descriptor is not None:
                opened.append((path, descriptor))
        for path, descriptor in opened:
            empty_directory(descriptor)
            with os.scandir(descriptor) as entries:
                if any(entries):
                    raise RuntimeError('Cache changed during cleanup: ' + str(path))
            print('Cleared: ' + str(path), flush=True)
    finally:
        for _, descriptor in opened:
            os.close(descriptor)


def main():
    if sys.platform != 'darwin':
        raise RuntimeError('This script requires macOS.')
    mode, username = sys.argv[1:]
    if mode not in ('--check', '--clear'):
        raise RuntimeError('Invalid operation.')
    user = pwd.getpwnam(username)
    if user.pw_uid < 501:
        raise RuntimeError('Launch this script from your macOS user account.')
    require_idle(subprocess.check_output(['/bin/ps', '-axo', 'comm='], text=True))
    paths = cache_paths(user.pw_dir)
    if mode == '--check':
        print('Omarchy Installer Cache\n')
        for path in paths:
            print('Download cache: ' + str(path))
        print('\nClears Omarchy downloads and the Apple firmware development cache.\n'
              'Keeps the installed app, disk partitions, settings, and diagnostic logs.', flush=True)
        return
    if os.geteuid() != 0:
        raise RuntimeError('Administrator privileges required.')
    clear_caches(paths)
    print('\nDone. The next installation will download from Backblaze and Apple again.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, KeyError, ValueError, subprocess.CalledProcessError) as error:
        print('\nSTOPPED: ' + str(error), file=sys.stderr)
        sys.exit(1)
PYTHON
/usr/bin/python3 "$work/clear-cache.py" --check "$cache_user"
[[ $mode != "--check" ]] || exit 0
if (( EUID == 0 )); then
  /usr/bin/python3 "$work/clear-cache.py" --clear "$cache_user"
else
  sudo /usr/bin/python3 "$work/clear-cache.py" --clear "$cache_user"
fi
