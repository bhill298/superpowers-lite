"""Offline Pi SDK integration; requires Node and an installed Pi npm package.

Uses temporary homes and an in-memory model stream, without credentials or
model/network usage. The companion .mjs exercises Pi's actual resource loader,
session prompt expansion, and extension tool registration.
"""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from lite_test_support import lite


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--pi-package', type=Path, required=True,
                        help='installed @earendil-works/pi-coding-agent package directory')
    args = parser.parse_args()
    package = args.pi_package.resolve()
    if not (package / 'dist/index.js').is_file() or not shutil.which('node'):
        parser.error('Requires Node and the installed Pi package with dist/index.js')
    with tempfile.TemporaryDirectory(prefix='lite-native-pi-') as td:
        base = Path(td)
        home, project = base / 'home', base / 'project'
        project.mkdir()
        layout = lite.Layout(str(home))
        layout.pi.mkdir(parents=True)
        fallback = layout.pi / 'CLAUDE.md'
        original = b'# Existing Pi rules\r\nFIXTURE_PI_RULE\r\n'
        fallback.write_bytes(original)
        settings = layout.pi / 'settings.json'
        # This setting controls command discovery, not explicit expansion.
        settings.write_text('{"enableSkillCommands": false}\n', encoding='utf-8')
        argv = ['--home', str(home), '--source', str(args.source.resolve()), '--only', 'pi']
        with contextlib.redirect_stdout(io.StringIO()):
            lite.main(argv)
        env = os.environ.copy()
        env.update({'HOME': str(home), 'USERPROFILE': str(home),
                    'PI_CODING_AGENT_DIR': str(layout.pi)})
        result = subprocess.run(['node', str(Path(__file__).with_suffix('.mjs')),
                                 str(package), str(project), str(layout.pi), lite.VERSION],
                                env=env, cwd=project, capture_output=True, text=True,
                                encoding='utf-8', timeout=90)
        if result.returncode:
            raise AssertionError(result.stdout + '\n' + result.stderr)
        report = json.loads(result.stdout)
        with contextlib.redirect_stdout(io.StringIO()):
            lite.main(argv + ['--uninstall', '--prune'])
        assert fallback.read_bytes() == original
        assert not (layout.pi / 'AGENTS.md').exists()
        assert settings.read_text() == '{"enableSkillCommands": false}\n'
        report['uninstall_preserves_rules_and_settings'] = True
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
