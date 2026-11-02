import contextlib
import copy
import io
import json
from pathlib import Path
import plistlib
import stat
import tempfile
import unittest
from unittest.mock import patch

import happy_navicat as app

HASH = '0123456789ABCDEF0123456789ABCDEF'
SERVICE = 'com.navicat.NavicatPremium'


class HappyNavicatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))

    def make_app(self, version='17.3.7', bundle=SERVICE):
        path = self.home / 'Navicat Premium.app'
        (path / 'Contents').mkdir(parents=True, exist_ok=True)
        with (path / 'Contents/Info.plist').open('wb') as output:
            plistlib.dump({'CFBundleShortVersionString': version, 'CFBundleIdentifier': bundle}, output)
        return path

    def make_plan(self):
        state = self.home / app.STATE_DIR
        state.mkdir(parents=True)
        target = state / ('.' + HASH)
        target.write_bytes(b'original state')
        keep = state / 'connections.json'
        keep.write_bytes(b'keep')
        snapshot = {HASH: ['trial'], 'Servers': {'database': 'keep'}}
        plan = app.Plan('17.3.7', SERVICE, self.home / 'preferences.plist', snapshot,
                        [HASH], [target], self.home / 'login.keychain-db', [], True)
        return plan

    def mock_system(self, plan):
        actual = copy.deepcopy(plan.snapshot)
        self.stack.enter_context(patch.object(app, 'navicat_running', return_value=False))
        self.stack.enter_context(patch.object(app, 'read_preferences', side_effect=lambda _: copy.deepcopy(actual)))

        def execute(*arguments):
            self.assertEqual(arguments[:3], ('/usr/bin/defaults', 'delete', str(plan.preferences)))
            # 每一个备份文件及清单必须在首次删除前存在。
            backup = next((self.home / 'backups').iterdir())
            self.assertTrue((backup / 'manifest.json').exists())
            self.assertEqual(plistlib.loads((backup / 'preferences.plist').read_bytes()), plan.snapshot)
            self.assertEqual((backup / 'hidden-0.bin').read_bytes(), b'original state')
            del actual[arguments[3]]
            return b''

        command = self.stack.enter_context(patch.object(app, 'run_command', side_effect=execute))
        return actual, command

    def test_version_boundaries(self):
        for version, expected in [('15', False), ('16.3.7', False), ('17.3.6', False),
                                  ('17.3.7', True), ('17.10.0', True)]:
            self.assertEqual(app.version_info(version)[1], expected)
        for invalid in ['', '18.0', '17..7', '17.3.7-beta', '17.3.7.1', '-17']:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                app.version_info(invalid)

    def test_hidden_files_are_exact_and_nonrecursive(self):
        plan = self.make_plan()
        directory = plan.files[0].parent
        (directory / ('.' + HASH + '.bak')).write_bytes(b'keep')
        (directory / 'nested').mkdir()
        (directory / 'nested' / ('.' + HASH)).write_bytes(b'keep')
        self.assertEqual(app.find_hidden_files(directory), plan.files)
        self.assertFalse(app.is_hash(HASH.lower()))
        self.assertFalse(app.is_hash('Z' * 32))

    def test_matching_symlinks_are_rejected(self):
        target = self.home / 'important'
        target.write_bytes(b'keep')
        (self.home / ('.' + HASH)).symlink_to(target)
        with self.assertRaises(ValueError):
            app.find_hidden_files(self.home)
        self.assertEqual(target.read_bytes(), b'keep')

    def test_keychain_service_class_and_account_must_match_same_record(self):
        def item(kind, service, account):
            return (f'keychain: "test"\nclass: "{kind}"\nattributes:\n'
                    f'    "svce"<blob>="{service}"\n    "acct"<blob>="{account}"\n')
        text = (item('genp', SERVICE + '.other', HASH) + item('inet', SERVICE, HASH)
                + item('genp', SERVICE, 'normal-user') + item('genp', SERVICE, HASH))
        self.assertEqual(app.parse_keychain_accounts(text, SERVICE), [HASH])
        text = f'class: "genp"\n"svce"<blob>="{SERVICE}"\nclass: "genp"\n"acct"<blob>="{HASH}"'
        self.assertEqual(app.parse_keychain_accounts(text, SERVICE), [])
        with self.assertRaises(ValueError):
            app.parse_keychain_accounts('unexpected format', SERVICE)

    def test_preview_never_modifies_files_or_reads_keychain_by_default(self):
        plan = self.make_plan()
        application = self.make_app()
        with patch.object(app, 'read_preferences', return_value=plan.snapshot), \
                patch.object(app, 'keychain_accounts') as keychain, \
                patch.object(app, 'apply_plan') as apply, \
                patch.object(app.os, 'geteuid', return_value=501), \
                patch.object(app.sys, 'platform', 'darwin'), \
                patch.dict(app.os.environ, {'HOME': str(self.home)}):
            self.assertEqual(app.main(['--app', str(application)]), 0)
            keychain.assert_not_called()
            apply.assert_not_called()
        self.assertEqual(plan.files[0].read_bytes(), b'original state')
        self.assertFalse((self.home / 'Library/Application Support/HappyNavicat').exists())

    def test_unsupported_app_and_keychain_version_fail_early(self):
        for version, bundle, include in [('18.0', SERVICE, False), ('17.3.7', 'other', False),
                                          ('16.0', SERVICE, True)]:
            with self.subTest(version=version, bundle=bundle), \
                    patch.object(app, 'read_preferences') as read, self.assertRaises(ValueError):
                app.build_plan(self.make_app(version, bundle), self.home, include)
            read.assert_not_called()

    def test_only_top_level_hash_keys_are_selected(self):
        preferences = {HASH: ['trial'], 'Servers': {HASH: 'keep'}, HASH.lower(): 'keep', 'Z' * 32: 'keep'}
        with patch.object(app, 'read_preferences', return_value=preferences):
            plan = app.build_plan(self.make_app(), self.home, False)
        self.assertEqual(plan.keys, [HASH])

    def test_apply_backs_up_before_deleting_and_preserves_other_settings(self):
        plan = self.make_plan()
        actual, command = self.mock_system(plan)
        backup = app.apply_plan(plan, self.home / 'backups')
        self.assertEqual(actual, {'Servers': {'database': 'keep'}})
        command.assert_called_once()
        self.assertFalse(plan.files[0].exists())
        self.assertEqual((plan.files[0].parent / 'connections.json').read_bytes(), b'keep')
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o700)
        for path in backup.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        records = [json.loads(line) for line in (backup / 'journal.jsonl').read_text().splitlines()]
        self.assertEqual(records[-1]['action'], 'completed')

    def test_backup_failure_prevents_all_deletion(self):
        plan = self.make_plan()
        actual, command = self.mock_system(plan)
        with patch.object(app, 'write_private', side_effect=OSError('disk full')), self.assertRaises(OSError):
            app.apply_plan(plan, self.home / 'backups')
        command.assert_not_called()
        self.assertEqual(actual, plan.snapshot)
        self.assertTrue(plan.files[0].exists())

    def test_file_changed_during_backup_prevents_deletion(self):
        plan = self.make_plan()
        _, command = self.mock_system(plan)
        original_write = app.write_private

        def write(path, content):
            original_write(path, content)
            if path.name == 'manifest.json':
                plan.files[0].write_bytes(b'changed externally')

        with patch.object(app, 'write_private', side_effect=write), self.assertRaises(RuntimeError):
            app.apply_plan(plan, self.home / 'backups')
        command.assert_not_called()
        self.assertEqual(plan.files[0].read_bytes(), b'changed externally')

    def test_running_application_prevents_backup_and_deletion(self):
        plan = self.make_plan()
        with patch.object(app, 'navicat_running', return_value=True), self.assertRaises(RuntimeError):
            app.apply_plan(plan, self.home / 'backups')
        self.assertFalse((self.home / 'backups').exists())
        self.assertTrue(plan.files[0].exists())

    def test_failed_system_command_keeps_backups_and_does_not_mark_complete(self):
        plan = self.make_plan()
        self.mock_system(plan)
        with patch.object(app, 'run_command', side_effect=RuntimeError('command failed')), self.assertRaises(RuntimeError):
            app.apply_plan(plan, self.home / 'backups')
        backup = next((self.home / 'backups').iterdir())
        self.assertEqual(plistlib.loads((backup / 'preferences.plist').read_bytes()), plan.snapshot)
        self.assertNotIn('completed', (backup / 'journal.jsonl').read_text())
        self.assertTrue(plan.files[0].exists())

    def test_private_backup_never_overwrites_existing_file(self):
        path = self.home / 'backup'
        app.write_private(path, b'original')
        with self.assertRaises(FileExistsError):
            app.write_private(path, b'replacement')
        self.assertEqual(path.read_bytes(), b'original')


if __name__ == '__main__':
    unittest.main()
