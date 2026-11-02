# HappyNavicat

macOS 的 Navicat Premium 状态清理脚本。只依赖 **Python 3.9+ 标准库**，无需安装依赖。

## 使用

```sh
# 预览全部匹配目标
python3 happy_navicat.py

# 保存工作、退出 Navicat 后，执行全部清理
python3 happy_navicat.py --apply

# 自定义应用路径
python3 happy_navicat.py --app '/Applications/Navicat Premium.app' --apply
```

**执行时直接删除，不创建备份，不支持恢复。** 每项删除成功后会在终端输出结果；发生错误会停止并报告已执行的删除数量。默认运行仍只预览，必须加 `--apply` 才会修改数据。

请以普通用户运行，不要使用 `sudo`。执行期间请勿启动 Navicat 或修改相关配置。

# 不保证适用于所有补丁版本