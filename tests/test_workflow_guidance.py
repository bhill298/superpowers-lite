"""Managed instruction blocks: preserve user text and lifecycle ownership."""
import contextlib
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_superpowers_lite as fixtures
from lite_test_support import lite


class InstructionTests(unittest.TestCase):
    setUp = fixtures.InstallerTests.setUp
    run_cli = fixtures.InstallerTests.run_cli
    manifest = fixtures.InstallerTests.manifest
    prepare = fixtures.InstallerTests.prepare
    snapshot = fixtures.InstallerTests.snapshot

    def test_install_roundtrip_preserves_bytes_bom_crlf_and_no_final_newline(self):
        originals = {}
        for h, content in zip(lite.HARNESSES, (b'\xef\xbb\xbf# User\r\nKeep this.\r\n', b'# User\nNo final newline', b'', b'# Pi\r\n')):
            path = self.layout.instruction_paths(h)[0]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            originals[path] = content
        self.run_cli()
        for path, content in originals.items():
            self.assertTrue(path.read_bytes().startswith(content))
            self.assertIn(b'only after the user explicitly starts', path.read_bytes())
        self.run_cli('--uninstall')
        for path, content in originals.items():
            self.assertEqual(path.read_bytes(), content)

    def test_owned_files_removed_and_later_user_text_preserved(self):
        self.run_cli()
        path = self.layout.codex / 'AGENTS.md'
        path.write_bytes(path.read_bytes() + b'# User addition\nKeep me.\n')
        self.run_cli('--uninstall')
        self.assertEqual(path.read_bytes(), b'# User addition\nKeep me.\n')
        for h in ('claude', 'opencode'):
            self.assertFalse(self.layout.instruction_paths(h)[0].exists())

    def test_update_in_place_preserves_surrounding_user_text(self):
        path = self.layout.codex / 'AGENTS.md'
        fixtures.put(path, '# Before\n')
        self.run_cli('--only', 'codex')
        path.write_bytes(path.read_bytes() + b'# After\n')
        old = path.read_bytes()
        with patch.object(lite, 'WORKFLOW_GUIDANCE', lite.WORKFLOW_GUIDANCE + '\nExtra reminder.\n'):
            self.run_cli('--only', 'codex')
        self.assertEqual(path.read_bytes().replace(b'\n\nExtra reminder.', b''), old)
        self.run_cli('--uninstall', '--only', 'codex')
        self.assertTrue(path.read_bytes().startswith(b'# Before\n'))
        self.assertTrue(path.read_bytes().endswith(b'# After\n'))

    def test_user_modified_block_blocks_update_and_uninstall(self):
        self.run_cli('--only', 'codex')
        path = self.layout.codex / 'AGENTS.md'
        path.write_bytes(path.read_bytes().replace(b'short checklist', b'different checklist'))
        before = self.snapshot(self.home)
        for extra in ((), ('--uninstall',)):
            with self.assertRaisesRegex(lite.SetupError, 'User-modified workflow block'):
                self.run_cli('--only', 'codex', *extra)
            self.assertEqual(before, self.snapshot(self.home))

    def test_uninstall_does_not_join_surrounding_user_lines(self):
        path = self.layout.codex / 'AGENTS.md'
        fixtures.put(path, 'Before without newline')
        self.run_cli('--only', 'codex')
        path.write_bytes(path.read_bytes() + b'After\n')
        self.run_cli('--uninstall', '--only', 'codex')
        self.assertEqual(path.read_bytes(), b'Before without newline\nAfter\n')

    def test_unowned_duplicate_and_missing_markers_refused(self):
        path = self.layout.codex / 'AGENTS.md'
        fixtures.put(path, '<!-- superpowers-lite:workflow-codex:begin -->\ntext\n<!-- superpowers-lite:workflow-codex:end -->\n')
        with self.assertRaisesRegex(lite.SetupError, 'Unowned workflow block'):
            self.run_cli('--only', 'codex')
        path.unlink()
        self.run_cli('--only', 'codex')
        original = path.read_bytes()
        for content, error in ((original + original, 'Malformed/duplicate'),
                               (original.replace(b':end -->', b':broken -->'), 'Malformed/duplicate'),
                               (b'# User text\n', 'Missing owned')):
            path.write_bytes(content)
            with self.assertRaisesRegex(lite.SetupError, error):
                self.run_cli('--only', 'codex')
            self.assertEqual(path.read_bytes(), content)

    def test_opt_out_persists_and_explicit_enable_restores_block(self):
        self.run_cli('--only', 'codex')
        self.run_cli('--only', 'codex', '--no-workflow-guidance')
        path = self.layout.codex / 'AGENTS.md'
        self.assertFalse(path.exists())
        self.run_cli('--only', 'codex')
        self.assertFalse(path.exists())
        self.run_cli('--only', 'codex', '--workflow-guidance')
        self.assertIn('workflow-codex:begin', path.read_text())

    def test_codex_effective_override_and_path_change(self):
        normal = self.layout.codex / 'AGENTS.md'
        override = self.layout.codex / 'AGENTS.override.md'
        fixtures.put(normal, '# Normal\n')
        fixtures.put(override, '\n')
        self.run_cli('--only', 'codex')
        self.assertIn('workflow-codex:begin', normal.read_text())
        self.assertEqual(override.read_text(), '\n')
        fixtures.put(override, '# Override\n')
        self.run_cli('--only', 'codex')
        self.assertEqual(normal.read_text(), '# Normal\n')
        self.assertIn('workflow-codex:begin', override.read_text())
        self.run_cli('--uninstall', '--only', 'codex')
        self.assertEqual(override.read_text(), '# Override\n')

    def test_legacy_cleanup_and_new_block_do_not_overlap_writes(self):
        path = self.layout.codex / 'AGENTS.md'
        fixtures.put(path, '# User\n<!-- superpowers-lite:codex:begin -->\nusing-superpowers\n<!-- superpowers-lite:codex:end -->\n')
        self.run_cli('--only', 'codex')
        self.assertNotIn('using-superpowers', path.read_text())
        self.assertEqual(path.read_text().count('workflow-codex:begin'), 1)
        self.assertEqual(lite.plugin_conflicts(self.layout, {'codex'}), [])
        self.run_cli('--only', 'codex')

    def test_new_guidance_does_not_hide_unmarked_bootstrap(self):
        self.run_cli('--only', 'codex')
        path = self.layout.codex / 'AGENTS.md'
        path.write_bytes(path.read_bytes() + b'Always load using-superpowers.\n')
        with self.assertRaisesRegex(lite.SetupError, 'bootstrap/plugin'):
            self.run_cli('--only', 'codex')

    def test_old_schema_upgrade_and_owned_file_removal(self):
        self.run_cli()
        state = self.manifest()
        for h, record in state['harnesses'].items():
            Path(record.pop('instruction')['path']).unlink()
        state['schema'] = 2
        self.layout.manifest.write_bytes(lite.json_bytes(state))
        self.run_cli()
        self.assertEqual(self.manifest()['schema'], lite.SCHEMA)
        self.run_cli('--uninstall')
        self.assertFalse((self.layout.codex / 'AGENTS.md').exists())

    def test_v1_fallback_preserved_repeat_and_uninstall(self):
        fallback = self.layout.claude / 'CLAUDE.md'
        fixtures.put(fallback, '# Existing shared rules\n')
        cfg = self.layout.config('opencode')
        fixtures.put(cfg, '{"instructions":["existing.md"]}')
        with patch.dict(os.environ, {'OPENCODE_DISABLE_CLAUDE_CODE': '', 'OPENCODE_DISABLE_CLAUDE_CODE_PROMPT': ''}):
            self.run_cli('--only', 'opencode')
            expected = ['existing.md', fallback.as_posix()]
            self.assertEqual(json.loads(cfg.read_text())['instructions'], expected)
            before = self.snapshot(self.home)
            self.run_cli('--only', 'opencode')
            self.assertEqual(self.snapshot(self.home), before)
            self.run_cli('--uninstall', '--only', 'opencode')
        self.assertEqual(json.loads(cfg.read_text())['instructions'], ['existing.md'])
        self.assertEqual(fallback.read_text(), '# Existing shared rules\n')
        self.assertFalse((self.layout.opencode / 'AGENTS.md').exists())

    def test_fallback_not_added_when_previously_shadowed_or_disabled_or_v2(self):
        fixtures.put(self.layout.claude / 'CLAUDE.md', '# Claude\n')
        for mode in ('existing', 'disabled', 'v2'):
            with self.subTest(mode=mode):
                path = self.layout.opencode / 'AGENTS.md'
                if mode == 'existing':
                    fixtures.put(path, '# OpenCode\n')
                with patch.dict(os.environ, {'OPENCODE_DISABLE_CLAUDE_CODE_PROMPT': '1' if mode == 'disabled' else ''}):
                    self.run_cli('--only', 'opencode', '--opencode-version', '2' if mode == 'v2' else '1')
                cfg = self.layout.config('opencode')
                self.assertNotIn('instructions', json.loads(cfg.read_text()) if cfg.exists() else {})
                self.run_cli('--uninstall', '--only', 'opencode')
                path.unlink(missing_ok=True)

    def test_fallback_bootstrap_rejected_and_custom_claude_path_not_imported(self):
        fallback = self.home / '.claude' / 'CLAUDE.md'
        fixtures.put(fallback, 'Run using-superpowers.\n')
        with self.assertRaisesRegex(lite.SetupError, 'bootstrap in OpenCode instruction fallback'):
            self.run_cli('--only', 'opencode')
        fixtures.put(fallback, '# Actual fallback\n')
        self.layout.claude = self.home / 'custom-claude'
        fixtures.put(self.layout.claude / 'CLAUDE.md', '# Unrelated custom Claude config\n')
        args = lite.parser().parse_args(self.args + ['--only', 'opencode'])
        staging = self.base / 'staging'
        staging.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            plan, _, _ = lite.prepare(args, self.layout, staging)
            plan.apply()
        self.assertEqual(json.loads(self.layout.config('opencode').read_text())['instructions'], [fallback.as_posix()])

    def test_instruction_race_and_transaction_rollback(self):
        path = self.layout.codex / 'AGENTS.md'
        fixtures.put(path, '# Original\n')
        plan, _, _ = self.prepare('--only', 'codex')
        fixtures.put(path, '# Concurrent edit\n')
        with self.assertRaisesRegex(lite.SetupError, 'Changed since planning'):
            with contextlib.redirect_stdout(io.StringIO()):
                plan.apply()
        self.assertEqual(path.read_text(), '# Concurrent edit\n')
        plan, _, _ = self.prepare('--only', 'codex')
        count = list(plan.changes).index(path.absolute()) + 1
        with self.assertRaisesRegex(OSError, 'Injected'):
            with contextlib.redirect_stdout(io.StringIO()):
                plan.apply(fail_after=count)
        self.assertEqual(path.read_text(), '# Concurrent edit\n')
        self.assertFalse(self.layout.manifest.exists())

    def test_edit_during_planning_is_not_overwritten(self):
        path = self.layout.codex / 'AGENTS.md'
        original_update = lite.InstructionEdit.update
        def concurrent_edit(edit, *args):
            result = original_update(edit, *args)
            fixtures.put(path, '# Concurrent user instructions\n')
            return result
        for existing in (False, True):
            if existing:
                fixtures.put(path, '# Original\n')
            with patch.object(lite.InstructionEdit, 'update', concurrent_edit):
                with self.assertRaisesRegex(lite.SetupError, 'Changed since reading'):
                    self.prepare('--only', 'codex')
            self.assertEqual(path.read_text(), '# Concurrent user instructions\n')
            self.assertFalse(self.layout.manifest.exists())
            path.unlink()

    def test_symlink_target_preserved_and_retarget_refused(self):
        path = self.layout.codex / 'AGENTS.md'
        target = self.base / 'shared.md'
        fixtures.put(target, '# Shared\n')
        path.parent.mkdir(parents=True)
        try:
            path.symlink_to(target)
        except OSError:
            self.skipTest('File symlink privilege unavailable')
        self.run_cli('--only', 'codex')
        self.assertTrue(path.is_symlink())
        other = self.base / 'other.md'
        fixtures.put(other, '# Other\n')
        path.unlink()
        path.symlink_to(other)
        with self.assertRaisesRegex(lite.SetupError, 'link target changed'):
            self.run_cli('--uninstall', '--only', 'codex')
        self.assertEqual(other.read_text(), '# Other\n')
        path.unlink()
        path.symlink_to(target)
        self.run_cli('--uninstall', '--only', 'codex')
        self.assertTrue(path.is_symlink())
        self.assertEqual(target.read_text(), '# Shared\n')

    def test_malformed_instruction_manifest_refused(self):
        self.run_cli('--only', 'codex')
        state = self.manifest()
        state['harnesses']['codex']['instruction']['prefix'] = 'user text'
        self.layout.manifest.write_bytes(lite.json_bytes(state))
        before = self.snapshot(self.home)
        with self.assertRaisesRegex(lite.SetupError, 'instruction ownership'):
            self.run_cli('--uninstall', '--only', 'codex')
        self.assertEqual(before, self.snapshot(self.home))

    def test_two_harnesses_sharing_symlink_target_uninstall_independently(self):
        shared = self.base / 'shared-instructions.md'
        fixtures.put(shared, '# Shared user instructions\n')
        for h in ('codex', 'opencode'):
            path = self.layout.instruction_paths(h)[0]
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                path.symlink_to(shared)
            except OSError:
                self.skipTest('File symlink privilege unavailable')
        self.run_cli('--only', 'codex,opencode')
        self.run_cli('--only', 'codex,opencode')
        self.run_cli('--uninstall', '--only', 'codex')
        self.assertNotIn('workflow-codex', shared.read_text())
        self.assertIn('workflow-opencode', shared.read_text())
        self.run_cli('--uninstall', '--only', 'opencode')
        self.assertEqual(shared.read_text(), '# Shared user instructions\n')


if __name__ == '__main__':
    unittest.main()
