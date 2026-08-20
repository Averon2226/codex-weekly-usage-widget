# Codex Weekly Usage Widget

Windows 桌面悬浮窗，用于显示本机 Codex 周额度剩余百分比、重置时间和最近一次成功更新的时间。

> **非官方工具声明**：本项目与 OpenAI 无隶属关系。它读取本机 Codex 登录态，并访问 Codex 当前使用的非公开额度接口；接口、认证格式或服务条款变化都可能导致功能失效。请勿用于访问他人账户、绕过权限或规避速率限制。

## 功能

- 悬浮显示周额度剩余百分比。
- 显示 `Reset` 重置时间和 `Update` 最近更新时间。
- 优先读取当前额度接口，接口不可用时兼容本地日志。
- 兼容新版 `primary` 周额度和旧版 `secondary` 周额度字段。
- 超过 15 分钟没有新数据时显示 `--%`，避免旧数据被误认为实时值。
- 左键拖动窗口，右键退出；可配置为 Windows 开机自启。

## 运行要求

- Windows 10 或 Windows 11
- Python 3.10 或更高版本，包含 `py` / `pyw` 启动器
- 已登录并使用过 Codex

项目只使用 Python 标准库，不需要安装第三方依赖。

## 快速开始

在项目目录打开 PowerShell：

```powershell
py -3 codex_weekly_widget.py
```

也可以双击 `start_widget.bat`，它会用 `pyw` 在后台启动悬浮窗。

## 开机自启

在项目目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_startup.ps1
```

脚本会在当前 Windows 用户的启动目录创建快捷方式。删除该快捷方式即可取消自启。

## 数据来源和刷新策略

1. 首选请求 `https://chatgpt.com/backend-api/wham/usage`。
2. 请求使用本机 `%USERPROFILE%\.codex\auth.json` 中已有的登录态，不会把令牌发送到本项目的服务器。
3. 当接口暂时不可用时，程序读取 `%USERPROFILE%\.codex\logs_2.sqlite` 中的脱敏额度响应记录。
4. 界面每 2.5 秒刷新；额度接口最多每 30 秒请求一次。
5. 没有新额度响应超过 15 分钟后，显示 `--%` 和最后更新时间。

## 安全和隐私

- **绝不要**把 `auth.json`、Cookie、Token、日志数据库或包含账号信息的截图提交到 GitHub。
- 仓库中的 `.gitignore` 已忽略常见凭据、数据库、日志和临时文件，但提交前仍应检查暂存区。
- 项目不提供远程代理或账号存储服务，所有读取都发生在本机。
- 额度接口不是公开稳定 API，升级 Codex 后可能需要更新解析逻辑。
- 详情请阅读 [SECURITY.md](SECURITY.md) 和 [OpenAI 使用条款](https://openai.com/policies/terms-of-use/)。

## 故障排查

如果显示 `--%`：

1. 确认 Codex 已登录，并且近期确实产生过一次正常请求。
2. 确认 `%USERPROFILE%\.codex\auth.json` 存在且登录态未过期。
3. 检查网络、防火墙或代理是否阻止 `chatgpt.com`。
4. 如果 Codex 刚升级，接口字段可能已变化；请提交脱敏后的错误现象，不要上传令牌或完整日志。

## 开发和测试

```powershell
py -3 -m unittest discover -s . -p "test_*.py" -v
```

GitHub Actions 会在 Windows 和 Python 3.10–3.13 上运行编译检查与单元测试；CI 使用固定脱敏样例，不会访问真实 Codex 账号。

## 项目结构

```text
codex_weekly_widget.py       # 悬浮窗和额度读取逻辑
test_codex_weekly_widget.py  # 单元测试
start_widget.bat             # 手动启动
install_startup.ps1          # 配置开机自启
SECURITY.md                  # 安全说明
CONTRIBUTING.md              # 贡献指南
CHANGELOG.md                 # 更新记录
```

## 许可证

本项目采用 [MIT License](LICENSE)。
