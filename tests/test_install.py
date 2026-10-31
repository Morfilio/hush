import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('hush_install', ROOT / 'install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.config = self.home / '.config'
        self.plugin = self.config / 'omarchy/plugins' / installer.PLUGIN_ID
        self.shell = self.config / 'omarchy/shell.json'
        self.shell.parent.mkdir(parents=True)
        self.shell.write_text('{}')
        self.enterContext(patch.dict(os.environ, {
            'HOME': str(self.home), 'XDG_CONFIG_HOME': str(self.config),
            'XDG_STATE_HOME': str(self.home / '.local/state'),
            'CODEX_HOME': str(self.home / '.codex'),
        }))
        self.enterContext(patch.object(installer.os, 'getuid', return_value=1000))
        self.enterContext(patch.object(installer.shutil, 'which', return_value='/usr/bin/fixture'))
        self.run = self.enterContext(patch.object(installer, 'run'))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))
        old_umask = os.umask(0o077)
        self.addCleanup(os.umask, old_umask)

    def test_fresh_install_and_repeat_leave_agent_config_opt_in(self):
        installer.main([])
        binary = self.home / '.local/bin/hush'
        self.assertEqual(binary.resolve(), self.plugin / 'hush')
        self.assertEqual(binary.stat().st_mode & 0o777, 0o755)
        self.assertFalse((self.home / '.codex/AGENTS.md').exists())
        installer.main(['--configure-agents'])
        installer.main(['--configure-agents'])
        instructions = (self.home / '.codex/AGENTS.md').read_text()
        self.assertEqual(instructions.count('<!-- hush:start -->'), 1)
        self.assertIn('hush request NAME', instructions)
        self.run.assert_any_call('omarchy', 'plugin', 'enable', installer.PLUGIN_ID)

    def test_migration_preserves_layout_values_and_other_instructions(self):
        legacy = self.config / 'omarchy/plugins' / installer.LEGACY_ID
        legacy.mkdir(parents=True)
        (legacy / 'agent-secret').write_text('old helper')
        binary_dir = self.home / '.local/bin'
        binary_dir.mkdir(parents=True)
        (binary_dir / 'agent-secret').symlink_to(legacy / 'agent-secret')
        original = {'bar': {'layout': {'right': ['omarchy.clock', installer.LEGACY_ID]}},
                    'plugins': {installer.LEGACY_ID: {'custom': True}}, 'other': 42}
        self.shell.write_text(json.dumps(original))
        units = self.config / 'systemd/user'
        units.mkdir(parents=True)
        (units / 'agent-secrets-cleanup.service').write_text('ExecStart=%h/.local/bin/agent-secret cleanup')
        (units / 'agent-secrets-cleanup.timer').write_text('old timer')
        agents = self.home / '.codex/AGENTS.override.md'
        agents.parent.mkdir()
        agents.write_text('Keep before\n<!-- agent-secrets:start -->\nold\n<!-- agent-secrets:end -->\nKeep after\n')
        runtime = self.home / 'runtime/agent-secrets/example.secret'
        runtime.parent.mkdir(parents=True)
        runtime.write_text('synthetic migration fixture')
        with patch.dict(os.environ, {'XDG_RUNTIME_DIR': str(runtime.parent.parent)}):
            installer.main(['--configure-agents'])
        self.assertEqual(json.loads(self.shell.read_text()), installer.replace_id(original))
        self.assertFalse(legacy.exists())
        self.assertEqual((binary_dir / 'agent-secret').resolve(), self.plugin / 'hush')
        self.assertEqual(runtime.read_text(), 'synthetic migration fixture')
        self.assertIn('Keep before\n<!-- hush:start -->', agents.read_text())
        self.assertTrue(agents.read_text().endswith('Keep after\n'))
        self.assertNotIn('agent-secrets:start', agents.read_text())
        self.assertFalse((units / 'agent-secrets-cleanup.timer').exists())
        backups = list((self.home / '.local/state/hush/backups').rglob('agent-secret'))
        self.assertTrue(any(p.is_file() and not p.is_symlink() and p.read_text() == 'old helper' for p in backups))
        self.run.assert_any_call('systemctl', '--user', 'disable', '--now', 'agent-secrets-cleanup.timer')

    def test_unrelated_hush_command_is_never_overwritten(self):
        binary = self.home / '.local/bin/hush'
        binary.parent.mkdir(parents=True)
        binary.write_text('unrelated application')
        with self.assertRaises(SystemExit):
            installer.main([])
        self.assertEqual(binary.read_text(), 'unrelated application')
        self.assertFalse(self.plugin.exists())
        self.run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
