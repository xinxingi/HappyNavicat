#!/usr/bin/env python3
"""macOS / Windows Navicat 状态清理：默认预览，仅依赖 Python 3.9+ 标准库。"""

import argparse
import copy
from dataclasses import dataclass
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import sys
from typing import Optional

STATE_DIR = 'Library/Application Support/PremiumSoft CyberTech/Navicat CC/Navicat Premium'


def is_hash(value):
    return re.fullmatch(r'[0-9A-F]{32}', value) is not None


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
    if not path.exists():
        return []
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

    @property
    def empty(self):
        return not (self.keys or self.files or self.accounts)


def build_plan(app, home):
    with (app / 'Contents/Info.plist').open('rb') as source:
        info = plistlib.load(source)
    if not isinstance(info, dict):
        raise ValueError('应用 Info.plist 顶层不是字典')
    service = info.get('CFBundleIdentifier')
    if not isinstance(service, str) or not re.fullmatch(r'com\.(?:navicat|prect)\.NavicatPremium[0-9]*', service):
        raise ValueError('应用 Bundle ID 不是 Navicat Premium，拒绝处理')
    # 版本号仅用于显示，不参与目标筛选；缺失或预发布版本也不阻止执行。
    version = info.get('CFBundleShortVersionString')
    if not isinstance(version, str) or not version.strip():
        version = '未知版本'
    preferences = home / f'Library/Preferences/{service}.plist'
    snapshot = read_preferences(preferences)
    keys = sorted(key for key in (snapshot or {}) if is_hash(key))
    keychain = home / 'Library/Keychains/login.keychain-db'
    return Plan(version, service, preferences, snapshot, keys, find_hidden_files(home / STATE_DIR),
                keychain, keychain_accounts(keychain, service))


def print_plan(plan):
    print(f'Navicat Premium {plan.version} ({plan.service})')
    for key in plan.keys:
        print(f'  偏好设置键：{plan.preferences} → {key}')
    for path in plan.files:
        print(f'  隐藏文件：{path}')
    for account in plan.accounts:
        print(f'  钥匙串：service={plan.service} account={account}')
    print(f'共 {len(plan.keys)} 个偏好设置键、{len(plan.files)} 个隐藏文件、{len(plan.accounts)} 个钥匙串条目。')


def assert_preferences_unchanged(plan):
    if read_preferences(plan.preferences) != plan.snapshot:
        raise RuntimeError('偏好设置在预览后发生变化，已停止；请重新预览')


def assert_file_unchanged(path, expected):
    if not regular_file(path) or path.read_bytes() != expected:
        raise RuntimeError(f'文件在检查后发生变化，拒绝删除：{path}')


def apply_plan(plan):
    if navicat_running():
        raise RuntimeError('请保存工作并手动退出 Navicat Premium 后重试')
    assert_preferences_unchanged(plan)
    contents = {}
    for path in plan.files:
        if not regular_file(path):
            raise RuntimeError(f'目标已消失：{path}')
        contents[path] = path.read_bytes()
    if keychain_accounts(plan.keychain, plan.service) != plan.accounts:
        raise RuntimeError('钥匙串匹配条目发生变化，请重新预览')
    if any(not is_hash(account) for account in plan.accounts):
        raise ValueError('非法钥匙串账号，拒绝删除')
    # 读取目标后再次检查；文件内容仅在内存中用于变化检测，不保存备份。
    if navicat_running():
        raise RuntimeError('Navicat 已重新启动，请退出后重试')
    assert_preferences_unchanged(plan)
    for path, content in contents.items():
        assert_file_unchanged(path, content)
    completed = 0
    try:
        expected = copy.deepcopy(plan.snapshot)
        for key in plan.keys:
            run_command('/usr/bin/defaults', 'delete', str(plan.preferences), key)
            completed += 1
            print(f'已删除偏好设置键：{key}')
            del expected[key]
        if plan.keys and (read_preferences(plan.preferences) or {}) != expected:
            raise RuntimeError('偏好设置清理后的校验不一致，已停止后续操作')
        for path, content in contents.items():
            assert_file_unchanged(path, content)
            path.unlink()
            completed += 1
            print(f'已删除隐藏文件：{path}')
        for account in plan.accounts:
            run_command('/usr/bin/security', 'delete-generic-password', '-s', plan.service,
                        '-a', account, str(plan.keychain))
            completed += 1
            print(f'已删除钥匙串条目：{account}')
        if plan.accounts and set(plan.accounts).intersection(keychain_accounts(plan.keychain, plan.service)):
            raise RuntimeError('钥匙串中仍存在计划删除的条目')
    except (Exception, KeyboardInterrupt):
        print(f'清理未全部完成，已执行 {completed} 项删除；未创建备份，已执行操作不会回滚。', file=sys.stderr)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='执行清理；默认只预览')
    parser.add_argument('--app', type=Path, help='macOS 应用路径；Windows 自动定位当前用户注册表')
    args = parser.parse_args(argv)
    if sys.platform not in ('darwin', 'win32'):
        parser.error('仅支持 macOS 和 Windows')
    if sys.platform == 'win32' and args.app is not None:
        parser.error('--app 仅用于 macOS；Windows 自动定位当前用户注册表')
    try:
        if sys.platform == 'win32':
            import windows_reset
            registry = windows_reset.Registry()
            actions = windows_reset.build_plan(registry)
            print('模式：执行' if args.apply else '模式：仅预览')
            windows_reset.print_plan(actions)
            if not actions:
                print('没有发现符合规则的目标；这不代表试用状态已重置。')
            elif args.apply:
                windows_reset.apply_plan(actions, registry)
                print('已完成列出的清理操作。是否生效需要重新打开 Navicat 验证。')
            else:
                print('预览结束，没有修改数据。核对后加 --apply 执行。')
            return 0
        if os.geteuid() == 0:
            parser.error('请以普通用户身份运行，不要使用 sudo')
        home = Path(os.environ.get('HOME', ''))
        if not home.is_absolute() or home == Path('/'):
            parser.error('HOME 必须是用户主目录的绝对路径')
        application = args.app or Path('/Applications/Navicat Premium.app')
        plan = build_plan(application.expanduser(), home)
        print('模式：执行' if args.apply else '模式：仅预览')
        print_plan(plan)
        if plan.empty:
            print('没有发现符合规则的目标；这不代表试用状态已重置。')
        elif not args.apply:
            print('预览结束，没有修改数据。核对后加 --apply 执行。')
        else:
            apply_plan(plan)
            print('已完成列出的清理操作。是否生效需要重新打开 Navicat 验证。')
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, plistlib.InvalidFileException) as error:
        print(f'错误：{error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('已中断；已执行的删除不会回滚。', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
