"""Native OpenCode V2 API probes under WSL, confined to a temporary HOME."""
import contextlib
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import tempfile
import time
import urllib.request

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from lite_test_support import lite


def main():
    binary = BASE / '.superpowers-review' / 'opencode-v2-bin' / 'opencode'
    with tempfile.TemporaryDirectory(prefix='lite-v2-') as tmp:
        root = Path(tmp)
        home = root / 'home'
        project = root / 'project'
        project.mkdir()
        subprocess.run(['git', 'init', '-q', str(project)], check=True)
        with contextlib.redirect_stdout(io.StringIO()):
            lite.main(['--home', str(home), '--source', str(BASE / '.superpowers-review' / 'upstream'),
                       '--only', 'opencode', '--opencode-version', '2'])
        env = os.environ.copy()
        for key in ['OPENCODE_CONFIG', 'OPENCODE_CONFIG_CONTENT', 'OPENCODE_CONFIG_DIR', 'OPENCODE_PERMISSION', lite.ENV_VAR]:
            env.pop(key, None)
        env.update({'HOME': str(home), 'XDG_CONFIG_HOME': str(home / '.config'), 'XDG_DATA_HOME': str(home / '.local/share'),
                    'XDG_STATE_HOME': str(home / '.local/state'), 'XDG_CACHE_HOME': str(home / '.cache'),
                    'OPENCODE_DISABLE_MODELS_FETCH': '1', 'OPENCODE_PASSWORD': 'local-test-only', 'OPENCODE_SERVER_PASSWORD': 'local-test-only'})
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        server = subprocess.Popen([str(binary), '--log-level', 'debug', 'serve', '--hostname', '127.0.0.1', '--port', str(port)], cwd=project, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cleanup = contextlib.ExitStack()
        cleanup.callback(lambda: (server.terminate(), server.wait(timeout=10)))
        # Keep one server alive: its API can answer before asynchronous plugin activation finishes.
        def api(method, path, data=None):
            request = urllib.request.Request(f'http://127.0.0.1:{port}' + path,
                data=json.dumps(data).encode() if data is not None else None, method=method,
                headers={'Content-Type': 'application/json', 'x-opencode-directory': str(project),
                         'Authorization': 'Basic ' + base64.b64encode(b'opencode:local-test-only').decode()})
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
            return json.loads(raw) if raw else None

        try:
            deadline = time.monotonic() + 30
            while True:
                try:
                    first = api('GET', '/api/skill')['data']
                    if len([e for e in first if e['name'].startswith('superpowers-')]) == 3:
                        break
                except (OSError, ValueError):
                    pass
                if time.monotonic() > deadline:
                    raise AssertionError('V2 skills did not become available')
                time.sleep(0.2)
            check(api, home)
        finally:
            try:
                (BASE / '.superpowers-review' / 'v2-native-files.log').write_text('\n'.join(p.read_text(errors='replace') for p in home.rglob('*.log')))
            finally:
                cleanup.close()


def check(api, home):
    project = home.parent / 'project'
    runs = [api('GET', '/api/skill')['data'] for _ in range(3)]
    entries = [e for e in runs[0] if e['name'].startswith('superpowers-')]
    assert len(entries) == 3, entries
    assert all(e.get('autoinvoke') is False for e in entries), entries
    canonical = sorted((e['id'], e['path']) for e in entries)
    assert all(sorted((e['id'], e['path']) for e in run if e['name'].startswith('superpowers-')) == canonical for run in runs)
    entry = next(e for e in entries if e['name'] == 'superpowers-brainstorming')
    session = api('POST', '/api/session', {'location': {'directory': str(project)}})['data']
    api('POST', f'/api/experimental/session/{session["id"]}/skill', {'id': entry['id'], 'resume': False})
    messages = api('GET', f'/api/session/{session["id"]}/message')
    assert 'The user explicitly started' in json.dumps(messages), messages
    result = {'version': '2.0.16', 'entries': canonical, 'autoinvoke_false': True,
              'stable_across_three_loads': True, 'explicit_activation_expands_entry': True,
              'model_inference': False}
    (BASE / '.superpowers-review' / 'v2-linux-native-results.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
