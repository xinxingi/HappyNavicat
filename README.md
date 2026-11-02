# HappyNavicat

macOS 的 Navicat Premium 状态清理脚本。只依赖 **Python 3.9+ 标准库**，无需 pip、虚拟环境或编译工具链。

## 使用

在项目目录运行：

```sh
# 默认只预览，不修改数据
python3 happy_navicat.py

# 保存工作、手动退出 Navicat，确认预览目标后执行
python3 happy_navicat.py --apply

# 自定义应用路径
python3 happy_navicat.py --app '/Applications/Navicat Premium.app'

# Navicat 17.3.7+：额外预览匹配的钥匙串条目
python3 happy_navicat.py --include-keychain

# 同时删除这些钥匙串条目；这些条目不会备份
python3 happy_navicat.py --apply --include-keychain

# 自定义文件备份根目录
python3 happy_navicat.py --apply --backup-dir '/path/to/backups'
```

请使用当前普通用户运行，不要使用 `sudo`。程序不会强制关闭 Navicat，也不会访问数据库或修改应用二进制。

## 适用范围与匹配规则

沿用参考脚本的 Navicat Premium **15、16、17** 版本映射，同时检查应用 Bundle ID。尚未在这些版本上逐一验证重置效果，不保证适用于所有补丁版本。

- 偏好设置：只删除对应应用偏好字典中，名称为 32 位大写十六进制字符的顶层键。
- 隐藏文件：只处理 `~/Library/Application Support/PremiumSoft CyberTech/Navicat CC/Navicat Premium/` 下，名字为 `.` 加 32 位大写十六进制字符的普通文件；不递归，不删除目录，不跟随目标文件符号链接。
- 钥匙串：默认不读取、不修改。显式指定 `--include-keychain` 且为 17.3.7 及以上的 17.x 时，读取登录钥匙串元数据，只匹配同一条 generic password 记录中 service 完全一致、account 为 32 位大写十六进制的条目。不会读取或导出密码内容。

这些名称规则仍是启发式判断，不能证明目标一定是试用记录；请检查预览。匹配范围比参考脚本的 `[0-9A-Z]` 更窄，使用 `[0-9A-F]`。

**钥匙串删除不能由本工具的文件备份恢复。** 只有确认列出的条目可删除时，才使用 `--apply --include-keychain`。

## 备份与恢复

默认保存到 `~/Library/Application Support/HappyNavicat/backups/` 下的独立目录。目录权限 `0700`，文件权限 `0600`。备份可能包含敏感配置，请妥善保管。

- `preferences.plist`：修改前的完整偏好设置，仅在删除偏好键时生成。
- `hidden-N.bin`：待删除隐藏文件的原始内容。
- `manifest.json`：原始路径和备份文件的映射，以及本次计划。
- `journal.jsonl`：每一步操作前后的日志；末尾有 `completed` 才表示全部完成。

所有文件备份成功后才开始清理。执行前重新检查应用进程、配置和文件内容，发现变化就停止。偏好设置通过 macOS `defaults` 读写，避免直接修改 plist 后被缓存覆盖。

清理不是事务操作；中途失败时会保留备份和日志，不自动覆盖当前配置回滚。执行期间请勿启动 Navicat 或编辑相关配置。

恢复时先退出 Navicat，按 `manifest.json` 核对原始路径。整份偏好设置可这样恢复：

```sh
/usr/bin/defaults import '/原始偏好设置路径.plist' '/备份目录/preferences.plist'
```

这会覆盖备份之后的偏好设置变更。隐藏文件按清单中的 `original` 和 `backup` 复制回去，避免覆盖后来生成的文件。文件备份不包含扩展属性或 ACL；钥匙串条目没有备份。

## 测试

```sh
python3 -B -m unittest discover -s tests -v
```

测试使用临时文件、合成数据和模拟系统命令，不清理真实 Navicat 数据，不操作真实钥匙串。
