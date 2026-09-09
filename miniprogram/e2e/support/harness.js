const fs = require('fs');
const path = require('path');
const automator = require('miniprogram-automator');

const MiniProgram = require('miniprogram-automator/out/MiniProgram').default;

const PROJECT_PATH = path.resolve(__dirname, '../..');
const ARTIFACT_DIR = path.join(PROJECT_PATH, 'e2e-artifacts');
const DEFAULT_CLI_PATH = '/Applications/wechatwebdevtools.app/Contents/MacOS/cli';

// DevTools 2.02.2607271 renamed Tool.getInfo.SDKVersion to version. Automator
// 0.12.1 dereferences the missing field before a session can start. Accept the
// new field while retaining the original SDK-version check when it is present.
const originalCheckVersion = MiniProgram.prototype.checkVersion;
MiniProgram.prototype.checkVersion = async function checkVersionCompat() {
  const info = await this.send('Tool.getInfo');
  if (info.SDKVersion) return originalCheckVersion.call(this);
  if (!info.version) throw new Error('DevTools did not return version information');
};

async function launchMiniProgram() {
  const cliPath = process.env.WECHAT_DEVTOOLS_CLI_PATH || DEFAULT_CLI_PATH;
  if (!fs.existsSync(cliPath)) {
    throw new Error(`WeChat DevTools CLI not found: ${cliPath}`);
  }
  const miniProgram = await automator.launch({
    cliPath,
    projectPath: PROJECT_PATH,
    trustProject: true,
    timeout: 60000,
  });
  const logs = [];
  miniProgram.on('console', log => logs.push({ type: 'console', log }));
  miniProgram.on('exception', error => logs.push({ type: 'exception', error }));
  return { miniProgram, logs };
}

async function mockRequest(miniProgram, routes) {
  await miniProgram.mockWxMethod('request', function requestMock(options, routeTable) {
    const pathAndQuery = options.url.replace(/^https?:\/\/[^/]+/, '');
    const pathname = pathAndQuery.split('?')[0];
    const method = (options.method || 'GET').toUpperCase();
    const key = `${method} ${pathAndQuery}`;
    const matched = routeTable[key] || routeTable[`${method} ${pathname}`];
    if (matched) return matched;
    // 未命中 mock 的请求静默 404 会隐藏页面发出了预期外请求（测试仍绿），
    // 打 warn 日志（harness 已收集 console 日志，失败 artifact 中可查）便于排障。
    console.warn(`[e2e] Unmocked wx.request: ${key}`);
    return {
      statusCode: 404,
      data: { message: `Unmocked request: ${key}` },
    };
  }, routes);
}

async function waitForPath(miniProgram, expected, timeout = 10000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const page = await miniProgram.currentPage();
    if (page.path === expected) return page;
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  const page = await miniProgram.currentPage();
  throw new Error(`Expected page ${expected}, received ${page.path}`);
}

async function saveFailureArtifacts(miniProgram, logs, testName) {
  fs.mkdirSync(ARTIFACT_DIR, { recursive: true });
  const safeName = testName.replace(/[^a-zA-Z0-9_-]+/g, '-');
  const page = await miniProgram.currentPage();
  let screenshotError = null;
  try {
    await miniProgram.screenshot({ path: path.join(ARTIFACT_DIR, `${safeName}.png`) });
  } catch (error) {
    screenshotError = error.message;
  }
  fs.writeFileSync(path.join(ARTIFACT_DIR, `${safeName}.json`), JSON.stringify({
    path: page.path,
    data: await page.data(),
    logs,
    screenshotError,
  }, null, 2));
}

module.exports = { launchMiniProgram, mockRequest, saveFailureArtifacts, waitForPath };
