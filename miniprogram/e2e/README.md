# 微信小程序 E2E 测试

测试通过 `miniprogram-automator` 驱动本机微信开发者工具，接口数据使用 `wx.request` mock，不依赖后端服务。

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
