#!/usr/bin/python3
"""Install Hush for the current desktop user; optionally configure local agents."""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import tempfile

PLUGIN_ID = 'io.github.morfilio.hush'
LEGACY_ID = 'andi.agent-secrets'
FILES = ('manifest.json', 'Panel.qml', 'hush', 'README.md', 'LICENSE')


def run(*args):
    subprocess.run(list(args), check=True)


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def replace_id(value):
    if isinstance(value, str):
        return PLUGIN_ID if value == LEGACY_ID else value
    if isinstance(value, list):
        return [replace_id(item) for item in value]
    if isinstance(value, dict):
        return {replace_id(key): replace_id(item) for key, item in value.items()}
    return value


def agent_document(previous, instructions):
    block = '<!-- hush:start -->\n## Local secret requests with Hush\n\n' + instructions.strip() + '\n<!-- hush:end -->'
    pattern = r'<!-- (?:agent-secrets|hush):start -->.*?<!-- (?:agent-secrets|hush):end -->'
    if re.search(pattern, previous, flags=re.S):
        return re.sub(pattern, lambda _: block, previous, flags=re.S)
    return previous.rstrip() + ('\n\n' if previous.strip() else '') + block + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--configure-agents', action='store_true', help='Add/update Hush instructions for Codex and Claude Code')
    args = parser.parse_args(argv)
    os.umask(0o077)
    if os.getuid() == 0:
        parser.error('Run as your desktop user, without sudo.')
    for command in ('omarchy', 'omarchy-shell', 'systemctl', 'wl-copy', 'timeout'):
        if not shutil.which(command):
            parser.error('Missing dependency: ' + command)
    if not (shutil.which('pinentry-qt') or shutil.which('pinentry')):
        parser.error('Install pinentry (pinentry-qt recommended).')

    source = Path(__file__).resolve().parent
    home = Path.home()
    config = Path(os.environ.get('XDG_CONFIG_HOME') or home / '.config')
    state = Path(os.environ.get('XDG_STATE_HOME') or home / '.local/state')
    plugin = config / 'omarchy/plugins' / PLUGIN_ID
    legacy = config / 'omarchy/plugins' / LEGACY_ID
    binary_dir = home / '.local/bin'
    shell_config = config / 'omarchy/shell.json'
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup = state / 'hush/backups' / stamp

    def preserve(path):
        if not (path.exists() or path.is_symlink()):
            return
        # Absolute path structure keeps external CODEX_HOME backups distinct.
        destination = backup / path.relative_to(path.anchor)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if path.is_dir() and not path.is_symlink():
            shutil.copytree(path, destination)
        else:
            shutil.copy2(path, destination, follow_symlinks=False)

    def owned_link(path):
        return path.is_symlink() and path.resolve() in {
            plugin / 'hush', legacy / 'agent-secret', source / 'hush'}

    for directory in (plugin, legacy):
        if directory.is_symlink():
            parser.error('Refusing a symlinked plugin directory: ' + str(directory))
    binary = binary_dir / 'hush'
    if (binary.exists() or binary.is_symlink()) and not owned_link(binary):
        parser.error('An unrelated ~/.local/bin/hush already exists; move it before installing.')
    if shell_config.exists():
        try:
            json.loads(shell_config.read_text())
        except (ValueError, OSError):
            parser.error('Cannot read shell.json as JSON; leaving the installation unchanged.')
    run('omarchy', 'plugin', 'validate', str(source))

    preserve(shell_config)
    if source.resolve() != plugin.resolve():
        preserve(plugin)
        plugin.mkdir(parents=True, exist_ok=True)
        for name in FILES:
            shutil.copy2(source / name, plugin / name)
        shutil.copytree(source / 'i18n', plugin / 'i18n', dirs_exist_ok=True)
    (plugin / 'hush').chmod(0o755)
    binary_dir.mkdir(parents=True, exist_ok=True)
    for name in ('hush', 'agent-secret'):
        path = binary_dir / name
        if (path.exists() or path.is_symlink()) and not owned_link(path):
            print('Keeping unrelated compatibility command: ' + str(path))
            continue
        preserve(path)
        path.unlink(missing_ok=True)
        path.symlink_to(plugin / 'hush')

    units = config / 'systemd/user'
    units.mkdir(parents=True, exist_ok=True)
    for name in ('hush-cleanup.service', 'hush-cleanup.timer'):
        preserve(units / name)
        shutil.copy2(source / 'systemd' / name, units / name)
    if (units / 'agent-secrets-cleanup.timer').exists():
        # Replace only this application's old units, preserving their contents.
        old_service = units / 'agent-secrets-cleanup.service'
        if old_service.is_file() and '%h/.local/bin/agent-secret cleanup' in old_service.read_text():
            run('systemctl', '--user', 'disable', '--now', 'agent-secrets-cleanup.timer')
            for name in ('agent-secrets-cleanup.service', 'agent-secrets-cleanup.timer'):
                path = units / name
                preserve(path)
                path.unlink(missing_ok=True)

    if args.configure_agents:
        instructions = runpy.run_path(str(source / 'hush'))['INSTRUCTIONS']
        codex = Path(os.environ.get('CODEX_HOME') or home / '.codex')
        override = codex / 'AGENTS.override.md'
        codex_file = override if override.is_file() and override.stat().st_size else codex / 'AGENTS.md'
        for path in (codex_file, home / '.claude/CLAUDE.md'):
            previous = path.read_text() if path.exists() else ''
            updated = agent_document(previous, instructions)
            if updated != previous:
                preserve(path)
                atomic_text(path, updated)

    # Keep existing bar position/settings and all runtime secret files.
    if shell_config.exists():
        previous = json.loads(shell_config.read_text())
        migrated = replace_id(previous)
        if migrated != previous:
            atomic_text(shell_config, json.dumps(migrated, indent=2) + '\n')
    if legacy.exists():
        preserve(legacy)
        shutil.rmtree(legacy)

    run('systemctl', '--user', 'daemon-reload')
    run('systemctl', '--user', 'enable', '--now', 'hush-cleanup.timer')
    run('omarchy-shell', 'shell', 'rescanPlugins')
    run('omarchy', 'plugin', 'enable', PLUGIN_ID)
    print('Installed Hush. Backups: ' + str(backup))
    if args.configure_agents:
        print('Hush instructions updated for Codex and Claude Code. New sessions load them automatically.')


if __name__ == '__main__':
    main()
