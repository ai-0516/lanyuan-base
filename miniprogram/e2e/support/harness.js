const fs = require('fs');
const path = require('path');
const automator = require('miniprogram-automator-next');

const PROJECT_PATH = path.resolve(__dirname, '../..');
const ARTIFACT_DIR = path.join(PROJECT_PATH, 'e2e-artifacts');
const DEFAULT_CLI_PATH = '/Applications/wechatwebdevtools-2.01.2510290.app/Contents/MacOS/cli';
const SCREENSHOT_INCOMPATIBLE_VERSIONS = new Set(['2.02.2607271']);

async function launchMiniProgram() {
  const cliPath = process.env.WECHAT_DEVTOOLS_CLI_PATH || DEFAULT_CLI_PATH;
  const wsEndpoint = process.env.WECHAT_DEVTOOLS_WS_ENDPOINT;
  if (!wsEndpoint && !fs.existsSync(cliPath)) {
    throw new Error(`WeChat DevTools CLI not found: ${cliPath}`);
  }
  const miniProgram = wsEndpoint
    ? await automator.connect({ wsEndpoint })
    : await automator.launch({
      cliPath,
      projectPath: PROJECT_PATH,
      trustProject: true,
      timeout: 60000,
    });
  const info = await miniProgram.raw.send('Tool.getInfo');
  if (SCREENSHOT_INCOMPATIBLE_VERSIONS.has(info.version)) {
    await miniProgram.teardown();
    throw new Error(
      `WeChat DevTools ${info.version} cannot return automator screenshots; ` +
      'use the tested 2.01.2510290 version or set WECHAT_DEVTOOLS_CLI_PATH to a compatible CLI'
    );
  }
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
    const page = miniProgram.page;
    const route = await page.route();
    if (route.route === expected) return page;
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  const page = miniProgram.page;
  const route = await page.route();
  throw new Error(`Expected page ${expected}, received ${route.route}`);
}

async function saveFailureArtifacts(miniProgram, logs, testName) {
  fs.mkdirSync(ARTIFACT_DIR, { recursive: true });
  const safeName = testName.replace(/[^a-zA-Z0-9_-]+/g, '-');
  let screenshotError = null;
  try {
    await miniProgram.saveScreenshot(path.join(ARTIFACT_DIR, `${safeName}.png`));
  } catch (error) {
    screenshotError = error.message;
  }
  // currentPage 在页面栈异常时可能抛错——若 artifacts 采集自身抛错会吞掉原始测试错误，
  // 因此页面状态采集整体 try/catch，失败只记录原因不中断。
  let pageInfo = {};
  try {
    const page = miniProgram.page;
    const route = await page.route();
    pageInfo = { path: route.route, options: route.options, data: await page.data() };
  } catch (error) {
    pageInfo = { error: `page unavailable: ${error.message}` };
  }
  fs.writeFileSync(path.join(ARTIFACT_DIR, `${safeName}.json`), JSON.stringify({
    ...pageInfo,
    logs,
    screenshotError,
  }, null, 2));
}

module.exports = { launchMiniProgram, mockRequest, saveFailureArtifacts, waitForPath };
