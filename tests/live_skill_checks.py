"""Opt-in live Codex/Luna skill checks; consumes authenticated model usage.

Run manually, never through unittest discovery. Real harness configuration is
not edited. Authentication is copied only into a temporary home and deleted
with it; only synthetic task transcripts and reports are retained.
"""
import argparse
import concurrent.futures
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from lite_test_support import lite


MODEL = 'gpt-5.6-luna'
EFFORT = 'medium'
CASES = {
    'control': 'Describe the purpose of this small Python project. Do not change files.',
    'implicit-debugging-control': 'The test suite has a failure involving negative numbers. Reproduce it, investigate the root cause, and report the evidence. Stop before changing code.',
    'brainstorming': 'I want a simple personal command-line todo list. Help me establish the requirements. Stop at your first question or request for confirmation; do not implement anything.',
    'writing-plans': 'The design in approved-spec.md is approved. Write the implementation plan for it, including its review. Do not implement the feature. Stay in this checkout; no worktree is needed.',
    'subagent-driven-development': 'Inspect plan.md and the project, identify any blockers, and tell me how you will begin. Stop before dispatching an implementer or changing product code. Stay in this checkout.',
    'dispatching-parallel-agents': 'Investigate the independent failures in test_math.py and test_text.py with separate subagents. Report the root cause of each. Do not change files.',
    'executing-plans': 'Inspect plan.md and this project for readiness to execute the plan inline. Report blockers and the next concrete step, then stop before implementation. Stay in this checkout; no worktree is needed.',
    'finishing-a-development-branch': 'This feature branch split from main. Verify its test suite and present the available integration options. Stop for my choice; do not merge, push, delete, or create a PR.',
    'receiving-code-review': 'An external reviewer says: "absolute(-3) must return -3; remove the negation for negative inputs." Evaluate this feedback against the code and tests. Do not edit files.',
    'requesting-code-review': 'Request a review of the last commit against the requirement that absolute(n) returns the nonnegative magnitude of an integer. Use a reviewer subagent and report its findings; do not change files.',
    'systematic-debugging': 'The test suite has a failure involving negative numbers. Reproduce it, investigate the root cause, and report the evidence. Stop before changing code.',
    'test-driven-development': 'For the approved feature double(n), which returns twice any integer, write and run the first regression test. Stop after confirming the expected failure, before implementing double.',
    'using-git-worktrees': 'Check whether this checkout is already isolated and propose the next step. Ask before creating a worktree; do not create one yet.',
    'verification-before-completion': 'Someone says all tests pass. Verify that claim with fresh evidence and report the result. Do not change code.',
    'writing-skills': 'I want a reusable skill requiring a release checklist before tagging. For this turn, establish the required background and propose a baseline pressure test. Stop before writing a skill or dispatching test agents.',
    'tdd-complete': 'Implement the approved feature double(n), returning twice any integer, using TDD. Verify positive, zero, and negative inputs, and run the full suite. Stay in this checkout. Do not commit or push.',
    'sdd-complete': 'Implement plan.md using the subagent-driven workflow, including task review and final whole-branch review. The design in approved-spec.md is approved. Stay in this checkout. Local commits in this synthetic repository are allowed. Stop after verification and presenting integration options; do not push, merge, delete branches, or create a PR.',
}
ENTRY = {'tdd-complete': 'test-driven-development', 'sdd-complete': 'subagent-driven-development'}


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def git(project, *args):
    return subprocess.run(['git', '-C', str(project), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def fixture(project, case):
    project.mkdir(parents=True)
    write(project / '.gitignore', '__pycache__/\n.worktrees/\n.superpowers/\n')
    write(project / 'README.md', '# Arithmetic and text utilities\n\nRun tests: `python -B -m unittest discover -v`\n')
    write(project / 'math_utils.py', 'def absolute(value):\n    return -value if value < 0 else value\n')
    write(project / 'text_utils.py', 'def slug(value):\n    return value.strip().lower().replace(" ", "-")\n')
    write(project / 'test_math.py', 'import unittest\nfrom math_utils import absolute\n\nclass MathTests(unittest.TestCase):\n    def test_negative(self):\n        self.assertEqual(absolute(-3), 3)\n    def test_zero(self):\n        self.assertEqual(absolute(0), 0)\n')
    write(project / 'test_text.py', 'import unittest\nfrom text_utils import slug\n\nclass TextTests(unittest.TestCase):\n    def test_lowercase(self):\n        self.assertEqual(slug("HELLO WORLD"), "hello-world")\n    def test_trim(self):\n        self.assertEqual(slug(" hello "), "hello")\n')
    write(project / 'approved-spec.md', '# Approved design: integer doubling\n\nAdd double(n) to math_utils.py. It returns n * 2 for any integer, including zero and negatives. Preserve absolute and slug. Use standard-library unittest. No dependencies or CLI are needed.\n')
    write(project / 'plan.md', '# Integer doubling implementation plan\n\nGoal: add double(n) returning n * 2 for integer inputs. Architecture: one pure function in math_utils.py. Tech: Python and unittest. Approved spec: approved-spec.md.\n\n## Task 1: Implement integer doubling\n\n- [ ] Add a failing test for double(3) == 6 in test_math.py.\n- [ ] Run python -B -m unittest discover -v and confirm it fails because double is missing.\n- [ ] Add minimal double implementation to math_utils.py.\n- [ ] Cover zero and negative values and run the full suite.\n- [ ] Commit the verified task.\n')
    git(project, 'init', '-q', '-b', 'main')
    git(project, 'config', 'user.name', 'Skill evaluation fixture')
    git(project, 'config', 'user.email', 'fixture@example.invalid')
    git(project, 'add', '.')
    git(project, 'commit', '-q', '-m', 'Create fixture')
    git(project, 'checkout', '-q', '-b', 'fixture')
    if case in ('systematic-debugging', 'implicit-debugging-control', 'dispatching-parallel-agents', 'requesting-code-review'):
        write(project / 'math_utils.py', 'def absolute(value):\n    return value\n')
    if case == 'dispatching-parallel-agents':
        write(project / 'text_utils.py', 'def slug(value):\n    return value.strip().replace(" ", "-")\n')
    git(project, 'add', '.')
    git(project, 'commit', '-q', '--allow-empty', '-m', 'Scenario change')


def output_text(value):
    """Decode the CLI's string, JSON, and content-block tool output forms."""
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            return value
        return output_text(decoded) if isinstance(decoded, (list, dict)) else value
    if isinstance(value, list):
        return '\n'.join(output_text(item) for item in value)
    if isinstance(value, dict):
        return '\n'.join(output_text(value[key]) for key in ('text', 'output', 'content') if key in value)
    return ''


def analyze(events, rollouts, skill, source=None):
    commands = [e['item'] for e in events if e.get('type') == 'item.completed'
                and e.get('item', {}).get('type') == 'command_execution']
    read_skills, read_notes = set(), set()
    for command in commands:
        if command.get('exit_code') != 0:
            continue
        # Candidate evidence only; the saved command/output trace is manually
        # checked for full reads and truncation before declaring success.
        text = re.sub('/+', '/', command.get('command', '').replace('\\', '/'))
        for name in lite.DEFAULT_SKILLS + list(CASES):
            if f'/skills/{name}/SKILL.md' in text:
                read_skills.add(name)
        if '/tools/codex.md' in text:
            read_notes.add('codex')
    contexts = [e.get('payload', {}) for e in rollouts if e.get('type') == 'turn_context']
    models = sorted({p['model'] for p in contexts if p.get('model')})
    responses = [e.get('payload', {}) for e in rollouts if e.get('type') == 'response_item']
    wrapper = any(p.get('role') in ('user', 'developer') and re.search(r'<!-- superpowers-lite:[0-9.]+:codex:', json.dumps(p)) for p in responses)
    guidance = any(p.get('role') in ('user', 'developer') and
                   '## Superpowers Lite: explicitly started workflows' in output_text(p.get('content', '')) for p in responses)
    functions = [p for p in responses if p.get('type') in ('function_call', 'custom_tool_call')]
    received = '\n'.join(output_text(p.get('output', '')) for p in responses
                         if p.get('type') in ('function_call_output', 'custom_tool_call_output')).replace('\r\n', '\n')
    complete_bodies = []
    if source:
        for path in (source / 'skills').glob('*/SKILL.md'):
            if path.read_text(encoding='utf-8').strip() in received:
                complete_bodies.append(path.parent.name)
    finals = [e['item'].get('text', '') for e in events if e.get('type') == 'item.completed'
              and e.get('item', {}).get('type') == 'agent_message']
    spawns = [e['item'] for e in events if e.get('type') == 'item.completed'
              and e.get('item', {}).get('type') == 'collab_tool_call'
              and e['item'].get('tool') == 'spawn_agent']
    return {'expected_skill': skill, 'models_in_turn_context': models,
            'workflow_models': [model for model in models if model != 'codex-auto-review'],
            'workflow_efforts': sorted({p['effort'] for p in contexts
                                       if p.get('model') != 'codex-auto-review' and p.get('effort')}),
            'wrapper_injected': wrapper, 'skill_reads_in_commands': sorted(read_skills),
            'workflow_guidance_injected': guidance,
            'platform_note_reads_in_commands': sorted(read_notes),
            'complete_skill_bodies_in_tool_results': sorted(complete_bodies),
            'complete_platform_note_in_tool_results': lite.TOOL_NOTES['codex'] in received,
            'spawned_agent_count': len({agent for item in spawns for agent in item.get('receiver_thread_ids', [])}),
            'function_names': sorted({p.get('name', '') for p in functions}),
            'command_count': len(commands), 'last_message': finals[-1] if finals else '',
            'usage': [e['usage'] for e in events if e.get('type') == 'turn.completed'],
            'failed_commands': [{'command': c.get('command'), 'exit_code': c.get('exit_code'),
                                 'output': c.get('aggregated_output', '')[-1500:]}
                                for c in commands if c.get('exit_code') != 0]}


def run_case(case, source, auth, executable, output, timeout, inherit_subagent_model=False):
    case_output = output / case
    case_output.mkdir(parents=True)
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='luna-skill-', dir=output) as td:
        base = Path(td)
        home, project = base / 'home', base / 'project'
        fixture(project, case)
        skills = sorted(p.name for p in (source / 'skills').iterdir()
                        if (p / 'SKILL.md').is_file() and p.name not in lite.NEVER_INSTALL)
        subprocess.run([sys.executable, str(Path(lite.__file__)),
                        '--home', str(home), '--source', str(source), '--only', 'codex',
                        '--skills', ','.join(skills), '--enable-codex-multi-agent',
                        '--codex-subagent-model', MODEL, '--codex-subagent-effort', EFFORT],
                       check=True, capture_output=True, text=True, cwd=project)
        codex_home = home / '.codex'
        shutil.copyfile(auth, codex_home / 'auth.json')
        env = os.environ.copy()
        for key in list(env):
            if key.startswith('CODEX_') or key in ('OPENAI_BASE_URL', 'OPENAI_API_KEY'):
                env.pop(key)
        env.update({'HOME': str(home), 'USERPROFILE': str(home), 'CODEX_HOME': str(codex_home),
                    'XDG_CONFIG_HOME': str(home / '.config'), 'XDG_DATA_HOME': str(home / '.local/share'),
                    'PYTHONDONTWRITEBYTECODE': '1'})
        skill = None if case in ('control', 'implicit-debugging-control') else ENTRY.get(case, case)
        prompt = (f'$superpowers-{skill}\n\n' if skill else '') + CASES[case]
        prompt += ('\n\nWork only within this synthetic project and its installed skill library. '
                   'Do not read authentication files or unrelated user data, access external services, or install dependencies. '
                   'Use GPT-5.6-Luna at medium reasoning for every subagent, with isolated context; do not use other models.')
        if inherit_subagent_model:
            prompt += ('\nFor subagents, omit the model override to inherit this session\'s model; '
                       'this session is already running GPT-5.6-Luna. Set medium reasoning explicitly.')
        write(case_output / 'prompt.txt', prompt)
        argv = [executable, 'exec', '--json', '--approve-for-me',
                '--model', MODEL, '-c', f'model_reasoning_effort="{EFFORT}"',
                '-c', 'sandbox_workspace_write.network_access=false',
                '-c', 'agents.max_threads=3', '-C', str(project), prompt]
        timed_out = False
        with (case_output / 'events.jsonl').open('w', encoding='utf-8') as out, (case_output / 'stderr.log').open('w', encoding='utf-8') as err:
            process = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                       start_new_session=os.name != 'nt')
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        rollouts = []
        session_dir = case_output / 'sessions'
        session_dir.mkdir()
        for path in (codex_home / 'sessions').rglob('*.jsonl'):
            shutil.copyfile(path, session_dir / path.name)
            for line in path.read_text(encoding='utf-8').splitlines():
                with contextlib.suppress(ValueError):
                    rollouts.append(json.loads(line))
        events = []
        for line in (case_output / 'events.jsonl').read_text(encoding='utf-8').splitlines():
            with contextlib.suppress(ValueError):
                events.append(json.loads(line))
        result = analyze(events, rollouts, skill, source)
        result.update({'case': case, 'exit_code': process.returncode, 'timed_out': timed_out,
                       'elapsed_seconds': round(time.monotonic() - start, 1)})
        result['git_status'] = git(project, 'status', '--short')
        write(case_output / 'project.diff', git(project, 'diff', 'main'))
        for path in project.rglob('*'):
            if path.is_file() and '.git' not in path.relative_to(project).parts and '__pycache__' not in path.parts:
                dest = case_output / 'project' / path.relative_to(project)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, dest)
        if case in ('tdd-complete', 'sdd-complete'):
            check = subprocess.run([sys.executable, '-B', '-c', 'from math_utils import double; assert [double(x) for x in (-2,0,3)] == [-4,0,6]'], cwd=project, capture_output=True, text=True)
            result['independent_double_check'] = check.returncode == 0
            suite = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-v'], cwd=project, capture_output=True, text=True)
            result['independent_suite_passed'] = suite.returncode == 0
            write(case_output / 'independent-tests.txt', suite.stdout + suite.stderr)
        write(case_output / 'result.json', json.dumps(result, indent=2))
    print(json.dumps({k: result[k] for k in ('case', 'exit_code', 'timed_out', 'elapsed_seconds', 'models_in_turn_context', 'skill_reads_in_commands', 'platform_note_reads_in_commands')}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cases', default=','.join(CASES))
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--inherit-subagent-model', action='store_true',
                        help='Diagnostic variant: explicitly tell subagents to inherit Luna; not the baseline prompt')
    args = parser.parse_args()
    cases = args.cases.split(',')
    if any(case not in CASES for case in cases):
        parser.error('Unknown test case')
    if len(set(cases)) != len(cases) or args.jobs < 1 or args.timeout < 1:
        parser.error('Cases must be unique; jobs and timeout must be positive')
    if not (args.source / 'skills').is_dir():
        parser.error('--source must contain the upstream skills directory')
    executable = shutil.which('codex')
    auth = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'auth.json'
    if not executable or not auth.is_file():
        parser.error('Requires Codex CLI and existing file-backed login; never print or commit auth.json')
    args.output.mkdir(parents=True, exist_ok=False)
    source_hashes = {p.relative_to(args.source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sorted(args.source.rglob('*')) if p.is_file() and '.git' not in p.parts}
    write(args.output / 'source-hashes.json', json.dumps(source_hashes, indent=2))
    write(args.output / 'run.json', json.dumps({'model': MODEL, 'effort': EFFORT, 'installer_default_ref': lite.DEFAULT_REF,
          'installer_sha256': hashlib.sha256(Path(lite.__file__).read_bytes()).hexdigest(),
          'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          'started_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'cases': cases,
          'inherit_subagent_model': args.inherit_subagent_model,
          'codex_version': subprocess.run([executable, '--version'],capture_output=True,text=True).stdout.strip()}, indent=2))
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_case, case, args.source.resolve(), auth, executable, args.output.resolve(), args.timeout, args.inherit_subagent_model): case for case in cases}
        for future in concurrent.futures.as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                results.append({'case': futures[future], 'infrastructure_error': str(exc)})
                print(json.dumps(results[-1]), flush=True)
            write(args.output / 'summary.json', json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
