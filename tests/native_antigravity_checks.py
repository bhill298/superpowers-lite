"""Run Antigravity's real headless CLI against a localhost Gemini fixture.

No real credentials are used. Installation, CLI state, and project files live
in temporary directories. Requests are retained only when --output is given.
"""
import argparse
import contextlib
import http.server
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

from lite_test_support import lite


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


def isolated_env(home):
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(('AGY_', 'ANTIGRAVITY_', 'GEMINI_', 'GOOGLE_')):
            env.pop(key)
    env.update({'HOME': str(home), 'USERPROFILE': str(home),
                'APPDATA': str(home / 'AppData/Roaming'),
                'LOCALAPPDATA': str(home / 'AppData/Local'),
                'XDG_CONFIG_HOME': str(home / '.config'),
                'XDG_DATA_HOME': str(home / '.local/share'),
                'AGY_CLI_DISABLE_AUTO_UPDATE': '1'})
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    exe = shutil.which('agy')
    if not exe:
        parser.error('Requires Antigravity CLI (agy) on PATH')
    if args.output:
        args.output.mkdir(parents=True, exist_ok=False)
    captured = []
    actions = []
    action_index = 0

    class Fixture(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            nonlocal action_index
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            captured.append({'path': self.path, 'body': body})
            parts = [{'text': 'FIXTURE_OK'}]
            if body.get('tools') and action_index < len(actions):
                name, arguments = actions[action_index]
                action_index += 1
                parts = [{'functionCall': {'name': name, 'args': arguments}}]
            response = {'candidates': [{'content': {'role': 'model', 'parts': parts},
                                         'finishReason': 'STOP', 'index': 0}],
                        'usageMetadata': {'promptTokenCount': 1, 'candidatesTokenCount': 1, 'totalTokenCount': 2}}
            data = ('data: ' + json.dumps(response) + '\n\n').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='lite-native-agy-') as td:
            base = Path(td)
            home, project = base / 'home', base / 'project'
            project.mkdir()
            layout = lite.Layout(str(home))
            settings = home / '.gemini/antigravity-cli/settings.json'
            settings.parent.mkdir(parents=True)
            settings.write_text(json.dumps({'modelProvider': 'gemini'}), encoding='utf-8')
            layout.antigravity.mkdir(parents=True, exist_ok=True)
            original_rules = b'# User rules\r\nFIXTURE_EXISTING_AGY_RULE\r\n'
            (layout.antigravity / 'AGENTS.md').write_bytes(original_rules)
            env = isolated_env(home)
            env.update({'GEMINI_API_KEY': 'local-fixture-only',
                        'GOOGLE_GEMINI_BASE_URL': f'http://127.0.0.1:{server.server_port}'})
            skills = sorted(p.name for p in (args.source / 'skills').iterdir()
                            if (p / 'SKILL.md').is_file() and p.name not in lite.NEVER_INSTALL)
            argv = ['--home', str(home), '--source', str(args.source.resolve()), '--only', 'antigravity',
                    '--skills', ','.join(skills)]
            with contextlib.redirect_stdout(io.StringIO()):
                lite.main(argv)
            state = lite.load_manifest(layout)['harnesses']['antigravity']
            bundle = layout.store / 'bundles' / state['bundle']
            marker = f'<!-- superpowers-lite:{lite.VERSION}:antigravity:'
            report = {'installer_version': lite.VERSION, 'bundle': state['bundle'],
                      'cli_version': subprocess.run([exe, '--version'], env=env,
                                                    capture_output=True, text=True, check=True).stdout.strip(),
                      'transport': 'localhost scripted fixture; no live model', 'cases': {}}

            def run_case(name, prompt, paths=(), installed=True, explicit=False):
                nonlocal action_index
                captured.clear()
                action_index = 0
                actions[:] = [('view_file', {'AbsolutePath': str(path), 'toolSummary': 'Skill instructions',
                                            'toolAction': 'Reading skill instructions'}) for path in paths]
                r = subprocess.run([exe, '-p', prompt, '--output-format', 'json', '--print-timeout', '30s'],
                                   cwd=project, env=env, stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, encoding='utf-8', timeout=45)
                if args.output:
                    (args.output / (name + '-requests.json')).write_text(json.dumps(captured, indent=2), encoding='utf-8')
                    (args.output / (name + '-result.json')).write_text(json.dumps({'exit_code': r.returncode, 'stdout': r.stdout, 'stderr': r.stderr}, indent=2), encoding='utf-8')
                assert r.returncode == 0, (name, r.stdout, r.stderr)
                result = json.loads(r.stdout)
                assert result['status'] == 'SUCCESS' and 'FIXTURE_OK' in result['response'], result
                assert not result.get('denied_actions'), result
                main = [r['body'] for r in captured if r['body'].get('tools')]
                assert main, 'No main-agent request reached the fixture'
                system = '\n'.join(strings(main[0].get('systemInstruction', {})))
                assert ('Superpowers Lite: explicitly started workflows' in system) == installed
                assert 'FIXTURE_EXISTING_AGY_RULE' in system
                assert all('superpowers-' + skill not in system for skill in skills), 'Manual skills advertised'
                assert (marker in json.dumps(main[0]['contents'])) == explicit, 'Wrong explicit expansion behavior'
                responses = [part['functionResponse'] for body in main for content in body['contents']
                             for part in content.get('parts', []) if 'functionResponse' in part]
                received = '\n'.join(strings(responses))
                assert action_index == len(paths), 'Did not issue every planned read'
                # Native view_file numbers lines. Every nonempty source line must
                # appear in its actual tool responses, not just the request path.
                for path in paths:
                    missing = [line.strip() for line in path.read_text(encoding='utf-8').splitlines()
                               if line.strip() and line.strip() not in received]
                    assert not missing, (name, str(path), 'Missing tool output', missing[:3], received[-1500:])
                report['cases'][name] = {'passed': True, 'main_requests': len(main),
                                        'complete_files_read': [str(p.relative_to(bundle)) for p in paths]}

            run_case('ordinary', 'Explain a basic function.')
            for skill in skills:
                paths = [bundle / 'skills' / skill / 'SKILL.md', bundle / 'tools/antigravity.md']
                if skill != 'verification-before-completion':
                    paths.append(bundle / 'skills/verification-before-completion/SKILL.md')
                run_case(skill, '/superpowers-' + skill + ' Perform the isolated fixture task.', paths, explicit=True)
            with contextlib.redirect_stdout(io.StringIO()):
                lite.main(argv)
            run_case('after-update', '/superpowers-brainstorming Perform the isolated fixture task.', explicit=True)
            with contextlib.redirect_stdout(io.StringIO()):
                lite.main(argv + ['--uninstall', '--prune'])
            assert (layout.antigravity / 'AGENTS.md').read_bytes() == original_rules
            run_case('after-uninstall', 'Explain a basic function.', installed=False)
            run_case('removed-command', '/superpowers-brainstorming Perform the isolated fixture task.', installed=False)
            report['uninstall_restored_existing_rules'] = True
            if args.output:
                (args.output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            print(json.dumps(report, indent=2))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
