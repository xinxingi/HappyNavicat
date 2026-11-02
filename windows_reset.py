"""Windows 注册表清理后端；winreg 仅在 Windows 执行时加载。"""

import csv
from dataclasses import dataclass
import importlib
import io
import locale
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = r'Software\PremiumSoft'
PRODUCT = re.compile(r'NavicatPremium[0-9]*', re.IGNORECASE)


@dataclass(eq=True)
class Snapshot:
    values: dict
    children: dict


@dataclass
class Action:
    view: int
    view_name: str
    path: str
    kind: str
    snapshot: Snapshot

    @property
    def recursive(self):
        return self.kind == 'delete_tree'


class Registry:
    def __init__(self):
        self.api = importlib.import_module('winreg')
        is_64_bit = sys.maxsize > 2**32 or bool(os.environ.get('PROCESSOR_ARCHITEW6432'))
        self.views = [('64 位', self.api.KEY_WOW64_64KEY), ('32 位', self.api.KEY_WOW64_32KEY)] if is_64_bit else [('默认', 0)]

    def subkeys(self, path, view):
        try:
            key = self.api.OpenKey(self.api.HKEY_CURRENT_USER, path, 0, self.api.KEY_READ | view)
        except FileNotFoundError:
            return []
        with key:
            count = self.api.QueryInfoKey(key)[0]
            return sorted((self.api.EnumKey(key, index) for index in range(count)), key=str.casefold)

    def read(self, path, view, recursive=False):
        try:
            key = self.api.OpenKey(self.api.HKEY_CURRENT_USER, path, 0, self.api.KEY_READ | view)
        except FileNotFoundError:
            return None
        with key:
            child_count, value_count, _ = self.api.QueryInfoKey(key)
            values = {}
            for index in range(value_count):
                name, value, kind = self.api.EnumValue(key, index)
                values[name] = (value, kind)
            names = [self.api.EnumKey(key, index) for index in range(child_count)] if recursive else []
        children = {}
        for name in names:
            child = self.read(path + '\\' + name, view, recursive=True)
            if child is None:
                raise RuntimeError(f'注册表在读取期间发生变化：{path}\\{name}')
            children[name] = child
        return Snapshot(values, children)

    def clear_values(self, action):
        with self.api.OpenKey(self.api.HKEY_CURRENT_USER, action.path, 0,
                              self.api.KEY_SET_VALUE | action.view) as key:
            for name in action.snapshot.values:
                self.api.DeleteValue(key, name)
                print(f'  已删除值：{name or "(默认)"}')

    def delete_tree(self, action):
        def remove(path, snapshot):
            if self.read(path, action.view, recursive=True) != snapshot:
                raise RuntimeError(f'注册表在删除期间发生变化：{path}')
            for name, child in snapshot.children.items():
                remove(path + '\\' + name, child)
            # 子项删除后再检查父项的值，防止覆盖期间新写入的内容。
            if self.read(path, action.view, recursive=True) != Snapshot(snapshot.values, {}):
                raise RuntimeError(f'注册表在删除期间发生变化：{path}')
            self.api.DeleteKeyEx(self.api.HKEY_CURRENT_USER, path, action.view, 0)
            print(f'  已删除键：HKCU\\{path}')
        remove(action.path, action.snapshot)


def build_plan(registry):
    actions = []
    for view_name, view in registry.views:
        for product in registry.subkeys(ROOT, view):
            if not PRODUCT.fullmatch(product):
                continue
            product_path = ROOT + '\\' + product
            for name in registry.subkeys(product_path, view):
                path = product_path + '\\' + name
                if name.casefold() == 'update':
                    snapshot = registry.read(path, view, recursive=True)
                    if snapshot is not None:
                        actions.append(Action(view, view_name, path, 'delete_tree', snapshot))
                elif name.casefold().startswith('registration'):
                    collect_registration(registry, path, view, view_name, actions)
    return actions


def collect_registration(registry, path, view, view_name, actions):
    # Registration 分支保留键结构，只删除其中的值；Servers 分支始终跳过。
    snapshot = registry.read(path, view)
    if snapshot is None:
        raise RuntimeError(f'注册表在读取期间发生变化：{path}')
    if snapshot.values:
        actions.append(Action(view, view_name, path, 'clear_values', snapshot))
    for name in registry.subkeys(path, view):
        if name.casefold() != 'servers':
            collect_registration(registry, path + '\\' + name, view, view_name, actions)


def print_plan(actions):
    print('Navicat Premium / Windows（当前用户）')
    for action in actions:
        operation = '删除更新分支' if action.recursive else f'清除 {len(action.snapshot.values)} 个注册值'
        print(f'  [{action.view_name}] {operation}：HKCU\\{action.path}')
    print(f'共 {len(actions)} 个注册表操作；系统共享的 32/64 位条目执行时会自动去重。')
    print('仅清理 Navicat Premium 分支；未确认归属的全局 CLSID 条目不纳入清理。')


def navicat_running():
    system_root = os.environ.get('SystemRoot')
    if not system_root:
        raise RuntimeError('未找到 Windows SystemRoot，无法检查进程')
    command = str(Path(system_root) / 'System32' / 'tasklist.exe')
    result = subprocess.run([command, '/FO', 'CSV', '/NH'], capture_output=True, timeout=15, check=False)
    if result.returncode:
        raise RuntimeError('无法检查 Navicat 进程，已停止')
    text = result.stdout.decode(locale.getpreferredencoding(False), errors='replace')
    names = {'navicat.exe', 'navicatpremium.exe', 'navicat premium.exe'}
    return any(row and row[0].casefold() in names for row in csv.reader(io.StringIO(text)))


def cleaned(action, current):
    return current is None if action.recursive else current is not None and not current.values


def apply_plan(actions, registry, running=None):
    running = running or navicat_running
    if running():
        raise RuntimeError('请保存工作并手动退出 Navicat 后重试')
    for action in actions:
        if registry.read(action.path, action.view, action.recursive) != action.snapshot:
            raise RuntimeError(f'注册表在预览后发生变化，请重新预览：{action.path}')
    completed = []
    active = None
    try:
        for action in actions:
            active = action
            if running():
                raise RuntimeError('Navicat 已重新启动，请退出后重试')
            current = registry.read(action.path, action.view, action.recursive)
            same_completed = any(old.path.casefold() == action.path.casefold()
                                 and old.kind == action.kind and old.snapshot == action.snapshot
                                 for old in completed)
            if same_completed and cleaned(action, current):
                print(f'已由另一注册表视图清理：HKCU\\{action.path}')
                continue
            if current != action.snapshot:
                raise RuntimeError(f'注册表在执行期间发生变化：{action.path}')
            if action.recursive:
                registry.delete_tree(action)
            else:
                registry.clear_values(action)
            if not cleaned(action, registry.read(action.path, action.view, action.recursive)):
                raise RuntimeError(f'清理后的注册表校验失败：{action.path}')
            completed.append(action)
            print(f'已完成：HKCU\\{action.path}')
    except (Exception, KeyboardInterrupt):
        print(f'清理未全部完成，已完成 {len(completed)} 个注册表操作；'
              f'当前操作可能部分完成：{active.path if active else "无"}。不备份、不回滚。', file=sys.stderr)
        raise
