"""Antigravity audit, discovery, and ownership regressions in temporary homes."""
import contextlib
import io
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_superpowers_lite as fixtures
from lite_test_support import lite


class AntigravityTests(unittest.TestCase):
    setUp = fixtures.InstallerTests.setUp
    run_cli = fixtures.InstallerTests.run_cli
    manifest = fixtures.InstallerTests.manifest
    snapshot = fixtures.InstallerTests.snapshot

    def test_global_bootstrap_rules_block_audit_and_install_without_writes(self):
        for relative in ('.gemini/AGENTS.md', '.gemini/GEMINI.md',
                         '.gemini/config/AGENTS.md', '.gemini/config/GEMINI.md',
                         '.gemini/config/rules/bootstrap.md',
                         '.gemini/antigravity-cli/rules/bootstrap.md'):
            with self.subTest(relative=relative):
                path = self.home / relative
                fixtures.put(path, 'Always load using-superpowers before every task.\n')
                before = self.snapshot(self.home)
                self.assertEqual(self.run_cli('--only', 'antigravity', '--audit'), 1)
                with self.assertRaisesRegex(lite.SetupError, 'full bootstrap/plugin'):
                    self.run_cli('--only', 'antigravity')
                self.assertEqual(before, self.snapshot(self.home))
                path.unlink()

    def test_project_and_ancestor_bootstrap_rules_are_audited(self):
        project = self.base / 'project'
        cwd = project / 'nested'
        paths = [project / 'GEMINI.md', cwd / '.agents/GEMINI.md',
                 project / '.agent/AGENTS.md', project / '_agents/rules/bootstrap.md']
        self.layout.isolated = False
        with patch.object(Path, 'cwd', return_value=cwd):
            for path in paths:
                with self.subTest(path=path):
                    fixtures.put(path, 'Load using-superpowers.\n')
                    self.assertTrue(any(str(path) in issue for issue in
                                        lite.plugin_conflicts(self.layout, {'antigravity'})))
                    path.unlink()

    def test_other_harness_audit_does_not_scan_antigravity_global_rules(self):
        fixtures.put(self.home / '.gemini/GEMINI.md', 'Load using-superpowers.\n')
        self.assertEqual(lite.plugin_conflicts(self.layout, {'pi'}), [])

    def test_instruction_lifecycle_preserves_all_existing_global_rules(self):
        originals = {}
        for relative in ('.gemini/AGENTS.md', '.gemini/GEMINI.md',
                         '.gemini/config/AGENTS.md', '.gemini/config/GEMINI.md'):
            path = self.home / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'\xef\xbb\xbf# User rules\r\nPreserve these bytes.')
            originals[path] = path.read_bytes()
        self.run_cli('--only', 'antigravity')
        managed = self.layout.antigravity / 'AGENTS.md'
        for path, content in originals.items():
            if path == managed:
                self.assertTrue(path.read_bytes().startswith(content))
                self.assertIn(b'workflow-antigravity:begin', path.read_bytes())
            else:
                self.assertEqual(path.read_bytes(), content)
        before = self.snapshot(self.home)
        self.run_cli('--only', 'antigravity')
        self.assertEqual(before, self.snapshot(self.home))
        self.run_cli('--only', 'antigravity', '--uninstall')
        for path, content in originals.items():
            self.assertEqual(path.read_bytes(), content)

    def test_new_bootstrap_does_not_prevent_owned_uninstall(self):
        self.run_cli('--only', 'antigravity')
        path = self.home / '.gemini/GEMINI.md'
        fixtures.put(path, 'Load using-superpowers.\n')
        self.run_cli('--only', 'antigravity', '--uninstall')
        self.assertEqual(path.read_text(), 'Load using-superpowers.\n')
        self.assertFalse((self.layout.antigravity / 'AGENTS.md').exists())

    def test_same_name_in_each_global_skill_root_blocks_install_without_writes(self):
        for relative in ('.gemini/config/skills', '.gemini/antigravity-cli/skills',
                         '.gemini/skills', '.agents/skills'):
            with self.subTest(relative=relative):
                path = self.home / relative / 'superpowers-brainstorming/SKILL.md'
                fixtures.put(path, '---\nname: superpowers-brainstorming\ndescription: Existing\n---\nOriginal\n')
                before = self.snapshot(self.home)
                with self.assertRaisesRegex(lite.SetupError, 'unowned entry|Conflicting discoverable skill'):
                    self.run_cli('--only', 'antigravity')
                self.assertEqual(before, self.snapshot(self.home))
                path.unlink()
                path.parent.rmdir()

    def test_each_project_customization_root_is_checked(self):
        project = self.base / 'project'
        name = 'superpowers-brainstorming'
        self.layout.isolated = False
        with patch.object(Path, 'cwd', return_value=project / 'nested'):
            for directory in ('.agents', '.agent', '_agents', '_agent'):
                path = project / directory / 'skills' / name / 'SKILL.md'
                fixtures.put(path, 'Existing skill\n')
                with self.assertRaisesRegex(lite.SetupError, 'Conflicting discoverable skill'):
                    lite.check_collision(self.layout, 'antigravity', name,
                                         self.layout.entry('antigravity', name), [])
                path.unlink()
                path.parent.rmdir()

    def test_full_plugins_and_bootstrap_skills_in_global_roots_are_rejected(self):
        for directory in ('.gemini/config', '.gemini/antigravity-cli', '.gemini'):
            for relative in ('plugins/superpowers/plugin.json', 'skills/superpowers/SKILL.md',
                             'skills/using-superpowers/SKILL.md'):
                with self.subTest(directory=directory, relative=relative):
                    path = self.home / directory / relative
                    fixtures.put(path, '{}\n')
                    before = self.snapshot(self.home)
                    with self.assertRaisesRegex(lite.SetupError, 'full bootstrap/plugin'):
                        self.run_cli('--only', 'antigravity')
                    self.assertEqual(before, self.snapshot(self.home))
                    path.unlink()
                    path.parent.rmdir()

    def test_unrelated_skills_and_plugins_are_preserved(self):
        paths = []
        for directory in ('.gemini/config', '.gemini/antigravity-cli', '.gemini'):
            for relative in ('skills/unrelated/SKILL.md', 'plugins/unrelated/plugin.json'):
                path = self.home / directory / relative
                fixtures.put(path, 'Unrelated user content\n')
                paths.append(path)
        self.run_cli('--only', 'antigravity')
        before = self.snapshot(self.home)
        self.run_cli('--only', 'antigravity')
        self.assertEqual(before, self.snapshot(self.home))
        self.run_cli('--only', 'antigravity', '--uninstall', '--prune')
        for path in paths:
            self.assertEqual(path.read_text(), 'Unrelated user content\n')

    def test_unsupported_override_is_ignored_for_install_audit_and_uninstall(self):
        custom = self.base / 'unsupported override'
        argv = ['--source', str(self.source), '--only', 'antigravity']
        with patch.dict(os.environ, {'ANTIGRAVITY_CONFIG_DIR': str(custom),
                                     'XDG_DATA_HOME': str(self.home / '.local/share')}), \
                patch.object(Path, 'home', return_value=self.home):
            layout = lite.Layout()
            self.assertEqual(layout.antigravity, self.home / '.gemini/config')
            self.assertEqual(lite.Layout(str(self.home)).antigravity, layout.antigravity)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(lite.main(argv + ['--audit']), 0)
                lite.main(argv)
                self.assertTrue(layout.entry('antigravity', 'superpowers-brainstorming').is_dir())
                lite.main(argv + ['--uninstall'])
            self.assertIn('Ignoring ANTIGRAVITY_CONFIG_DIR', output.getvalue())
            self.assertFalse(custom.exists())
            self.assertFalse(layout.entry('antigravity', 'superpowers-brainstorming').exists())
            bootstrap = self.home / '.gemini/GEMINI.md'
            fixtures.put(bootstrap, 'Load using-superpowers.\n')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(lite.main(argv + ['--audit']), 1)

    def test_existing_unsupported_path_manifest_is_not_silently_retargeted(self):
        # Reproduce a manifest made by the original adapter's unsupported env
        # override. A normal update must preserve it for explicit reconciliation.
        old_layout = lite.Layout(str(self.home))
        old_layout.antigravity = self.base / 'old custom profile'
        with patch.object(lite, 'Layout', return_value=old_layout):
            self.run_cli('--only', 'antigravity')
        before = self.snapshot(self.base)
        for extra in ((), ('--uninstall',)):
            with self.assertRaisesRegex(lite.SetupError, 'Installation paths changed'):
                self.run_cli('--only', 'antigravity', *extra)
            self.assertEqual(before, self.snapshot(self.base))

    def test_normal_detection_uses_agy_and_ignores_override_directory(self):
        custom = self.base / 'unsupported override'
        custom.mkdir()
        with patch.dict(os.environ, {'ANTIGRAVITY_CONFIG_DIR': str(custom)}), \
                patch.object(Path, 'home', return_value=self.home), \
                patch.object(lite.shutil, 'which', return_value=None):
            self.assertNotIn('antigravity', lite.detected(lite.Layout()))
        with patch.object(lite.shutil, 'which', side_effect=lambda h: '/bin/agy' if h == 'agy' else None):
            self.assertIn('antigravity', lite.detected(self.layout))

    def test_manual_entry_and_modified_file_protection(self):
        self.run_cli('--only', 'antigravity')
        state = self.manifest()['harnesses']['antigravity']
        self.assertEqual(state['configs'], {})
        entry = self.layout.entry('antigravity', 'superpowers-brainstorming') / 'SKILL.md'
        body = entry.read_text()
        self.assertIn('disable-model-invocation: true', body)
        self.assertIn('/tools/antigravity.md', body)
        self.assertIn('authorizes chaining', body)
        entry.write_text(body + '\nUser addition\n')
        before = self.snapshot(self.home)
        with self.assertRaisesRegex(lite.SetupError, 'User-modified owned entry'):
            self.run_cli('--only', 'antigravity', '--uninstall')
        self.assertEqual(before, self.snapshot(self.home))


if __name__ == '__main__':
    unittest.main()
