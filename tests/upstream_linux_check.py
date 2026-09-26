"""Exercise pinned upstream download/install/helpers on the native Linux filesystem."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lite_test_support import lite


def command(argv, cwd):
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(f'{argv}: {result.returncode}\n{result.stdout}\n{result.stderr}')
    return result.stdout


def main():
    assert os.name == 'posix', 'Run this check under WSL/Linux'
    with tempfile.TemporaryDirectory(prefix='lite-upstream-') as temp:
        base = Path(temp)
        home = base / 'home with spaces'
        with contextlib.redirect_stdout(io.StringIO()) as log:
            lite.main(['--home', str(home), '--only', 'codex,opencode,claude', '--opencode-version', '1'])
        layout = lite.Layout(str(home))
        state = json.loads(layout.manifest.read_text())
        record = state['harnesses']['codex']
        assert record['provenance']['git_commit'] == lite.DEFAULT_REF
        bundle = layout.store / 'bundles' / record['bundle']
        assert lite.fingerprint(bundle) == record['bundle']
        repo = base / 'work project'
        repo.mkdir()
        command(['git', 'init', '-q', str(repo)], base)
        command(['git', 'config', 'user.name', 'Installer test'], repo)
        command(['git', 'config', 'user.email', 'installer-test@example.invalid'], repo)
        plan = repo / 'plan.md'
        plan.write_text('# Plan\n\n## Task 1: Verify installation\n\n- [ ] Run deterministic check.\n')
        command(['git', 'add', 'plan.md'], repo)
        command(['git', 'commit', '-q', '-m', 'fixture'], repo)
        scripts = bundle / 'skills' / 'executing-plans' / 'scripts'
        started = command([str(scripts / 'task-start'), str(plan), '1'], repo)
        assert 'brief:' in started and 'base:' in started, started
        base_commit = command(['git', 'rev-parse', 'HEAD'], repo).strip()
        (repo / 'result.txt').write_text('implementation fixture\n')
        command(['git', 'add', 'result.txt'], repo)
        command(['git', 'commit', '-q', '-m', 'implementation'], repo)
        done = command([str(scripts / 'task-done'), str(plan), '1', base_commit, '--', 'printf', 'tests passed\n'], repo)
        assert 'Task 1: complete' in done, done
        helper = bundle / 'skills' / 'subagent-driven-development' / 'scripts' / 'review-package'
        review = command([str(helper), str(plan), base_commit, 'HEAD'], repo)
        failed = subprocess.run([str(scripts / 'task-done'), str(plan), '2', base_commit, '--', 'bash', '-c', 'echo failed; exit 7'], cwd=repo, text=True, capture_output=True)
        assert failed.returncode == 7 and 'NOT recorded' in failed.stderr, failed
        print(json.dumps({'platform': sys.platform, 'python': sys.version.split()[0], 'commit': lite.DEFAULT_REF,
                          'download_install': 'passed', 'task_start': started.strip(), 'task_done': done.strip(),
                          'review_package': review.strip(), 'failed_test_exit_preserved': failed.returncode,
                          'all_helpers_directly_executable': True}, indent=2))


if __name__ == '__main__':
    main()
