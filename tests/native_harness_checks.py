"""Offline native discovery and OpenCode command-expansion smoke checks.

Uses fresh homes, a local mock model endpoint, and the audited upstream source.
No live credentials or external inference endpoints are used.
"""
import contextlib
import http.server
import io
import json
import os
from pathlib import Path
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lite_test_support import lite

BASE = Path(__file__).resolve().parents[1]
REPORT = BASE / '.superpowers-review' / 'v2-native-results.json'


def main():
    reports = {}
    with tempfile.TemporaryDirectory(prefix='lite-native-') as temporary:
        root = Path(temporary)
        home, project = root / 'home', root / 'project'
        project.mkdir()
        subprocess.run(['git', 'init', '-q', str(project)], check=True)
        with contextlib.redirect_stdout(io.StringIO()):
            lite.main(['--home', str(home), '--source', str(BASE / '.superpowers-review' / 'upstream'),
                       '--only', 'codex,opencode,claude', '--opencode-version', '1'])
        layout = lite.Layout(str(home))
        env = os.environ.copy()
        for key in ['OPENCODE_CONFIG', 'OPENCODE_CONFIG_CONTENT', 'OPENCODE_CONFIG_DIR', 'OPENCODE_PERMISSION', lite.ENV_VAR]:
            env.pop(key, None)
        env.update({'HOME': str(home), 'USERPROFILE': str(home), 'CODEX_HOME': str(layout.codex),
                    'CLAUDE_CONFIG_DIR': str(layout.claude), 'XDG_CONFIG_HOME': str(home / '.config'),
                    'XDG_DATA_HOME': str(home / '.local' / 'share'), 'XDG_CACHE_HOME': str(home / '.cache'),
                    'XDG_STATE_HOME': str(home / '.local' / 'state'), 'OPENCODE_DISABLE_MODELS_FETCH': '1',
                    'OPENCODE_DISABLE_DEFAULT_PLUGINS': '1'})

        def run(argv, timeout=45):
            r = subprocess.run(argv, cwd=project, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
            if r.returncode:
                raise AssertionError(f'{argv}: {r.returncode}\n{r.stderr[-2000:]}\n{r.stdout[-1000:]}')
            return r.stdout

        codex = shutil.which('codex')
        if codex:
            proc = subprocess.Popen([codex, 'app-server', '--stdio', '--strict-config'], cwd=project, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
            messages = queue.Queue()
            threading.Thread(target=lambda: [messages.put(line) for line in proc.stdout], daemon=True).start()
            try:
                proc.stdin.write(json.dumps({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'lite-test', 'version': '1'}}}) + '\n')
                proc.stdin.flush()
                while json.loads(messages.get(timeout=20)).get('id') != 1:
                    pass
                proc.stdin.write(json.dumps({'method': 'initialized'}) + '\n' + json.dumps({'id': 2, 'method': 'skills/list', 'params': {'cwds': [str(project)], 'forceReload': True}}) + '\n')
                proc.stdin.flush()
                while True:
                    reply = json.loads(messages.get(timeout=20))
                    if reply.get('id') == 2:
                        break
                ours = [s for s in reply['result']['data'][0]['skills'] if s['name'].startswith('superpowers-')]
                assert len(ours) == 3 and not reply['result']['data'][0]['errors'], reply
                reports['codex_discovery'] = {'count': len(ours), 'scope': sorted(set(s['scope'] for s in ours))}
                (BASE / '.superpowers-review' / 'v2-codex-skills.json').write_text(json.dumps(reply, indent=2))
            finally:
                proc.stdin.close()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    proc.wait(timeout=10)
            for name, prompt in [('ordinary', 'Explain a basic function.'), ('explicit', '$superpowers-brainstorming design a widget')]:
                output = run([codex, 'debug', 'prompt-input', prompt])
                (BASE / '.superpowers-review' / ('v2-codex-' + name + '.json')).write_text(output, encoding='utf-8')
                # The user message itself names the skill in the explicit case;
                # test the wrapper marker to establish actual content loading.
                included = '<!-- superpowers-lite:2.0.0:codex:' in output
                reports['codex_debug_' + name + '_entry_loaded'] = included
                if name == 'ordinary':
                    assert not included, 'Manual skill leaked into ordinary prompt'
                else:
                    if not included:
                        print('Codex debug prompt-input did not expand the skill; inspecting skills/list separately.')
        else:
            reports['codex'] = 'not installed'

        # Native OpenCode is a standalone executable in the Windows npm package.
        opencode = os.environ.get('SUPERPOWERS_TEST_OPENCODE')
        if not opencode and os.name == 'nt':
            candidate = Path(os.environ['APPDATA']) / 'npm' / 'node_modules' / 'opencode-ai' / 'bin' / 'opencode.exe'
            opencode = str(candidate) if candidate.is_file() else None
        if not opencode and os.name != 'nt':
            candidate = shutil.which('opencode')
            if candidate and not candidate.startswith('/mnt/'):
                opencode = candidate
        if opencode:
            seen = []
            for _ in range(3):
                skills = json.loads(run([opencode, '--pure', 'debug', 'skill']))
                ours = {s['name']: s['location'] for s in skills if s['name'].startswith('superpowers-')}
                assert len(ours) == 3, ours
                assert all(str(layout.opencode) in p for p in ours.values()), ours
                seen.append(ours)
            assert seen[0] == seen[1] == seen[2]
            reports['opencode_discovery'] = {'count': len(seen[0]), 'stable_runs': 3, 'duplicate_claude_agents_roots': False}

            captured = []
            class MockModel(http.server.BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass
                def do_POST(self):
                    body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                    captured.append({'path': self.path, 'body': json.loads(body)})
                    if 'count_tokens' in self.path:
                        data = json.dumps({'input_tokens': 20}).encode()
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.send_header('Content-Length', str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                        return
                    if '/messages' in self.path:
                        message = {'id': 'msg_fixture', 'type': 'message', 'role': 'assistant', 'model': 'claude-sonnet-4-5',
                                   'content': [], 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 20, 'output_tokens': 0}}
                        events = [('message_start', {'type': 'message_start', 'message': message}),
                                  ('content_block_start', {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}}),
                                  ('content_block_delta', {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'fixture response'}}),
                                  ('content_block_stop', {'type': 'content_block_stop', 'index': 0}),
                                  ('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': 2}}),
                                  ('message_stop', {'type': 'message_stop'})]
                        data = ''.join('event: ' + kind + '\ndata: ' + json.dumps(value) + '\n\n' for kind, value in events).encode()
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/event-stream')
                        self.send_header('Content-Length', str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                        return
                    # OpenAI Responses streaming event sequence; no tools are run.
                    response = {'id': 'resp_fixture', 'object': 'response', 'created_at': 1, 'status': 'completed',
                                'model': 'gpt-4.1', 'output': [{'id': 'msg_fixture', 'type': 'message', 'role': 'assistant',
                                'status': 'completed', 'content': [{'type': 'output_text', 'text': 'fixture response', 'annotations': []}]}],
                                'usage': {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2}}
                    if json.loads(body).get('stream'):
                        events = [('response.created', {'type': 'response.created', 'response': dict(response, status='in_progress', output=[])}),
                                  ('response.output_item.added', {'type': 'response.output_item.added', 'output_index': 0, 'item': response['output'][0]}),
                                  ('response.output_text.delta', {'type': 'response.output_text.delta', 'item_id': 'msg_fixture', 'output_index': 0, 'content_index': 0, 'delta': 'fixture response'}),
                                  ('response.completed', {'type': 'response.completed', 'response': response})]
                        data = ''.join('event: ' + kind + '\ndata: ' + json.dumps(value) + '\n\n' for kind, value in events).encode()
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/event-stream')
                    else:
                        data = json.dumps(response).encode()
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
            mock = http.server.ThreadingHTTPServer(('127.0.0.1', 0), MockModel)
            threading.Thread(target=mock.serve_forever, daemon=True).start()
            cfg = json.loads(layout.config('opencode').read_text())
            cfg.update({'model': 'openai/gpt-4.1', 'provider': {'openai': {'options': {'baseURL': f'http://127.0.0.1:{mock.server_port}/v1', 'apiKey': 'local-fixture-only'}}}})
            layout.config('opencode').write_text(json.dumps(cfg))
            try:
                output = run([opencode, '--pure', 'run', '--command', 'superpowers-brainstorming', 'design a widget'], timeout=60)
                assert captured, 'No request reached the local mock provider'
                raw = json.dumps(captured[-1]['body'])
                assert '<!-- superpowers-lite:2.0.0:opencode:' in raw, 'Explicit slash command did not expand the wrapper'
                # Body appears in the explicit user prompt, but should not be
                # advertised as an available skill in system/tool instructions.
                body = captured[-1]['body']
                inputs = body.get('input', [])
                system = json.dumps([x for x in inputs if isinstance(x, dict) and x.get('role') in ('system', 'developer')])
                tools = json.dumps(body.get('tools', []))
                assert 'superpowers-brainstorming' not in system + tools, 'Manual-only skill was advertised to the model'
                reports['opencode_explicit_command'] = {'expanded_despite_skill_deny': True, 'not_advertised_to_model': True, 'provider': 'localhost mock only'}
                if codex:
                    captured.clear()
                    endpoint = f'http://127.0.0.1:{mock.server_port}/v1'
                    flags = ['-c', 'model_provider="fixture"', '-c', 'model_providers.fixture.name="Fixture"',
                             '-c', 'model_providers.fixture.wire_api="responses"',
                             '-c', 'model_providers.fixture.base_url=' + json.dumps(endpoint)]
                    output = run([codex, 'exec', '--sandbox', 'read-only', '--model', 'gpt-6-sol', *flags,
                                  '$superpowers-brainstorming design a widget'], timeout=60)
                    assert captured, 'Codex did not reach the local mock'
                    raw = json.dumps(captured[-1]['body'])
                    assert '<!-- superpowers-lite:2.0.0:codex:' in raw, 'Codex explicit entry was not injected'
                    reports['codex_explicit_invocation'] = {'entry_injected': True, 'provider': 'localhost mock only'}
                claude = Path(os.environ.get('APPDATA', '')) / 'npm' / 'node_modules' / '@anthropic-ai' / 'claude-code' / 'bin' / 'claude.exe'
                if os.name == 'nt' and claude.is_file():
                    env.update({'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{mock.server_port}', 'ANTHROPIC_API_KEY': 'local-fixture-only',
                                'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'DISABLE_AUTOUPDATER': '1'})
                    env.pop('ANTHROPIC_AUTH_TOKEN', None)
                    env.pop('CLAUDE_CODE_SIMPLE', None)
                    env.pop('CLAUDE_CODE_SAFE_MODE', None)
                    for kind, prompt in [('ordinary', 'Explain a basic function.'), ('explicit', '/superpowers-brainstorming design a widget')]:
                        captured.clear()
                        output = run([str(claude), '--setting-sources', 'user', '--settings', '{"disableAllHooks":true}',
                                      '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '-p',
                                      '--model', 'claude-sonnet-4-5', '--output-format', 'json', prompt], timeout=60)
                        bodies = [x['body'] for x in captured if '/messages' in x['path'] and 'count_tokens' not in x['path']]
                        assert bodies, 'Claude did not reach the local mock: ' + output[-2000:] + ' paths=' + str([x['path'] for x in captured])
                        raw = json.dumps(bodies[-1])
                        included = '<!-- superpowers-lite:2.0.0:claude:' in raw
                        assert included == (kind == 'explicit'), (kind, included)
                        if kind == 'ordinary':
                            assert 'superpowers-brainstorming' not in raw, 'Claude advertised a manual-only command'
                        reports['claude_' + kind] = {'entry_injected': included, 'provider': 'localhost mock only'}
            finally:
                mock.shutdown()
                mock.server_close()
        else:
            reports['opencode'] = 'not installed'
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(reports, indent=2), encoding='utf-8')
    print(json.dumps(reports, indent=2))


if __name__ == '__main__':
    main()
