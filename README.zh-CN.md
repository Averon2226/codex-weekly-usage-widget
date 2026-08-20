# Codex 周额度悬浮窗

一个轻量的 Windows 桌面悬浮窗，用于显示本机 Codex 周额度剩余百分比、重置时间和最近一次成功更新时间。

[English](README.md)

<p align="center">
  <img src="github.jpg" alt="Codex 周额度悬浮窗截图" width="420">
</p>

> **非官方工具声明：** 本项目与 OpenAI 无隶属关系。程序读取本机 Codex 登录态，并使用 Codex 当前的非公开额度接口；接口、认证格式或适用条款变化后，功能可能失效。

## 功能

- 显示周额度剩余百分比
- 显示重置时间
- 显示最近一次成功更新时间
- 轻量桌面悬浮窗
- 不依赖第三方 Python 包
- 支持 Windows 开机自启

## 安装与运行

运行要求：

- Windows 10 或 Windows 11
- Python 3.10 或更高版本，包含 `py` / `pyw` 启动器
- 已登录并至少正常使用过一次 Codex

项目只使用 Python 标准库，不需要安装第三方依赖。

在项目目录打开 PowerShell：

```powershell
py -3 codex_weekly_widget.py
```

也可以双击 `start_widget.bat`，在不打开控制台窗口的情况下启动悬浮窗。

### 开机自启

```powershell
powershell -ExecutionPolicy Bypass -File .\install_startup.ps1
```

脚本会在当前 Windows 用户的启动目录创建快捷方式。删除该快捷方式即可取消自启。

## 工作原理

1. 程序优先请求 `https://chatgpt.com/backend-api/wham/usage`。
2. 请求使用本机 `%USERPROFILE%\.codex\auth.json` 中已有的登录态。
3. 当额度接口暂时不可用时，程序回退读取 `%USERPROFILE%\.codex\logs_2.sqlite` 中的额度响应记录。
4. 同时兼容新版 `primary` 周额度字段和旧版 `secondary` 周额度字段。
5. 界面每 2.5 秒刷新，额度接口最多每 30 秒请求一次。
6. 超过 15 分钟没有新的额度响应时，程序显示 `--%`，避免把旧数据误认为实时数据。

## 隐私与安全

- 程序不提供远程代理或账号存储服务，数据读取发生在本机。
- **绝不要**把 `auth.json`、Cookie、Token、数据库、日志或包含账号信息的截图提交到 GitHub。
- 仓库中的 `.gitignore` 已覆盖常见凭据和运行时文件，但发布前仍应检查暂存区内容。
- 本项目依赖非公开、非稳定接口，Codex 更新后可能需要调整解析逻辑。
- 发布前请阅读 [SECURITY.md](SECURITY.md) 和 [OpenAI 使用条款](https://openai.com/policies/terms-of-use/)。

## 故障排查

如果界面显示 `--%`：

1. 确认 Codex 已登录，并且近期产生过一次正常请求。
2. 确认 `%USERPROFILE%\.codex\auth.json` 存在且登录态未过期。
3. 检查防火墙、代理或网络策略是否阻止 `chatgpt.com`。
4. 如果 Codex 刚升级，私有接口字段可能已经变化。提交 Issue 时只提供脱敏后的现象，不要上传 Token 或完整日志。

## 开发与测试

```powershell
py -3 -m unittest discover -s . -p "test_*.py" -v
```

GitHub Actions 会在 Windows 和 Python 3.10–3.13 上运行编译检查与单元测试。CI 使用固定的脱敏样例，不会访问真实 Codex 账号。

## 项目结构

```text
codex_weekly_widget.py       # 悬浮窗和额度读取逻辑
test_codex_weekly_widget.py  # 单元测试
start_widget.bat             # 手动启动
install_startup.ps1          # 配置开机自启
github.jpg                   # README 截图
SECURITY.md                  # 安全说明
CONTRIBUTING.md              # 贡献指南
CHANGELOG.md                 # 更新记录
```

## 许可证

本项目采用 [MIT License](LICENSE)。

