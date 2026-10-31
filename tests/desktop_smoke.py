"""Desktop smoke test using only a synthetic value. Run explicitly in a live session."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import time

helper = str(Path.home() / '.local/bin/hush')
name = 'SELFTEST_' + secrets.token_hex(5)
value = 'synthetic-plugin-test-' + secrets.token_hex(10)


def check_prompt(proc):
    for _ in range(60):
        if proc.poll() is not None:
            raise RuntimeError('Helper exited before opening its prompt')
        window = json.loads(subprocess.check_output(['hyprctl', 'activewindow', '-j']))
        pid = window.get('pid', -1)
        if 'pinentry' in window.get('class', '').lower() and window.get('title') == 'Hush':
            status = Path('/proc') / str(pid) / 'status'
            fields = dict(line.split(':', 1) for line in status.read_text().splitlines() if ':' in line)
            if int(fields['PPid'].strip()) == proc.pid:
                return
        time.sleep(0.1)
    raise RuntimeError('Our own pinentry did not acquire focus; no keys sent')


subprocess.run(['omarchy-shell', 'shell', 'hide', 'io.github.morfilio.hush'], check=True)
time.sleep(0.3)
try:
    proc = subprocess.Popen([helper, 'request', name, '--reason', 'Automatic plugin test; dummy data only', '--ttl', '1'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    check_prompt(proc)
    subprocess.run(['wtype', value, '-k', 'Return'], check=True)
    output, error = proc.communicate(timeout=8)
    assert proc.returncode == 0, 'Request failed: ' + error
    path = Path(output.strip())
    assert path.parent == Path(os.environ['XDG_RUNTIME_DIR']) / 'agent-secrets'
    assert path.read_text() == value
    assert value not in output + error
    cached = subprocess.run([helper, 'request', name], capture_output=True, text=True, timeout=5)
    assert cached.returncode == 0 and cached.stdout == output
    subprocess.run([helper, 'remove', name], check=True)
    assert not path.exists()
    print('PASS: actual pinentry -> private file -> path-only response -> no-prompt reuse -> removal')
    proc = subprocess.Popen([helper, 'request', name, '--reason', 'Automatic cancellation test'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    check_prompt(proc)
    subprocess.run(['wtype', '-k', 'Escape'], check=True)
    output, error = proc.communicate(timeout=8)
    assert proc.returncode == 1 and not output and 'Canceled' in error
    print('PASS: actual prompt cancellation returns failure and no secret path')
finally:
    if proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=5)
    subprocess.run([helper, 'remove', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['omarchy-shell', 'shell', 'summon', 'io.github.morfilio.hush', '{}'], stdout=subprocess.DEVNULL)
