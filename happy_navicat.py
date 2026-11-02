#!/usr/bin/env python3
"""macOS Navicat 状态清理：默认预览，仅依赖 Python 3.9+ 标准库。"""

import argparse
import copy
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import sys
import tempfile
from typing import Optional

STATE_DIR = 'Library/Application Support/PremiumSoft CyberTech/Navicat CC/Navicat Premium'


def is_hash(value):
    return re.fullmatch(r'[0-9A-F]{32}', value) is not None


def version_info(text):
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+){0,2}', text):
        raise ValueError(f'无法识别版本号：{text}')
    parts = [int(part) for part in text.split('.')]
    major, minor, patch = (parts + [0, 0])[:3]
    services = {15: 'com.prect.NavicatPremium15', 16: 'com.navicat.NavicatPremium',
                17: 'com.navicat.NavicatPremium'}
    if major not in services:
        raise ValueError(f'当前仅处理 Navicat 15、16、17，无法处理 {text}')
    return services[major], major == 17 and (minor, patch) >= (3, 7)


def run_command(*arguments):
    result = subprocess.run(arguments, capture_output=True, timeout=60, check=False)
    if result.returncode:
        detail = result.stderr.decode('utf-8', errors='replace').strip()
        raise RuntimeError(f'{Path(arguments[0]).name} 执行失败（{result.returncode}）：{detail}')
    return result.stdout


def regular_file(path):
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(mode):
        raise ValueError(f'目标不是普通文件，拒绝处理目录或符号链接：{path}')
    return True


def read_preferences(path):
    if not regular_file(path):
        return None
    # 通过系统偏好设置服务读取，避免直接编辑磁盘文件与缓存不一致。
    result = plistlib.loads(run_command('/usr/bin/defaults', 'export', str(path), '-'))
    if not isinstance(result, dict):
        raise ValueError('偏好设置顶层不是字典，拒绝处理')
    return result


def find_hidden_files(directory):
    if not directory.exists():
        return []
    return sorted(path for path in directory.iterdir()
                  if path.name.startswith('.') and is_hash(path.name[1:]) and regular_file(path))


def parse_keychain_accounts(text, expected_service):
    """按完整记录匹配，不混淆相邻条目的 service 和 account。"""
    accounts, record = set(), {}
    saw_class = False

    def flush():
        account = record.get('acct', '')
        if record.get('class') == 'genp' and record.get('svce') == expected_service and is_hash(account):
            accounts.add(account)

    for line in map(str.strip, text.splitlines()):
        if line.startswith(('keychain:', 'class:')):
            flush()
            record = {}
            if line.startswith('class:'):
                saw_class = True
                match = re.fullmatch(r'class:\s*"([^"]+)"', line)
                if match:
                    record['class'] = match[1]
        else:
            match = re.fullmatch(r'"(acct|svce)"<blob>="([^"]*)"', line)
            if match:
                record[match[1]] = match[2]
    flush()
    if text.strip() and not saw_class:
        raise ValueError('无法识别钥匙串元数据格式，拒绝猜测删除目标')
    return sorted(accounts)


def keychain_accounts(path, service):
    # 不使用 -d/-r，不读取或导出密码内容。
    data = run_command('/usr/bin/security', 'dump-keychain', str(path))
    return parse_keychain_accounts(data.decode('utf-8'), service)


def navicat_running():
    result = subprocess.run(['/usr/bin/pgrep', '-x', 'Navicat Premium'],
                            capture_output=True, timeout=10, check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError('无法检查 Navicat 进程，已停止')
    return result.returncode == 0


@dataclass
class Plan:
    version: str
    service: str
    preferences: Path
    snapshot: Optional[dict]
    keys: list
    files: list
    keychain: Path
    accounts: list
    needs_keychain: bool

    @property
    def empty(self):
        return not (self.keys or self.files or self.accounts)


def build_plan(app, home, include_keychain):
    with (app / 'Contents/Info.plist').open('rb') as source:
        info = plistlib.load(source)
    if not isinstance(info, dict) or not isinstance(info.get('CFBundleShortVersionString'), str):
        raise ValueError('应用 Info.plist 缺少有效的版本号')
    version = info['CFBundleShortVersionString']
    service, needs_keychain = version_info(version)
    if info.get('CFBundleIdentifier') != service:
        raise ValueError(f'应用 Bundle ID 与预期的 {service} 不符，拒绝处理')
    if include_keychain and not needs_keychain:
        raise ValueError('--include-keychain 仅适用于 17.3.7 及以上的 17.x 版本')
    preferences = home / f'Library/Preferences/{service}.plist'
    snapshot = read_preferences(preferences)
    keys = sorted(key for key in (snapshot or {}) if is_hash(key))
    keychain = home / 'Library/Keychains/login.keychain-db'
    return Plan(version, service, preferences, snapshot, keys, find_hidden_files(home / STATE_DIR),
                keychain, keychain_accounts(keychain, service) if include_keychain else [], needs_keychain)


def print_plan(plan):
    print(f'Navicat Premium {plan.version} ({plan.service})')
    for key in plan.keys:
        print(f'  偏好设置键：{plan.preferences} → {key}')
    for path in plan.files:
        print(f'  隐藏文件：{path}')
    for account in plan.accounts:
        print(f'  钥匙串：service={plan.service} account={account}')
    print(f'共 {len(plan.keys)} 个偏好设置键、{len(plan.files)} 个隐藏文件、{len(plan.accounts)} 个钥匙串条目。')
    if plan.accounts:
        print('注意：钥匙串条目不会备份，删除后无法通过文件备份恢复。')


def write_private(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as destination:
        destination.write(content)
        destination.flush()
        os.fsync(destination.fileno())


def assert_preferences_unchanged(plan):
    if read_preferences(plan.preferences) != plan.snapshot:
        raise RuntimeError('偏好设置在预览后发生变化，已停止；请重新预览')


def assert_file_unchanged(path, expected):
    if not regular_file(path) or path.read_bytes() != expected:
        raise RuntimeError(f'文件在备份后发生变化，拒绝删除：{path}')


def journal(log, action, target):
    log.write(json.dumps({'action': action, 'target': target}, ensure_ascii=False) + '\n')
    log.flush()
    os.fsync(log.fileno())


def apply_plan(plan, backup_root):
    if navicat_running():
        raise RuntimeError('请保存工作并手动退出 Navicat Premium 后重试')
    assert_preferences_unchanged(plan)
    contents = {}
    for path in plan.files:
        if not regular_file(path):
            raise RuntimeError(f'目标已消失：{path}')
        contents[path] = path.read_bytes()
    if plan.accounts and keychain_accounts(plan.keychain, plan.service) != plan.accounts:
        raise RuntimeError('钥匙串匹配条目发生变化，请重新预览')
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix=datetime.now().strftime('%Y%m%d-%H%M%S-'), dir=backup_root))
    print(f'备份目录：{backup}')
    try:
        execute_with_backup(plan, backup, contents)
    except Exception:
        print(f'操作未全部完成；请检查备份及 journal.jsonl，已执行操作不会自动回滚：{backup}', file=sys.stderr)
        raise
    return backup


