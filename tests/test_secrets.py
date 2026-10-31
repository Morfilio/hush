import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('agent_secret', str(ROOT / 'hush'))
spec = importlib.util.spec_from_loader(loader.name, loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)


class SecretTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'XDG_RUNTIME_DIR': self.temp.name})
        self.env.start()
        self.protection = patch.object(m, 'harden')
        self.protection.start()
        self.store = m.Store()

    def tearDown(self):
        self.protection.stop()
        self.env.stop()
        self.temp.cleanup()

    def call(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(m.main(list(args)), 0)
        return output.getvalue().strip()

    def add(self, name='TOKEN', value='dummy-fixture-only-<img src=x>\nline2'):
        with patch.object(m, 'prompt', return_value=value) as prompt:
            path = Path(self.call('request', name))
            self.assertEqual(prompt.call_count, 1)
        return path

    def test_request_returns_only_path_and_private_modes(self):
        path = self.add()
        self.assertEqual(path.read_text(), 'dummy-fixture-only-<img src=x>\nline2')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.assertNotIn('dummy-fixture', self.store.index.read_text())
        self.assertEqual(json.loads(self.call('list', '--json')), ['TOKEN'])

    def test_existing_reused_without_prompt_or_extending_expiry(self):
        path = self.add()
        original = self.store.index.read_bytes()
        with patch.object(m, 'prompt', side_effect=AssertionError('unexpected prompt')):
            self.assertEqual(self.call('request', 'TOKEN'), str(path))
        self.assertEqual(self.store.index.read_bytes(), original)

    def test_cancel_does_not_store(self):
        with patch.object(m, 'prompt', side_effect=m.SecretError('Canceled')):
            with self.assertRaises(m.SecretError):
                self.call('request', 'TOKEN')
        self.assertEqual(self.call('list', '--json'), '[]')
        self.assertFalse(list(self.store.directory.glob('*.secret')))

    def test_expiry_removes_file_and_requires_new_prompt(self):
        with patch.object(m, 'boottime', return_value=100):
            path = self.add()
        with patch.object(m, 'boottime', return_value=3701):
            self.assertEqual(self.call('path', 'TOKEN'), str(path))
        with patch.object(m, 'boottime', return_value=86501):
            self.call('cleanup')
            self.assertFalse(path.exists())
            with self.assertRaises(m.SecretError):
                self.call('path', 'TOKEN')

    def test_failed_replacement_preserves_original(self):
        path = self.add()
        with patch.object(m.Store, 'save', side_effect=OSError('failure')):
            with patch.object(m, 'prompt', return_value='replacement'):
                with self.assertRaises(OSError):
                    self.call('set', 'TOKEN')
        self.assertEqual(self.call('path', 'TOKEN'), str(path))
        self.assertEqual(len(list(self.store.directory.glob('*.secret'))), 1)

    def test_list_details_reports_bounded_expiry_without_values(self):
        with patch.object(m, 'boottime', return_value=100):
            self.add()
        with patch.object(m, 'boottime', return_value=3700):
            output = self.call('list', '--details')
            self.assertEqual(json.loads(output), [{'name': 'TOKEN', 'expires_in': 82800}])
            self.assertNotIn('dummy-fixture', output)
            # The existing name-only formats are unchanged.
            self.assertEqual(json.loads(self.call('list', '--json')), ['TOKEN'])
            self.assertEqual(self.call('list'), 'TOKEN')
        data = json.loads(self.store.index.read_text())
        data['TOKEN']['expires'] = 10 ** 9
        self.store.index.write_text(json.dumps(data))
        with patch.object(m, 'boottime', return_value=3700):
            self.assertEqual(json.loads(self.call('list', '--details'))[0]['expires_in'], 86400)

    def test_prompt_text_for_new_and_replaced_secrets(self):
        with patch.object(m, 'prompt', return_value='first') as prompt:
            self.call('request', 'TOKEN', '--reason', 'Read checks')
        description = prompt.call_args.args[0]
        self.assertIn('Secret needed: TOKEN', description)
        self.assertIn('24 hours', description)
        self.assertNotIn('Replace', description)
        self.assertEqual(prompt.call_args.kwargs['ok'], 'Send to agent')
        path = Path(self.call('path', 'TOKEN'))
        # Replacing from the panel says so, and canceling keeps the old value.
        with patch.object(m, 'prompt', side_effect=m.SecretError('Canceled')) as prompt:
            with self.assertRaises(m.SecretError):
                self.call('set', 'TOKEN')
        description = prompt.call_args.args[0]
        self.assertIn('Replace secret: TOKEN', description)
        self.assertIn('Cancel keeps it', description)
        self.assertEqual(prompt.call_args.kwargs['ok'], 'Save')
        self.assertEqual(path.read_text(), 'first')
        self.assertEqual(m.lifetime(15), '15 minutes')
        self.assertEqual(m.lifetime(60), '1 hour')

    def test_replace_removes_old_value(self):
        old = self.add()
        with patch.object(m, 'prompt', return_value='replacement'):
            new = Path(self.call('set', 'TOKEN'))
        self.assertFalse(old.exists())
        self.assertEqual(new.read_text(), 'replacement')

    def test_remove_and_clear(self):
        first = self.add('FIRST')
        second = self.add('SECOND')
        self.call('remove', 'FIRST')
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        self.call('clear')
        self.assertFalse(second.exists())
        self.assertEqual(self.call('list', '--json'), '[]')

    def test_invalid_names_and_values(self):
        for value in ['../x', '<img src="https://example.invalid">', 'A\nB', 'A;sh', 'A'*65]:
            with self.subTest(value=value), self.assertRaises(m.SecretError):
                m.valid_name(value)
        for value in ['', '\0', 'x' * (m.MAX_VALUE+1)]:
            with self.assertRaises(m.SecretError):
                m.valid_value(value)

    def test_bad_metadata_does_not_escape_directory(self):
        path = self.add()
        data = json.loads(self.store.index.read_text())
        data['TOKEN']['file'] = '../../victim'
        self.store.index.write_text(json.dumps(data))
        with self.assertRaises(m.SecretError):
            self.call('cleanup')
        self.assertTrue(path.exists())

    def test_symlink_and_public_file_refused(self):
        path = self.add()
        path.chmod(0o644)
        with self.assertRaises(m.SecretError):
            self.call('path', 'TOKEN')
        path.unlink()
        path.symlink_to(self.store.index)
        with self.assertRaises(OSError):
            self.call('path', 'TOKEN')

    def test_orphan_cleanup(self):
        self.add()
        orphan = self.store.directory / ('a'*32 + '.secret')
        orphan.write_text('dummy-orphan')
        self.call('cleanup')
        self.assertFalse(orphan.exists())

    def test_prompt_lock_excludes_second_request(self):
        with self.store.lock():
            with self.assertRaises(m.SecretError):
                self.call('request', 'TOKEN')

    def test_copy_reference_does_not_contain_value(self):
        self.add()
        with patch.object(m, 'copy_text') as copy:
            self.call('reference', 'TOKEN')
            self.assertIn('hush request TOKEN', copy.call_args.args[0])
            self.assertNotIn('dummy-fixture', copy.call_args.args[0])
            self.assertFalse(copy.call_args.kwargs['sensitive'])

    def test_markup_is_neutralized_in_prompt_sink(self):
        escaped = m.escape('<img src="https://example.invalid/track"> &\nGETPIN%')
        self.assertNotIn('<', escaped)
        self.assertNotIn('>', escaped)
        self.assertNotIn('&', escaped)
        self.assertNotIn('\n', escaped)
        self.assertIn('%0A', escaped)
        self.assertIn('%25', escaped)

    def test_all_plugin_text_has_plaintext_and_shared_sinks_are_static(self):
        qml = (ROOT/'Panel.qml').read_text()
        import re
        blocks = re.findall(r'\bText\s*\{([^}]+)', qml)
        self.assertTrue(blocks)
        for block in blocks:
            self.assertIn('textFormat: Text.PlainText', block)
        self.assertIn('tooltipText: strings.t("title")', qml)
        # Results are shown inline; nothing is broadcast as a desktop notification.
        self.assertNotIn('notify-send', qml)
        self.assertNotIn('execDetached', qml)
        # Action output (a private path) is never kept in QML state.
        self.assertIn('captureOutput: true', qml)
        self.assertEqual(qml.count('captureOutput: true'), 1)

    def test_catalogs_match_and_cover_panel_keys(self):
        import re
        en = json.loads((ROOT/'i18n/en.json').read_text())
        ro = json.loads((ROOT/'i18n/ro.json').read_text())
        self.assertEqual(set(en), set(ro))
        for key, value in en.items():
            if isinstance(value, dict):
                self.assertEqual(set(value), {'one', 'other'}, key)
                self.assertEqual(set(ro[key]), {'one', 'few', 'other'}, key)
        qml = (ROOT/'Panel.qml').read_text()
        literals = set(re.findall(r'"([A-Za-z]+(?:\.[A-Za-z]+)*)"', qml))
        plugin_id = json.loads((ROOT/'manifest.json').read_text())['id']
        used = {key for key in literals if (key in en or '.' in key) and key != plugin_id}
        self.assertEqual(used - set(en), set(), 'keys missing from catalogs')
        self.assertEqual(set(en) - literals, set(), 'catalog keys unused by the panel')

    def test_real_cli_cached_path_and_no_secret_output(self):
        path = self.add(value='dummy-subprocess-only')
        result = subprocess.run([str(ROOT/'hush'), 'request', 'TOKEN'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(path))
        self.assertNotIn('dummy-subprocess-only', result.stdout + result.stderr)
        # The consumer gets the exact value, but prints only success, never it.
        result = subprocess.run(['python3', '-c', 'import pathlib,sys; assert pathlib.Path(sys.argv[1]).read_text() == "dummy-subprocess-only"; print("consumed")', str(path)], capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), 'consumed')
        self.assertEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
