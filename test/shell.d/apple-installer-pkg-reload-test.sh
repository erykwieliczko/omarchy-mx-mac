#!/bin/bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/base-test.sh"

python3 - "$ROOT/apps/omarchy-apple-installer/Packaging/pkg/scripts/postinstall" <<'PY'
from pathlib import Path
import os
import shlex
import subprocess
import sys
import tempfile

source = Path(sys.argv[1]).read_text().split('\nAPP=')[0]
with tempfile.TemporaryDirectory() as temporary:
  root = Path(temporary)
  plist = root / 'daemon.plist'
  plist.touch()
  launchctl = root / 'launchctl'
  launchctl.write_text('''#!/bin/bash
set -eu
count=$(cat "$RELOAD_STATE")
case "$1" in
  bootout) exit 0 ;;
  print)
    if (( count == 99 )); then exit 0; fi
    if [[ $RELOAD_MODE == "stuck" ]] || (( count < 3 )); then
      echo $((count + 1)) > "$RELOAD_STATE"
      exit 0
    fi
    exit 1
    ;;
  bootstrap)
    echo bootstrap >> "$RELOAD_CALLS"
    (( count >= 3 )) || exit 37
    [[ $RELOAD_MODE != "invalid" ]] || exit 5
    echo 99 > "$RELOAD_STATE"
    ;;
  *) exit 1 ;;
esac
''')
  launchctl.chmod(0o700)
  script = root / 'postinstall'
  source = source.replace('PLIST=/Library/LaunchDaemons/com.omarchy.mx.installer.helper.plist',
                          'PLIST=' + shlex.quote(str(plist)))
  source = source.replace('/bin/launchctl', shlex.quote(str(launchctl)))
  for command in ('/usr/sbin/chown', '/bin/chmod', '/bin/sleep', '/usr/bin/logger'):
    source = source.replace(command, '/usr/bin/true')
  script.write_text(source)
  for mode in ('delayed', 'stuck', 'invalid'):
    state, calls = root / 'state', root / 'calls'
    state.write_text('0\n')
    calls.write_text('')
    result = subprocess.run(['/bin/bash', str(script)], capture_output=True, text=True,
                            env={**os.environ, 'RELOAD_STATE': str(state),
                                 'RELOAD_CALLS': str(calls), 'RELOAD_MODE': mode})
    assert (result.returncode == 0) == (mode == 'delayed'), (mode, result.stderr)
    if mode == 'stuck':
      assert 'timed out' in result.stderr
      assert calls.read_text() == ''
    else:
      assert calls.read_text() == 'bootstrap\n'
PY
pass "helper upgrades wait for asynchronous removal and preserve registration failures"
