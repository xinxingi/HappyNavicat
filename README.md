# HappyNavicat

macOS / Windows 的 Navicat Premium 状态清理脚本。只依赖 **Python 3.9+ 标准库**，无需安装第三方依赖，不按版本号限制执行。

## 使用

Windows 在项目目录打开 PowerShell 或命令提示符：

```powershell
# 预览
py -3 happy_navicat.py

# 保存工作并退出 Navicat 后执行
py -3 happy_navicat.py --apply
```

如果没有 `py` 启动器，可用 `python` 替换 `py -3`。

macOS：

```sh
# 预览
python3 happy_navicat.py

# 执行
python3 happy_navicat.py --apply

# 自定义应用路径（仅 macOS）
python3 happy_navicat.py --app '/Applications/Navicat Premium.app' --apply
```

**执行时直接删除，不创建备份，不支持恢复。** 默认只预览，必须加 `--apply` 才会修改数据。执行期间请勿启动 Navicat 或修改相关配置。

## Windows 清理范围

自动定位当前登录用户 `HKEY_CURRENT_USER\Software\PremiumSoft` 下的 `NavicatPremium` 和带数字后缀的产品分支，不需要指定安装路径，也不需要管理员权限。

- 清除产品分支下 `Registration*` 注册信息分支及其子项中的值，保留键结构。
- 删除产品分支下的 `Update` 分支。
- 保留 `Servers` 连接配置和其他产品的分支。
- 在 64 位 Windows 上检查 32 位、64 位注册表视图；系统共享的条目执行时自动去重。

不会仅根据 `Info`、`ShellFolder` 名称删除全局 CLSID 项。这些项缺少可靠的应用归属信息，直接删除可能影响其他软件。因此，依赖此类隐藏状态的版本可能无法完全重置。

清理前检查 Navicat 是否退出、注册表是否变化；命令失败会停止，并报告已完成的操作。当前操作可能部分完成，已删除内容不会回滚。

## macOS 清理范围

读取应用自身的 Navicat Premium Bundle ID 定位配置。清理匹配的顶层偏好键、状态目录中的隐藏文件，以及登录钥匙串中的匹配条目；钥匙串自动包含，无需额外开关。

匹配名称为 32 位大写十六进制字符；文件不递归删除、不跟随目标文件符号链接。钥匙串要求 service 与应用 Bundle ID 完全一致，只读取元数据，不读取密码。请以普通用户运行，不要使用 `sudo`。


# 不保证适用于所有补丁版本。