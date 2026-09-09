# 微信小程序 E2E 测试

测试通过 `miniprogram-automator-next` 兼容层驱动本机微信开发者工具，底层仍使用官方 `miniprogram-automator`；接口数据使用 `wx.request` mock，不依赖后端服务。

## 为什么使用 automator-next

实测发现微信开发者工具与官方自动化 SDK 存在无法同时满足页面操作和失败截图的版本兼容问题：

- `2.02.2607271` 的页面 API 可用，但 `screenshot()` 报 `readFile:fail http://store/... not found`。
- `2.01.2510290` 的截图正常，但官方 `Page.*` / `Element.*` 协议无响应。

`miniprogram-automator-next` 保留官方 SDK 的启动、`wx` mock、存储和截图能力，仅通过仍可用的 `App.evaluate` 重建页面数据、元素查询与交互。因此本项目固定使用已实际验证的 `2.01.2510290`，同时获得页面操作和失败截图能力。

兼容层的交互通过调用 WXML 绑定的页面 handler 实现，并非原生 `Element.tap()`；所以 E2E 使用稳定的 `e2e-*` 元素 ID，并在测试中明确指定 handler。升级开发者工具或自动化 SDK 时，应先重新验证完整 E2E 和失败截图，再移除该兼容层。

## 本地运行

1. 安装并登录微信开发者工具。
2. 在“设置 → 安全设置”中开启“服务端口（CLI/HTTP 调用）”。
3. 执行 `npm run test:e2e`。

默认 CLI 路径为已验证兼容的 `/Applications/wechatwebdevtools-2.01.2510290.app/Contents/MacOS/cli`。其他安装位置可通过 `WECHAT_DEVTOOLS_CLI_PATH` 指定。

页面自动化与截图能力已通过 `miniprogram-automator-next` 在微信开发者工具 `2.01.2510290` 验证；`2.02.2607271` 的自动化截图协议会报 `readFile:fail http://store/... not found`，harness 会拒绝该已知不兼容版本。并行安装兼容版后可执行：

```bash
WECHAT_DEVTOOLS_CLI_PATH=/Applications/wechatwebdevtools-2.01.2510290.app/Contents/MacOS/cli npm run test:e2e
```

若已用 CLI 的 `auto --auto-port 9420` 打开自动化会话，可直接连接，便于排查 CLI 启动问题：

```bash
WECHAT_DEVTOOLS_WS_ENDPOINT=ws://127.0.0.1:9420 npm run test:e2e
```

测试失败时，截图、页面数据与控制台日志写入 `e2e-artifacts/`。该目录不会提交到 Git。

这组测试依赖桌面版微信开发者工具，因此不放入现有 Linux GitHub Actions；后续接入 macOS runner 时可直接调用同一条 npm script。
