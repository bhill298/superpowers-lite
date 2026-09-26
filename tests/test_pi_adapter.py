"""Pi discovery, instruction precedence, and independent installation lifecycle."""
import contextlib
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_superpowers_lite as fixtures
from lite_test_support import lite


class PiTests(unittest.TestCase):
    setUp = fixtures.InstallerTests.setUp
    run_cli = fixtures.InstallerTests.run_cli
    manifest = fixtures.InstallerTests.manifest
    snapshot = fixtures.InstallerTests.snapshot

    def test_manual_entries_and_private_library_roundtrip(self):
        fixtures.put(self.layout.pi / 'settings.json', '{"enableSkillCommands":false,"other":42}\n')
        settings = (self.layout.pi / 'settings.json').read_bytes()
        self.run_cli('--only', 'pi')
        state = self.manifest()
        self.assertEqual(set(state['harnesses']), {'pi'})
        record = state['harnesses']['pi']
        self.assertEqual(record['configs'], {})
        bundle = self.layout.store / 'bundles' / record['bundle']
        self.assertTrue((bundle / 'skills/executing-plans/scripts/task-start').is_file())
        for name in record['entries']:
            entry = self.layout.entry('pi', name)
            self.assertEqual(entry, self.home / '.pi/agent/skills' / name)
            body = (entry / 'SKILL.md').read_text()
            self.assertIn('disable-model-invocation: true', body)
            self.assertIn('/tools/pi.md', body)
            self.assertIn('authorizes chaining', body)
        notes = (bundle / 'tools/pi.md').read_text()
        self.assertIn('no built-in subagents', notes)
        self.assertIn('stop at that step', notes)
        self.assertIn('never substitute a different model', notes)
        before = self.snapshot(self.home)
        self.run_cli('--only', 'pi')
        self.assertEqual(before, self.snapshot(self.home))
        self.run_cli('--only', 'pi', '--uninstall', '--prune')
        self.assertEqual((self.layout.pi / 'settings.json').read_bytes(), settings)
        self.assertFalse((self.layout.pi / 'AGENTS.md').exists())
        self.assertFalse(bundle.exists())
        self.assertFalse((self.layout.pi / 'extensions').exists())

    def test_environment_override_and_isolated_home(self):
        custom = self.base / 'custom pi'
        with patch.dict(os.environ, {'PI_CODING_AGENT_DIR': str(custom)}), patch.object(Path, 'home', return_value=self.home):
            layout = lite.Layout()
            self.assertEqual(layout.pi, custom)
            self.assertEqual(lite.Layout(str(self.home)).pi, self.home / '.pi/agent')
            argv = ['--source', str(self.source), '--only', 'pi']
            with contextlib.redirect_stdout(io.StringIO()):
                lite.main(argv)
                self.assertTrue((custom / 'AGENTS.md').is_file())
                lite.main(argv + ['--uninstall'])
            self.assertFalse((custom / 'AGENTS.md').exists())

    def test_detection_from_cli_or_agent_directory(self):
        with patch.object(lite.shutil, 'which', side_effect=lambda h: '/bin/pi' if h == 'pi' else None):
            self.assertEqual(lite.detected(self.layout), {'pi'})
        self.layout.pi.mkdir(parents=True)
        with patch.object(lite.shutil, 'which', return_value=None):
            self.assertEqual(lite.detected(self.layout), {'pi'})

    def test_effective_global_file_is_preserved_including_empty_file(self):
        # Each case uses the first existing Pi candidate, even if it is empty.
        for name in ('CLAUDE.md', 'CLAUDE.MD', 'AGENTS.MD', 'AGENTS.md'):
            with self.subTest(name=name):
                path = self.layout.pi / name
                original = b'' if name == 'AGENTS.md' else b'\xef\xbb\xbf# User\r\nRetain me.'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(original)
                self.run_cli('--only', 'pi')
                self.assertEqual(Path(self.manifest()['harnesses']['pi']['instruction']['path']), path)
                self.assertTrue(path.read_bytes().startswith(original))
                self.assertIn(b'workflow-pi:begin', path.read_bytes())
                self.run_cli('--only', 'pi', '--uninstall')
                self.assertEqual(path.read_bytes(), original)
                path.unlink()

    def test_precedence_changes_move_only_owned_block(self):
        fallback = self.layout.pi / 'CLAUDE.md'
        primary = self.layout.pi / 'AGENTS.md'
        fixtures.put(fallback, 'Fallback\n')
        self.run_cli('--only', 'pi')
        fixtures.put(primary, 'Primary\n')
        self.run_cli('--only', 'pi')
        self.assertEqual(fallback.read_text(), 'Fallback\n')
        self.assertIn('workflow-pi:begin', primary.read_text())
        self.run_cli('--only', 'pi', '--uninstall')
        self.assertEqual(primary.read_text(), 'Primary\n')

    def test_other_harness_update_keeps_pi_and_pi_uninstall_keeps_others(self):
        self.run_cli()
        before = self.snapshot(self.layout.pi)
        self.run_cli('--only', 'codex', '--skills', 'executing-plans')
        self.assertEqual(before, self.snapshot(self.layout.pi))
        others = {h: self.snapshot(getattr(self.layout, h)) for h in ('codex', 'opencode', 'claude')}
        self.run_cli('--only', 'pi', '--uninstall', '--prune')
        for h, content in others.items():
            self.assertEqual(content, self.snapshot(getattr(self.layout, h)))

    def test_modified_pi_entry_and_instruction_are_protected(self):
        self.run_cli('--only', 'pi')
        for path in (self.layout.entry('pi', 'superpowers-brainstorming') / 'SKILL.md', self.layout.pi / 'AGENTS.md'):
            original = path.read_bytes()
            path.write_bytes(original.replace(b'workflow', b'edited workflow'))
            before = self.snapshot(self.home)
            with self.assertRaises(lite.SetupError):
                self.run_cli('--only', 'pi', '--uninstall')
            self.assertEqual(before, self.snapshot(self.home))
            path.write_bytes(original)

    def test_known_full_pi_extension_package_and_bootstrap_conflicts(self):
        for path, content in (
                (self.layout.pi / 'extensions/superpowers.ts', '// extension'),
                (self.layout.pi / 'settings.json', json.dumps({'packages': ['npm:superpowers-pi']})),
                (self.layout.pi / 'CLAUDE.md', 'Load using-superpowers now.'),
                (self.layout.pi / 'skills/superpowers/README.md', '# Full library')):
            fixtures.put(path, content)
            before = self.snapshot(self.home)
            with self.assertRaisesRegex(lite.SetupError, 'full bootstrap/plugin'):
                self.run_cli('--only', 'pi')
            self.assertEqual(before, self.snapshot(self.home))
            path.unlink()
            if path.name == 'README.md':
                path.parent.rmdir()

    def test_recursive_declared_name_and_project_collisions(self):
        name = 'superpowers-brainstorming'
        for root in (self.layout.pi / 'skills', self.home / '.agents/skills', self.base / 'project/.pi/skills'):
            path = root / 'collection/other-name/SKILL.md'
            fixtures.put(path, f'---\nname: "{name}"\ndescription: Existing skill\n---\nBody\n')
            with patch.object(Path, 'cwd', return_value=self.base / 'project'):
                layout = self.layout
                layout.isolated = False
                with self.assertRaisesRegex(lite.SetupError, 'Conflicting discoverable skill'):
                    lite.check_collision(layout, 'pi', name, layout.entry('pi', name), [])
                layout.isolated = True
            path.unlink()

    def test_skill_at_global_root_would_hide_nested_entries(self):
        fixtures.put(self.layout.pi / 'skills/SKILL.md',
                     '---\nname: collection\ndescription: Existing root skill\n---\nBody\n')
        before = self.snapshot(self.home)
        with self.assertRaisesRegex(lite.SetupError, 'prevents discovery'):
            self.run_cli('--only', 'pi')
        self.assertEqual(before, self.snapshot(self.home))

    def test_recursive_scan_follows_links_without_looping(self):
        root = self.layout.pi / 'skills'
        external = self.base / 'linked collection'
        fixtures.put(external / 'nested/SKILL.md',
                     '---\nname: superpowers-brainstorming\ndescription: Existing\n---\nBody\n')
        root.mkdir(parents=True)
        try:
            (root / 'alias').symlink_to(external, target_is_directory=True)
            (external / 'loop').symlink_to(root, target_is_directory=True)
        except OSError:
            self.skipTest('Directory symlink privilege unavailable')
        with self.assertRaisesRegex(lite.SetupError, 'Conflicting discoverable skill'):
            self.run_cli('--only', 'pi')


if __name__ == '__main__':
    unittest.main()
