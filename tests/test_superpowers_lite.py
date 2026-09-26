"""Regression/integration tests; every write is inside a fresh temporary home."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lite_test_support import lite


def put(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8', newline='\n')


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='lite-tests-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base / 'home with spaces'
        self.source = self.base / 'upstream'
        self.layout = lite.Layout(str(self.home))
        for name in ['brainstorming', 'writing-plans', 'subagent-driven-development', 'executing-plans', 'writing-skills', 'verification-before-completion', 'using-superpowers', 'diagnosing-superpowers']:
            put(self.source / 'skills' / name / 'SKILL.md', f'---\nname: {name}\ndescription: Example\n---\n# {name}\nFollow this workflow.\n')
        put(self.source / 'skills' / 'using-superpowers' / 'references' / 'codex-tools.md', '# Tools\n')
        put(self.source / 'skills' / 'executing-plans' / 'scripts' / 'task-start', '#!/usr/bin/env bash\r\nset -eu\r\n"$(dirname "$0")/../../subagent-driven-development/scripts/task-brief"\r\n')
        put(self.source / 'skills' / 'subagent-driven-development' / 'scripts' / 'task-brief', '#!/usr/bin/env bash\r\necho helper-ok\r\n')
        self.args = ['--home', str(self.home), '--source', str(self.source), '--only', 'codex,opencode,claude', '--opencode-version', '1']

    def run_cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return lite.main(self.args + list(args))

    def manifest(self):
        return json.loads(self.layout.manifest.read_text())

    def prepare(self, *extra):
        args = lite.parser().parse_args(self.args + list(extra))
        staging = Path(tempfile.mkdtemp(dir=self.base))
        with contextlib.redirect_stdout(io.StringIO()):
            return lite.prepare(args, self.layout, staging)

    def snapshot(self, root):
        if not root.exists():
            return {}
        return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}

    def test_install_separate_discovery_roots_and_three_entries(self):
        self.run_cli()
        state = self.manifest()
        self.assertEqual(set(state['harnesses']), set(lite.HARNESSES))
        self.assertFalse((self.home / '.agents' / 'skills').exists())
        self.assertFalse((self.layout.claude / 'skills').exists())
        for h, record in state['harnesses'].items():
            self.assertEqual(len(record['entries']), 3)
            for name, entry in record['entries'].items():
                self.assertEqual(lite.fingerprint(Path(entry['path'])), entry['digest'])
        cmd = self.layout.entry('claude', 'superpowers-brainstorming').read_text()
        self.assertIn('disable-model-invocation: true', cmd)
        self.assertIn('authorizes chaining', cmd)
        self.assertNotIn('\nname:', cmd)
        y = self.layout.entry('codex', 'superpowers-brainstorming') / 'agents' / 'openai.yaml'
        self.assertIn('allow_implicit_invocation: false', y.read_text())
        cfg = json.loads(self.layout.config('opencode').read_text())
        self.assertEqual(set(cfg['permission']['skill'].values()), {'deny'})
        self.assertFalse(self.layout.config('codex').exists())

    def test_repeat_is_noop_and_keeps_config_ownership(self):
        self.run_cli()
        before = self.snapshot(self.home)
        self.run_cli()
        self.assertEqual(before, self.snapshot(self.home))
        self.run_cli('--uninstall', '--only', 'opencode')
        self.assertFalse(self.layout.config('opencode').exists())

    def test_dry_run_no_home_writes(self):
        self.run_cli('--dry-run')
        self.assertFalse(self.home.exists())

    def test_lock_covers_manifest_read_and_source_download(self):
        original = lite.fetch_source
        def competing_install(*args):
            with self.assertRaisesRegex(lite.SetupError, 'Install lock exists'):
                self.run_cli('--only', 'claude')
            return original(*args)
        original_load = lite.load_manifest
        def read_locked(layout):
            self.assertTrue((layout.store / 'install.lock').exists())
            return original_load(layout)
        with patch.object(lite, 'fetch_source', side_effect=competing_install), patch.object(lite, 'load_manifest', side_effect=read_locked):
            self.run_cli('--only', 'codex')
        self.run_cli('--only', 'claude')
        self.assertEqual(set(self.manifest()['harnesses']), {'codex', 'claude'})

    def test_failed_planning_releases_lock_and_empty_directories(self):
        with self.assertRaises(lite.SetupError):
            self.run_cli('--skills', 'nonexistent')
        self.assertFalse(self.home.exists())
        self.run_cli()

    def test_private_paths_and_bootstrap_omission(self):
        self.run_cli('--skills', 'brainstorming')
        state = self.manifest()
        record = state['harnesses']['codex']
        self.assertEqual(len(record['entries']), 1)
        bundle = self.layout.store / 'bundles' / record['bundle']
        self.assertTrue((bundle / 'skills' / 'writing-skills' / 'SKILL.md').exists())
        self.assertFalse((bundle / 'skills' / 'using-superpowers' / 'SKILL.md').exists())
        self.assertFalse((bundle / 'skills' / 'diagnosing-superpowers').exists())
        self.assertTrue((bundle / 'skills' / 'using-superpowers' / 'references' / 'codex-tools.md').exists())
        script = bundle / 'skills' / 'executing-plans' / 'scripts' / 'task-start'
        self.assertNotIn(b'\r', script.read_bytes())
        self.assertIn('../../subagent-driven-development/scripts', script.read_text())
        if os.name != 'nt':
            self.assertTrue(script.stat().st_mode & stat.S_IXUSR)
            result = subprocess.run([str(script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'helper-ok')

    def test_only_codex_preserves_other_harnesses(self):
        self.run_cli()
        before = self.manifest()
        claude = self.snapshot(self.layout.claude)
        opencode = self.snapshot(self.layout.opencode)
        put(self.layout.claude / 'CLAUDE.md', 'unchanged\n<!-- superpowers-lite:old:begin -->\nlegacy\n<!-- superpowers-lite:old:end -->\n')
        note = (self.layout.claude / 'CLAUDE.md').read_bytes()
        self.run_cli('--only', 'codex', '--skills', 'verification-before-completion')
        after = self.manifest()
        for h in ['claude', 'opencode']:
            self.assertEqual(before['harnesses'][h], after['harnesses'][h])
        self.assertEqual(opencode, self.snapshot(self.layout.opencode))
        self.assertEqual(note, (self.layout.claude / 'CLAUDE.md').read_bytes())
        for name, data in claude.items():
            self.assertEqual(data, (self.layout.claude / name).read_bytes())

    def test_empty_selection_rejected_without_changes(self):
        self.run_cli()
        before = self.snapshot(self.home)
        with self.assertRaisesRegex(lite.SetupError, 'cannot be empty'):
            self.run_cli('--skills', '')
        self.assertEqual(before, self.snapshot(self.home))

    def test_bad_later_skill_rejected_before_any_write(self):
        self.run_cli()
        before = self.snapshot(self.home)
        put(self.source / 'skills' / 'writing-skills' / 'SKILL.md', 'missing header\n')
        with self.assertRaisesRegex(lite.SetupError, 'frontmatter'):
            self.run_cli()
        self.assertEqual(before, self.snapshot(self.home))

    def test_behavioral_upstream_metadata_requires_review(self):
        put(self.source / 'skills' / 'brainstorming' / 'SKILL.md', '---\nname: brainstorming\ncontext: fork\n---\nDo work\n')
        with self.assertRaisesRegex(lite.SetupError, 'unsupported upstream'):
            self.run_cli()
        self.assertFalse(self.home.exists())

    def test_inline_metadata_cannot_override_generated_policy(self):
        put(self.source / 'skills' / 'brainstorming' / 'SKILL.md', '---\nname: brainstorming\nmetadata: {opencode/autoinvoke: true}\n---\nDo work\n')
        put(self.source / 'skills' / 'brainstorming' / 'agents' / 'openai.yaml', 'policy: {allow_implicit_invocation: true}\n')
        self.run_cli('--opencode-version', '2')
        text = (self.layout.entry('opencode', 'superpowers-brainstorming') / 'SKILL.md').read_text()
        self.assertIn('opencode/autoinvoke: false', text)
        self.assertNotIn('opencode/autoinvoke: true', text)
        y = self.layout.entry('codex', 'superpowers-brainstorming') / 'agents' / 'openai.yaml'
        self.assertEqual(y.read_text().count('policy:'), 1)
        self.assertIn('allow_implicit_invocation: false', y.read_text())

    def test_jsonc_strings_preserved(self):
        cfg = self.layout.opencode / 'opencode.jsonc'
        put(cfg, '{ // comment\n "instructions":["literal,}", "literal,]", "//not-comment", "/*inside*/"],\n "permission": "ask",\n}')
        self.run_cli()
        parsed = json.loads(cfg.read_text())
        self.assertEqual(parsed['instructions'], ['literal,}', 'literal,]', '//not-comment', '/*inside*/'])
        self.assertEqual(parsed['permission']['*'], 'ask')
        self.run_cli('--uninstall', '--only', 'opencode')
        self.assertEqual(json.loads(cfg.read_text())['permission'], 'ask')

    def test_nested_skill_permission_shorthand_preserved(self):
        cfg = self.layout.config('opencode')
        put(cfg, '{"permission":{"skill":"ask","bash":"deny"}}')
        self.run_cli()
        self.assertEqual(json.loads(cfg.read_text())['permission']['skill']['*'], 'ask')
        self.run_cli('--uninstall', '--only', 'opencode')
        self.assertEqual(json.loads(cfg.read_text()), {'permission': {'skill': 'ask', 'bash': 'deny'}})

    def test_permission_order_effective_and_restored(self):
        cfg = self.layout.config('opencode')
        original = {'permission': {'skill': {'superpowers-brainstorming': 'ask', '*': 'allow'}, '*': 'allow'}}
        put(cfg, json.dumps(original))
        self.run_cli('--only', 'opencode')
        data = json.loads(cfg.read_text())
        self.assertEqual(list(data['permission'])[-1], 'skill')
        self.assertEqual(list(data['permission']['skill'])[0], '*')
        self.run_cli('--only', 'opencode')
        self.assertEqual(json.loads(cfg.read_text()), data)
        self.run_cli('--uninstall', '--only', 'opencode')
        self.assertEqual(json.dumps(json.loads(cfg.read_text())), json.dumps(original))

    def test_permission_order_only_change_and_user_addition(self):
        cfg = self.layout.config('opencode')
        put(cfg, '{"permission":{"skill":{"superpowers-brainstorming":"deny","*":"allow"}}}')
        self.run_cli('--only', 'opencode', '--skills', 'brainstorming')
        data = json.loads(cfg.read_text())
        self.assertEqual(list(data['permission']['skill']), ['*', 'superpowers-brainstorming'])
        data['permission']['skill']['other'] = 'ask'
        put(cfg, json.dumps(data))
        self.run_cli('--uninstall', '--only', 'opencode')
        self.assertEqual(list(json.loads(cfg.read_text())['permission']['skill']), ['superpowers-brainstorming', '*', 'other'])

    def test_uninstall_preserves_new_user_config_fields(self):
        self.run_cli()
        cfg = self.layout.config('opencode')
        data = json.loads(cfg.read_text())
        data['permission']['read'] = 'ask'
        data['permission']['skill']['other-skill'] = 'deny'
        put(cfg, json.dumps(data))
        self.run_cli('--uninstall', '--only', 'opencode')
        after = json.loads(cfg.read_text())
        self.assertEqual(after['permission']['skill'], {'other-skill': 'deny'})
        self.assertEqual(after['permission']['read'], 'ask')

    def test_update_removes_stale_permissions(self):
        self.run_cli()
        self.run_cli('--only', 'opencode', '--skills', 'verification-before-completion')
        cfg = json.loads(self.layout.config('opencode').read_text())
        self.assertEqual(cfg['permission']['skill'], {'superpowers-verification-before-completion': 'deny'})

    def test_v1_to_v2_releases_owned_permissions(self):
        self.run_cli()
        self.run_cli('--only', 'opencode', '--opencode-version', '2')
        self.assertFalse(self.layout.config('opencode').exists())
        self.assertIn('opencode/autoinvoke: false', (self.layout.entry('opencode', 'superpowers-brainstorming') / 'SKILL.md').read_text())

    def test_toml_layouts_and_effort_only(self):
        cfg = self.layout.config('codex')
        put(cfg, 'features.other = true\nmodel_instructions = """\n[features]\nmulti_agent = false\n"""\n[agents]\nenabled = false\n')
        original = lite.tomllib.loads(cfg.read_text())
        self.run_cli('--enable-codex-multi-agent', '--codex-subagent-effort', 'high')
        parsed = lite.tomllib.loads(cfg.read_text())
        self.assertEqual(parsed['model_instructions'], original['model_instructions'])
        self.assertTrue(parsed['features']['other'])
        self.assertTrue(parsed['features']['multi_agent'])
        self.assertTrue(parsed['agents']['enabled'])
        self.assertEqual(parsed['agents']['default_subagent_reasoning_effort'], 'high')
        self.run_cli()
        self.assertEqual(parsed, lite.tomllib.loads(cfg.read_text()))
        self.run_cli('--uninstall', '--only', 'codex')
        self.assertEqual(lite.tomllib.loads(cfg.read_text()), original)

    def test_inline_toml_preserves_original_values(self):
        cfg = self.layout.config('codex')
        put(cfg, 'features = { other = true }\nagents = { default_subagent_model = "old" }\n')
        self.run_cli('--enable-codex-multi-agent', '--codex-subagent-model', 'new')
        parsed = lite.tomllib.loads(cfg.read_text())
        self.assertEqual(parsed['agents']['default_subagent_model'], 'new')
        self.assertTrue(parsed['features']['other'])
        self.run_cli('--uninstall', '--only', 'codex')
        self.assertEqual(lite.tomllib.loads(cfg.read_text()), {'features': {'other': True}, 'agents': {'default_subagent_model': 'old'}})

    def test_symlink_config_format_survives_install_update_uninstall(self):
        cfg = self.layout.config('codex')
        target = self.base / 'dotfiles' / 'codex-config'
        put(target, 'model = "original"\n')
        cfg.parent.mkdir(parents=True)
        try:
            cfg.symlink_to(target)
        except OSError:
            self.skipTest('File symlinks require privileges on this platform')
        self.run_cli('--only', 'codex', '--enable-codex-multi-agent')
        self.assertTrue(lite.tomllib.loads(target.read_text())['features']['multi_agent'])
        self.run_cli('--only', 'codex')
        self.run_cli('--only', 'codex', '--uninstall')
        self.assertTrue(cfg.is_symlink())
        self.assertEqual(lite.tomllib.loads(target.read_text()), {'model': 'original'})

    def test_invalid_config_fails_before_writes(self):
        put(self.layout.config('opencode'), '{"permission": ["bad"]}')
        before = self.snapshot(self.home)
        with self.assertRaises(lite.SetupError):
            self.run_cli()
        self.assertEqual(before, self.snapshot(self.home))

    def test_duplicate_config_requires_explicit_selection(self):
        put(self.layout.opencode / 'opencode.json', '{}')
        put(self.layout.opencode / 'opencode.jsonc', '{}')
        with self.assertRaisesRegex(lite.SetupError, 'Both OpenCode'):
            self.run_cli()
        self.run_cli('--opencode-config', str(self.layout.opencode / 'opencode.jsonc'))
        self.assertEqual((self.layout.opencode / 'opencode.json').read_text(), '{}')

    def test_unowned_entry_is_not_overwritten(self):
        p = self.layout.entry('claude', 'superpowers-brainstorming')
        put(p, 'user command')
        with self.assertRaisesRegex(lite.SetupError, 'unowned'):
            self.run_cli()
        self.assertEqual(p.read_text(), 'user command')

    def test_modified_owned_entry_is_not_deleted(self):
        self.run_cli()
        p = self.layout.entry('claude', 'superpowers-brainstorming')
        p.write_text(p.read_text() + '\nuser edit')
        with self.assertRaisesRegex(lite.SetupError, 'User-modified'):
            self.run_cli('--uninstall')
        self.assertTrue(p.read_text().endswith('user edit'))

    def test_duplicate_discovery_root_rejected(self):
        p = self.home / '.agents' / 'skills' / 'superpowers-brainstorming' / 'SKILL.md'
        put(p, 'user skill')
        with self.assertRaisesRegex(lite.SetupError, 'Conflicting discoverable'):
            self.run_cli('--only', 'opencode')

    def test_project_and_ancestor_collisions(self):
        project = self.base / 'project'
        child = project / 'nested'
        child.mkdir(parents=True)
        name = 'superpowers-brainstorming'
        cases = [('codex', '.agents/skills/' + name + '/SKILL.md'),
                 ('codex', '.codex/skills/' + name + '/SKILL.md'),
                 ('claude', '.claude/skills/' + name + '/SKILL.md'),
                 ('claude', '.claude/commands/' + name + '.md'),
                 ('opencode', '.opencode/skills/' + name + '.md')]
        self.layout.isolated = False
        for harness, relative in cases:
            with self.subTest(harness=harness, relative=relative):
                path = project / relative
                put(path, 'unrelated user skill')
                with patch.object(Path, 'cwd', return_value=child), self.assertRaisesRegex(lite.SetupError, 'Conflicting discoverable'):
                    lite.check_collision(self.layout, harness, name, self.layout.entry(harness, name), [])
                path.unlink()
                # Remove this fixture's empty skill directory before the next case.
                if path.name == 'SKILL.md':
                    path.parent.rmdir()

    def test_isolated_home_ignores_project_collisions(self):
        project = self.base / 'project'
        name = 'superpowers-brainstorming'
        put(project / '.agents' / 'skills' / name / 'SKILL.md', 'user skill')
        with patch.object(Path, 'cwd', return_value=project):
            lite.check_collision(self.layout, 'codex', name, self.layout.entry('codex', name), [])

    def test_bootstrap_config_and_discovered_plugin_detection(self):
        for mode in ['v1', 'v2', 'file']:
            with self.subTest(mode=mode):
                if mode == 'file':
                    p = self.layout.opencode / 'plugins' / 'superpowers.js'
                    put(p, 'export default {}')
                else:
                    p = self.layout.config('opencode')
                    put(p, json.dumps({'plugin' if mode == 'v1' else 'plugins': ['superpowers@git+https://github.com/obra/superpowers.git']}))
                with self.assertRaisesRegex(lite.SetupError, 'bootstrap/plugin'):
                    self.run_cli()
                p.unlink()

    def test_legacy_migration_cleans_only_owned_paths(self):
        shared = self.home / '.agents' / 'skills' / 'superpowers-brainstorming'
        put(shared / 'SKILL.md', 'old skill')
        put(shared / lite.MARKER, json.dumps({'source': lite.REPO, 'upstream_name': 'brainstorming', 'installed': 'old'}))
        link = self.layout.claude / 'skills' / shared.name
        link.parent.mkdir(parents=True)
        try:
            link.symlink_to(shared, target_is_directory=True)
        except OSError:
            shutil.copytree(shared, link)
        unrelated = self.home / '.agents' / 'skills' / 'unrelated' / 'SKILL.md'
        put(unrelated, 'untouched')
        put(self.layout.config('opencode'), '{"permission":{"skill":{"superpowers-brainstorming":"ask","unrelated":"allow"}}}')
        put(self.home / '.profile', 'user config\n# superpowers-lite:env:begin\nexport OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1\n# superpowers-lite:env:end\n')
        with self.assertRaisesRegex(lite.SetupError, 'Legacy shared'):
            self.run_cli()
        with self.assertRaisesRegex(lite.SetupError, 'must include'):
            self.run_cli('--migrate-legacy', '--only', 'codex')
        self.run_cli('--migrate-legacy')
        self.assertFalse(lite.exists(shared))
        self.assertFalse(lite.exists(link))
        self.assertEqual(unrelated.read_text(), 'untouched')
        self.assertEqual((self.home / '.profile').read_text(), 'user config\n')
        cfg = json.loads(self.layout.config('opencode').read_text())
        self.assertEqual(cfg['permission']['skill']['unrelated'], 'allow')
        self.assertEqual(cfg['permission']['skill']['superpowers-brainstorming'], 'deny')

    def test_wrong_source_marker_is_not_owned(self):
        p = self.home / '.agents' / 'skills' / 'superpowers-brainstorming'
        put(p / 'SKILL.md', 'untouched')
        put(p / lite.MARKER, '{}')
        with self.assertRaisesRegex(lite.SetupError, 'Conflicting discoverable'):
            self.run_cli('--migrate-legacy')
        self.assertTrue(p.exists())

    def test_name_length_and_harness_typo(self):
        with self.assertRaisesRegex(lite.SetupError, 'invalid final name'):
            self.run_cli('--name-prefix', 'x' * 64 + '-')
        with self.assertRaisesRegex(lite.SetupError, '--only accepts'):
            self.run_cli('--only', 'codex,typo')
        self.assertFalse(self.home.exists())

    def test_transaction_rolls_back_every_target(self):
        self.run_cli()
        before = {h: self.snapshot(getattr(self.layout, h)) for h in lite.HARNESSES}
        manifest = self.layout.manifest.read_bytes()
        plan, _, _ = self.prepare('--skills', 'verification-before-completion')
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(OSError, 'Injected'):
            plan.apply(fail_after=4)
        for h in lite.HARNESSES:
            self.assertEqual(before[h], self.snapshot(getattr(self.layout, h)))
        self.assertEqual(manifest, self.layout.manifest.read_bytes())
        self.assertFalse((self.layout.store / 'install.lock').exists())
        self.assertFalse(list(self.home.rglob('.superpowers-lite-new-*')))
        self.assertFalse(list(self.home.rglob('.superpowers-lite-old-*')))

    def test_update_refuses_user_changed_owned_permission(self):
        self.run_cli()
        path = self.layout.config('opencode')
        config = json.loads(path.read_text())
        config['permission']['skill']['superpowers-brainstorming'] = 'allow'
        path.write_text(json.dumps(config))
        before = self.snapshot(self.home)
        with self.assertRaisesRegex(lite.SetupError, 'User-modified owned setting'):
            self.run_cli()
        self.assertEqual(before, self.snapshot(self.home))
        self.run_cli('--uninstall', '--only', 'opencode')
        self.assertEqual(json.loads(path.read_text())['permission']['skill']['superpowers-brainstorming'], 'allow')

    def test_update_refuses_user_deleted_owned_permission(self):
        self.run_cli()
        path = self.layout.config('opencode')
        config = json.loads(path.read_text())
        del config['permission']['skill']['superpowers-brainstorming']
        path.write_text(json.dumps(config))
        before = self.snapshot(self.home)
        with self.assertRaisesRegex(lite.SetupError, 'User-modified owned setting'):
            self.run_cli()
        self.assertEqual(before, self.snapshot(self.home))

    def test_old_unmarked_bootstrap_instructions_block_install(self):
        put(self.layout.codex / 'AGENTS.md', 'Run ~/.codex/superpowers/superpowers-codex bootstrap before work.\n')
        before = self.snapshot(self.home)
        with self.assertRaisesRegex(lite.SetupError, 'unmarked Superpowers bootstrap'):
            self.run_cli()
        self.assertEqual(before, self.snapshot(self.home))

    def test_malformed_manifest_fails_without_writes(self):
        self.run_cli()
        state = self.manifest()
        state['harnesses']['codex']['entries']['superpowers-brainstorming'] = {}
        self.layout.manifest.write_text(json.dumps(state))
        before = self.snapshot(self.home)
        with self.assertRaisesRegex(lite.SetupError, 'Invalid entry ownership record'):
            self.run_cli()
        self.assertEqual(before, self.snapshot(self.home))

    def test_recover_interrupted_apply_and_repeat_recovery(self):
        self.run_cli()
        before = {h: self.snapshot(getattr(self.layout, h)) for h in lite.HARNESSES}
        manifest = self.layout.manifest.read_bytes()
        plan, _, _ = self.prepare('--skills', 'verification-before-completion')
        # Simulate a process killed during apply: rollback cannot run in that process.
        with patch.object(lite, 'rollback', side_effect=OSError('process gone')):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(OSError, 'process gone'):
                plan.apply(fail_after=4)
        self.assertTrue((self.layout.store / 'install.lock').exists())
        self.run_cli('--recover')
        for h in lite.HARNESSES:
            self.assertEqual(before[h], self.snapshot(getattr(self.layout, h)))
        self.assertEqual(manifest, self.layout.manifest.read_bytes())
        self.assertFalse((self.layout.store / 'install.lock').exists())
        with self.assertRaisesRegex(lite.SetupError, 'No interrupted'):
            self.run_cli('--recover')

    def test_recover_committed_backup_cleanup(self):
        self.run_cli()
        plan, _, _ = self.prepare('--skills', 'verification-before-completion')
        with patch.object(lite, 'archive_backups', side_effect=OSError('process gone')):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(OSError, 'process gone'):
                plan.apply()
        committed = self.layout.manifest.read_bytes()
        self.assertTrue((self.layout.store / 'install.lock').exists())
        self.run_cli('--recover')
        self.assertEqual(committed, self.layout.manifest.read_bytes())
        self.assertFalse((self.layout.store / 'install.lock').exists())
        self.assertFalse(list(self.home.rglob('.superpowers-lite-old-*')))

    def test_concurrent_edit_before_commit_preserved(self):
        self.run_cli()
        plan, _, _ = self.prepare('--skills', 'verification-before-completion')
        p = self.layout.entry('claude', 'superpowers-brainstorming')
        p.write_text('concurrent change')
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(lite.SetupError, 'Changed since'):
            plan.apply()
        self.assertEqual(p.read_text(), 'concurrent change')

    def test_config_edit_during_planning_is_preserved(self):
        cfg = self.layout.config('codex')
        put(cfg, 'model = "original"\n')
        original = lite.ConfigEdit.result
        def user_edit(edit):
            result = original(edit)
            if edit.path == cfg:
                put(cfg, 'model = "user-choice"\n')
            return result
        with patch.object(lite.ConfigEdit, 'result', user_edit):
            with self.assertRaisesRegex(lite.SetupError, 'Changed since reading'):
                self.run_cli('--only', 'codex', '--enable-codex-multi-agent')
        self.assertEqual(lite.tomllib.loads(cfg.read_text()), {'model': 'user-choice'})
        self.assertFalse(self.layout.manifest.exists())

    def test_config_created_during_planning_is_preserved(self):
        cfg = self.layout.config('opencode')
        original = lite.ConfigEdit.result
        def user_create(edit):
            result = original(edit)
            if edit.path == cfg:
                put(cfg, '{"model":"user-choice"}')
            return result
        with patch.object(lite.ConfigEdit, 'result', user_create):
            with self.assertRaisesRegex(lite.SetupError, 'Changed since reading'):
                self.run_cli('--only', 'opencode')
        self.assertEqual(json.loads(cfg.read_text()), {'model': 'user-choice'})

    def test_uninstall_one_harness_keeps_other_libraries(self):
        self.run_cli()
        before = self.manifest()
        self.run_cli('--uninstall', '--only', 'codex', '--prune')
        after = self.manifest()
        self.assertNotIn('codex', after['harnesses'])
        for h in ['claude', 'opencode']:
            self.assertEqual(before['harnesses'][h], after['harnesses'][h])
            self.assertTrue((self.layout.store / 'bundles' / after['harnesses'][h]['bundle']).exists())

    def test_full_uninstall_prunes_owned_libraries(self):
        self.run_cli()
        self.run_cli('--uninstall', '--prune')
        self.assertEqual(self.manifest()['harnesses'], {})
        self.assertEqual(list((self.layout.store / 'bundles').iterdir()), [])
        self.assertFalse(self.layout.config('opencode').exists())


class FormatTests(unittest.TestCase):
    def test_jsonc_escaped_strings_and_comments(self):
        data = {'a': 'quote " literal,] // /*', 'b': '\\path,}', 'c': ['x', 'y']}
        raw = '// header\n' + json.dumps(data)[:-1] + ', /*end*/}'
        self.assertEqual(lite.load_json(raw), data)

    def test_json_duplicate_keys_rejected(self):
        with self.assertRaises(lite.SetupError):
            lite.load_json('{"x":1,"x":2}')

    def test_unterminated_comment_rejected(self):
        with self.assertRaises(lite.SetupError):
            lite.load_json('{"x":1 /* no end')

    def test_toml_roundtrip_all_value_types(self):
        text = '''title = "value\\ntext"
date = 2026-09-24
time = 12:00:00
timestamp = 2026-09-24T12:00:00Z
float = 1.25
inf = inf
nan = nan
"odd key" = true
[[tools]]
name = "one"
[[tools]]
name = "two"
[nested."a.b"]
array = [1, 2, 3]
'''
        result = lite.dump_toml(lite.tomllib.loads(text))
        self.assertEqual(lite.dump_toml(lite.tomllib.loads(result.decode())), result)

    def test_zip_extract_modes_and_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = io.BytesIO()
            with zipfile.ZipFile(data, 'w') as z:
                info = zipfile.ZipInfo('repo/script')
                info.external_attr = (stat.S_IFREG | 0o755) << 16
                z.writestr(info, '#!/bin/sh\necho ok\n')
            root = lite.extract_archive(data.getvalue(), Path(tmp) / 'good')
            if os.name != 'nt':
                self.assertTrue((root / 'script').stat().st_mode & stat.S_IXUSR)
            data = io.BytesIO()
            with zipfile.ZipFile(data, 'w') as z:
                z.writestr('../escape', 'bad')
            with self.assertRaises(lite.SetupError):
                lite.extract_archive(data.getvalue(), Path(tmp) / 'bad')
            self.assertFalse((Path(tmp) / 'escape').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
