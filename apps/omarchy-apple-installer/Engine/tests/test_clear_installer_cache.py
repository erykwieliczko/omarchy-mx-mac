"""Exercise cache removal in temporary directories, never a real installer cache."""
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / 'Packaging/clear-installer-cache.command'
worker = types.ModuleType('clear_cache_worker')
exec(compile(SCRIPT.read_text().split("<<'PYTHON'\n", 1)[1].split('\nPYTHON\n', 1)[0],
             str(SCRIPT), 'exec'), worker.__dict__)


class ClearCacheTests(unittest.TestCase):
    def test_removes_cache_contents_but_preserves_logs_and_symlink_targets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            cache = root / 'cache'
            cache.mkdir(mode=0o700)
            (cache / 'version').mkdir()
            (cache / 'version/payload.zip').write_bytes(b'cached image')
            logs = root / 'logs'
            logs.mkdir()
            (logs / 'journal').write_text('keep')
            (cache / 'linked-directory').symlink_to(logs, target_is_directory=True)
            (cache / 'linked-file').symlink_to(logs / 'journal')
            worker.clear_caches([cache, root / 'missing'])
            self.assertEqual(list(cache.iterdir()), [])
            self.assertEqual((logs / 'journal').read_text(), 'keep')
            self.assertEqual(cache.stat().st_mode & 0o777, 0o700)

    def test_symlinked_cache_or_parent_rejects_before_clearing_any_cache(self):
        for nested in (False, True):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                first = root / 'first'
                first.mkdir()
                (first / 'payload').write_text('keep')
                other = root / 'other'
                other.mkdir()
                link = root / 'link'
                link.symlink_to(other, target_is_directory=True)
                target = link / 'cache' if nested else link
                with self.assertRaisesRegex(RuntimeError, 'plain directory'):
                    worker.clear_caches([first, target])
                self.assertEqual((first / 'payload').read_text(), 'keep')

    def test_replaced_cache_path_cannot_redirect_privileged_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            cache, other = root / 'cache', root / 'other'
            cache.mkdir()
            other.mkdir()
            (cache / 'payload').write_text('remove')
            (other / 'important').write_text('keep')
            original = worker.empty_directory
            def replace_then_clear(descriptor):
                cache.rename(root / 'original-cache')
                cache.symlink_to(other, target_is_directory=True)
                original(descriptor)
            with patch.object(worker, 'empty_directory', side_effect=replace_then_clear):
                worker.clear_caches([cache])
            self.assertEqual((other / 'important').read_text(), 'keep')
            self.assertEqual(list((root / 'original-cache').iterdir()), [])

    def test_refuses_active_app_or_engine_but_allows_idle_helper(self):
        for process in ('/Applications/Omarchy MX Mac Installer.app/Contents/MacOS/OmarchyAppleInstallerApp',
                        '/private/var/db/com.omarchy.mx.installer/engine-execution-abc/bundle/Python'):
            with self.assertRaisesRegex(RuntimeError, 'Close the Omarchy installer'):
                worker.require_idle(process)
        worker.require_idle('/Applications/Omarchy MX Mac Installer.app/Contents/Resources/omarchy-apple-installer-helper')