def execute_with_backup(plan, backup, contents):
    if plan.keys:
        write_private(backup / 'preferences.plist', plistlib.dumps(plan.snapshot))
    files = []
    for index, (path, content) in enumerate(contents.items()):
        name = f'hidden-{index}.bin'
        write_private(backup / name, content)
        files.append({'original': str(path), 'backup': name})
    manifest = {
        'format_version': 1, 'navicat_version': plan.version, 'service': plan.service,
        'preferences_path': str(plan.preferences),
        'preferences_backup': 'preferences.plist' if plan.keys else None,
        'removed_preference_keys': plan.keys, 'files': files,
        'keychain_path': str(plan.keychain), 'keychain_accounts': plan.accounts,
        'keychain_backed_up': False,
    }
    write_private(backup / 'manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8'))
    write_private(backup / 'journal.jsonl', b'')
    # 所有备份完成后，再次检查状态，才允许修改用户数据。
    if navicat_running():
        raise RuntimeError('Navicat 已重新启动，请退出后重试')
    assert_preferences_unchanged(plan)
    for path, content in contents.items():
        assert_file_unchanged(path, content)
    with (backup / 'journal.jsonl').open('a', encoding='utf-8') as log:
        expected = copy.deepcopy(plan.snapshot)
        for key in plan.keys:
            journal(log, 'before_delete_preference', key)
            run_command('/usr/bin/defaults', 'delete', str(plan.preferences), key)
            journal(log, 'deleted_preference', key)
            del expected[key]
        if plan.keys and (read_preferences(plan.preferences) or {}) != expected:
            raise RuntimeError('偏好设置清理后的校验不一致，已停止后续操作')
        for path, content in contents.items():
            assert_file_unchanged(path, content)
            journal(log, 'before_delete_file', str(path))
            path.unlink()
            journal(log, 'deleted_file', str(path))
        # 钥匙串操作无法通过文件备份恢复，放到最后。
        for account in plan.accounts:
            if not is_hash(account):
                raise ValueError('非法钥匙串账号，拒绝删除')
            journal(log, 'before_delete_keychain', account)
            run_command('/usr/bin/security', 'delete-generic-password', '-s', plan.service,
                        '-a', account, str(plan.keychain))
            journal(log, 'deleted_keychain', account)
        if plan.accounts and set(plan.accounts).intersection(keychain_accounts(plan.keychain, plan.service)):
            raise RuntimeError('钥匙串中仍存在计划删除的条目')
        journal(log, 'completed', 'all')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='执行清理；默认只预览')
    parser.add_argument('--app', type=Path, default=Path('/Applications/Navicat Premium.app'), help='应用路径')
    parser.add_argument('--backup-dir', type=Path, help='自定义备份根目录')
    parser.add_argument('--include-keychain', action='store_true', help='包含匹配的钥匙串条目；这些条目不会备份')
    args = parser.parse_args(argv)
    if sys.platform != 'darwin':
        parser.error('仅支持 macOS')
    if os.geteuid() == 0:
        parser.error('请以普通用户身份运行，不要使用 sudo')
    home = Path(os.environ.get('HOME', ''))
    if not home.is_absolute() or home == Path('/'):
        parser.error('HOME 必须是用户主目录的绝对路径')
    try:
        plan = build_plan(args.app.expanduser(), home, args.include_keychain)
        print('模式：执行' if args.apply else '模式：仅预览')
        print_plan(plan)
        if plan.needs_keychain and not args.include_keychain:
            print('此版本可能还需清理钥匙串；当前未包含，可用 --include-keychain 单独预览。')
        if plan.empty:
            print('没有发现符合规则的目标；这不代表试用状态已重置。')
        elif not args.apply:
            print('预览结束，没有修改数据。核对后加 --apply 执行。')
        else:
            backup_root = (args.backup_dir or home / 'Library/Application Support/HappyNavicat/backups').expanduser()
            apply_plan(plan, backup_root)
            print('已完成列出的清理操作。是否生效需要重新打开 Navicat 验证。')
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, plistlib.InvalidFileException) as error:
        print(f'错误：{error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('已中断；若已开始执行，请检查备份和日志。', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
