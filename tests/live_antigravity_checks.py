"""Opt-in live Antigravity workflow trials using the existing OS keyring login.

Consumes model usage. Uses temporary profiles and synthetic repositories;
never copies credentials or modifies real harness configuration. Results are
evidence candidates requiring review, not automatic workflow-pass grades.
"""
import argparse
import contextlib
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

from lite_test_support import lite
from live_skill_checks import fixture
from native_antigravity_checks import isolated_env


CASES = {
    'control': (None, 'Inspect the Python utilities and explain the seeded defect. Do not change files.'),
    'brainstorming': ('brainstorming', 'Design a tiny offline calculator. Read the full skill and stop after its first clarification question. Do not write implementation code.'),
    'tdd-complete': ('test-driven-development', 'Implement double(n) in math_utils.py using TDD, including positive, zero, and negative integer tests. Preserve the other functions. Complete verification and report the result. Do not commit.'),
    'sdd-complete': ('subagent-driven-development', 'Implement plan.md using the subagent-driven workflow, including task review and final whole-branch review. The design in approved-spec.md is approved. Stay in this checkout. Local commits in this synthetic repository are allowed. Stop after verification and presenting integration options; do not push, merge, delete branches, or create a PR.'),
}


def run_case(case, source, exe, output, timeout):
    dest = output / case
    dest.mkdir()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='lite-live-agy-') as td:
        base = Path(td)
        home, project = base / 'home', base / 'project'
        fixture(project, 'implicit-debugging-control' if case == 'control' else case)
        settings = home / '.gemini/antigravity-cli/settings.json'
        settings.parent.mkdir(parents=True)
        # Permit test/repository commands, retaining the CLI permission system.
        commands = ('Get-ChildItem', 'Get-Content', 'Get-Command', 'Format-Table',
                    'Test-Path', 'git', 'python', 'python3', 'bash', 'wsl')
        allow = [
            rule for command in commands for rule in
            (f'command({command})', f'command(regex:{command} .*)')
        ]
        # Windows may expose WSL's bash first; allow the native Git Bash path
        # too, so upstream helpers can use Windows paths without translation.
        if os.name == 'nt':
            git_bash = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/bin/bash.exe'
            if git_bash.is_file():
                for path in (str(git_bash), git_bash.as_posix()):
                    allow.append(f'command(regex:& "{re.escape(path)}" .*)')
        settings.write_text(json.dumps({'permissions': {'allow': allow}}), encoding='utf-8')
        skills = sorted(p.name for p in (source / 'skills').iterdir()
                        if (p / 'SKILL.md').is_file() and p.name not in lite.NEVER_INSTALL)
        with contextlib.redirect_stdout(io.StringIO()):
            lite.main(['--home', str(home), '--source', str(source), '--only', 'antigravity',
                       '--skills', ','.join(skills)])
        state = lite.load_manifest(lite.Layout(str(home)))['harnesses']['antigravity']
        skill, task = CASES[case]
        prompt = (f'/superpowers-{skill} ' if skill else '') + task
        prompt += ('\nWork only within this synthetic project and its installed private skill library. '
                   'Do not read credentials or unrelated user data, use external services, install dependencies, '
                   'or access remote repositories. Use the available native tools. The test command is '
                   '`python -B -m unittest discover -v`. The project has no separate application dependencies.')
        env = isolated_env(home)
        if os.name == 'nt' and git_bash.is_file():
            env['PATH'] = str(git_bash.parent) + os.pathsep + env.get('PATH', '')
            prompt += ('\nTest environment: Git Bash is first on PATH; run upstream shell helpers '
                       'with bash. Use Windows Python for the unittest command above.')
        (dest / 'prompt.txt').write_text(prompt, encoding='utf-8')
        argv = [exe, '-p', prompt, '--output-format', 'stream-json', '--print-timeout', str(timeout) + 's']
        with (dest / 'events.jsonl').open('w', encoding='utf-8') as out, (dest / 'stderr.log').open('w', encoding='utf-8') as err:
            process = subprocess.Popen(argv, env=env, cwd=project,
                                       stdin=subprocess.DEVNULL, stdout=out, stderr=err)
            timed_out = False
            try:
                process.wait(timeout=timeout + 30)
            except subprocess.TimeoutExpired:
                timed_out = True
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
                else:
                    process.terminate()
                process.wait(timeout=10)
        events = []
        for line in (dest / 'events.jsonl').read_text(encoding='utf-8').splitlines():
            with contextlib.suppress(ValueError):
                events.append(json.loads(line))
        # Child tool calls are not included in the parent's stream-json feed.
        for path in (home / '.gemini/antigravity-cli/brain').glob('*/.system_generated/logs/transcript.jsonl'):
            target = dest / 'transcripts' / (path.parents[2].name + '.jsonl')
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(path, target)
        results = [e['result'] for e in events if e.get('event') == 'result']
        completed = [e['step_update'] for e in events if e.get('event') == 'step_update'
                     and e['step_update'].get('state') == 'DONE' and e['step_update'].get('tool_info')]
        report = {'case': case, 'elapsed_seconds': round(time.monotonic() - started, 1),
                  'recorded_at': datetime.now(timezone.utc).isoformat(),
                  'installer_version': lite.VERSION, 'bundle': state['bundle'],
                  'cli_version': subprocess.run([exe, '--version'], env=isolated_env(home),
                                                capture_output=True, text=True, check=True).stdout.strip(),
                  'model_selection': 'CLI default; no model override',
                  'exit_code': process.returncode, 'timed_out': timed_out,
                  'result': results[-1] if results else None,
                  'subagent_events': [e['step_update'] for e in events
                                      if e.get('event') == 'step_update'
                                      and e['step_update'].get('state') == 'DONE'
                                      and e['step_update'].get('subagent_info')],
                  'tool_errors': [e['step_update'] for e in events
                                  if e.get('event') == 'step_update'
                                  and e['step_update'].get('state') == 'ERROR'],
                  'completed_tools': [{'name': t['tool_info']['name'],
                                       'parameters': t['tool_info'].get('parameters'),
                                       'output': t['tool_info'].get('output')} for t in completed],
                  'sdd_workspace_remaining': (project / '.superpowers/sdd/plan').exists()}
        for path in project.rglob('*'):
            if path.is_file() and not any(p in ('.git', '__pycache__') for p in path.relative_to(project).parts):
                target = dest / 'project' / path.relative_to(project)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        if case in ('tdd-complete', 'sdd-complete'):
            tests = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-v'], cwd=project,
                                   capture_output=True, text=True, timeout=30)
            (dest / 'independent-tests.txt').write_text(tests.stdout + tests.stderr, encoding='utf-8')
            report['independent_tests_passed'] = tests.returncode == 0
            check = subprocess.run([sys.executable, '-B', '-c',
                                    'from math_utils import double; assert [double(x) for x in (-2,0,3)] == [-4,0,6]'],
                                   cwd=project, capture_output=True, text=True, timeout=30)
            report['independent_double_check'] = check.returncode == 0
        report['git_status'] = subprocess.run(['git', 'status', '--short'], cwd=project,
                                              capture_output=True, text=True).stdout.strip()
        for name, command in (('git-diff.txt', ['git', 'diff', 'main']),
                              ('git-log.txt', ['git', 'log', '--oneline', '--decorate'])):
            result = subprocess.run(command, cwd=project, capture_output=True, text=True, check=True)
            (dest / name).write_text(result.stdout, encoding='utf-8')
        (dest / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('case', 'elapsed_seconds', 'exit_code', 'timed_out')}), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cases', default=','.join(CASES))
    parser.add_argument('--timeout', type=int, default=480)
    args = parser.parse_args()
    cases = args.cases.split(',')
    exe = shutil.which('agy')
    if not exe or any(c not in CASES for c in cases) or len(cases) != len(set(cases)) or args.timeout < 1:
        parser.error('Requires agy, unique valid case names, and a positive timeout')
    args.output.mkdir(parents=True, exist_ok=False)
    reports = []
    for case in cases:
        reports.append(run_case(case, args.source.resolve(), exe, args.output.resolve(), args.timeout))
        (args.output / 'summary.json').write_text(json.dumps(reports, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
