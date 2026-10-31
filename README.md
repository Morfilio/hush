# Hush

An Omarchy shell plugin for handing temporary secrets to local agents. No account,
vault password, unlock step, or network service. Usually you do nothing in advance:
when an agent needs a credential, it asks and a local paste prompt opens. To add one
yourself, click the key icon, type a name, press Enter, and paste the value.

## Install

Requires Omarchy with shell plugin support, Python 3.10+, pinentry-qt (or a graphical
pinentry), wl-clipboard, GNU coreutils, and systemd user services.
Run as your desktop user, without sudo:

```bash
git clone https://github.com/Morfilio/hush.git
cd hush
python3 install.py --configure-agents
```

This enables the bar widget and cleanup timer, installs `~/.local/bin/hush`, and
adds Hush instructions to Codex and Claude Code. Make sure `~/.local/bin` is on
PATH. New agent sessions load the instructions automatically; existing sessions
can use the setup text in the panel's help. Omit `--configure-agents` to leave agent
instructions unchanged. Backups go in `$XDG_STATE_HOME/hush/backups` (normally
`~/.local/state/hush/backups`).

## Give an agent a secret

Agents can ask for missing credentials themselves:

```bash
hush request GITHUB_TOKEN --reason 'Read the repository checks'
```

This opens one local masked input and returns **only a private file path**.
If that name is already available, it returns the same path without prompting.
The agent reads the value inside the command that needs it:

```bash
TOKEN_FILE=$(hush request GITHUB_TOKEN --reason 'Read repository checks') &&
  GH_TOKEN=$(cat "$TOKEN_FILE") gh pr checks
```

Never run `cat` by itself, enable shell tracing, or print the value into tool output.
The command still runs through the agent's normal execution permissions. This is
a local helper; a cloud-only chat cannot read this computer's files.

## Panel and CLI

The panel has a name field with **Paste…**, one status line, and the list of
secrets with the time each has left. The name field starts empty. If the name
already exists, the button reads **Replace…** and the prompt says it will replace
the value; canceling keeps the old one. After a successful paste the panel reopens
with the new secret selected. Copy, forget, and failures report on the status line
without closing the panel or sending desktop notifications.

- **Copy for agent** (the labeled button on each row) copies a short note naming the
  secret, never its value. Use it to point an agent at a secret you added.
- **Copy value** (copy icon) copies the actual secret for 30 seconds with Wayland's
  sensitive clipboard hint. Pasting that into chat puts the value into the conversation.
- **Forget** (trash icon) and **Forget all** ask for a second click before removing.
- The **?** icon shows a short explanation and **Copy setup text for another agent**,
  needed only for agents that did not get the installed instructions.
- Keyboard: type a name and press Enter to paste; ↓ or Tab moves to the list; ↑/↓
  select; Enter copies for agent; C copies the value; Delete twice forgets; / returns
  to the name field; ? toggles help; Esc closes.
- `hush open` toggles the panel.
- `hush list` lists names; `--json` returns a JSON array of names;
  `--details` returns `[{"name": ..., "expires_in": seconds}]` (never values).
- `hush request NAME --ttl 15` sets a new secret's lifetime to 15 minutes.
- `hush set NAME` or `request NAME --fresh` replaces the value.
- `hush path NAME` prints an existing path without opening a prompt.
- `hush remove NAME`, `clear`, and `cleanup` forget, clear, and expire entries.

With `--configure-agents`, the installer adds a short Hush section to user-level
Codex and Claude instructions. New sessions pick it up; for an older session, copy the setup text
from the panel's help. Other agents can use the same CLI without a dedicated integration.
Canceling the prompt cancels the request; it never substitutes a dummy value.

## Lifetime and storage

Values live in `$XDG_RUNTIME_DIR/agent-secrets` (normally
`/run/user/<uid>/agent-secrets`), in randomly named `0600` files in a `0700`
directory. On Omarchy, the user runtime filesystem is tmpfs. The internal directory
name is retained for compatibility with earlier installations. This is temporary
storage, **not an encrypted vault**. Names and expiry metadata contain no values.
Only the helper and pinentry handle input; secret values are never sent through
Omarchy IPC or into the plugin's QML state.

Default lifetime is 24 hours, configurable per CLI request from 1 minute to 24 hours.
Reusing a name does not extend its lifetime. Expiry uses the boot clock, including
suspend time. The user systemd timer sweeps every 30 seconds (with 5 seconds of
scheduling tolerance); all CLI operations also sweep before delivery. Logout
removes the runtime directory when the last user session ends (user lingering can
retain it); reboot always removes it. Cleanup continues if the shell closes.
Forgetting/expiry cannot revoke a copy already read by a program or pasted elsewhere.

As in T3 Code, this prevents accidental inclusion in transcripts. It does not hide
credentials from arbitrary programs running as your user: an agent allowed to read
the file could print or transmit it. Child output is not redacted. The helper disables
core dumps, but tmpfs can swap and consumer programs have their own memory behavior.
Clipboard expiration cannot remove copies a clipboard manager or recipient kept.

## Inspiration and sources

T3 Code's `request_secret` offers a private input card. Its file delivery returns an
owner-only file path and sweeps files after 24 hours. This plugin uses the same
path-returning pattern and 24-hour default, with local desktop input and runtime
storage. It is an independent implementation, not a T3 integration.

- [T3 Code file-delivery change](https://github.com/pingdotgg/t3code/pull/24809)
- [T3 secret request implementation](https://github.com/pingdotgg/t3code/blob/main/apps/server/src/secrets/SecretRequests.ts)
- [T3 secret input card](https://github.com/pingdotgg/t3code/blob/main/apps/web/src/components/chat/SecretRequestCard.tsx)
- [Codex global instructions](https://learn.chatgpt.com/docs/agent-configuration/agents-md)

## Update and validate

From your clone:

```bash
git pull --ff-only
python3 install.py --configure-agents
```

The installer preserves existing secret files and bar placement when migrating
from the original plugin. `agent-secret` remains a compatibility alias; new
instructions use `hush`.

Run the test suite and Omarchy's manifest validation:

```bash
python3 -m unittest discover -s tests -v
omarchy plugin validate .
```

`tests/desktop_smoke.py` is an optional interactive check. It opens a real paste
prompt and uses `wtype` to enter dummy data; run it only on an idle desktop.

## Uninstall

To forget stored values, run `hush clear` before removing the helper. Disable the
widget and cleanup timer:

```bash
omarchy plugin disable io.github.morfilio.hush
systemctl --user disable --now hush-cleanup.timer
```

Remove `~/.config/omarchy/plugins/io.github.morfilio.hush`, the `hush` and
`agent-secret` symlinks in `~/.local/bin`, and `hush-cleanup.service` and
`hush-cleanup.timer` in `~/.config/systemd/user`. Then run
`systemctl --user daemon-reload`. If you configured agents, remove only the
`<!-- hush:start -->` through `<!-- hush:end -->` blocks from your Codex and
Claude instruction files. Paths above assume the default XDG configuration home.

## License

[MIT](LICENSE), copyright 2026 Andi Cojocaru.
