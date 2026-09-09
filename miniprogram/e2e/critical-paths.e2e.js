const {
  launchMiniProgram,
  mockRequest,
  saveFailureArtifacts,
  waitForPath,
} = require('./support/harness');

const USER = { id: 7, nickname: 'E2E 用户', avatar: 'data:image/png;base64,eA==' };
let miniProgram;
let logs;

async function authenticate() {
  await miniProgram.callWxMethod('setStorageSync', 'token', 'e2e-token');
  await miniProgram.callWxMethod('setStorageSync', 'user_info', USER);
}

async function waitForData(page, predicate, timeout = 5000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const data = await page.data();
    if (predicate(data)) return data;
    await page.wait(100);
  }
  return page.data();
}

beforeAll(async () => {
  ({ miniProgram, logs } = await launchMiniProgram());
  await miniProgram.mockWxMethod('login', { code: 'e2e-login-code', errMsg: 'login:ok' });
});

beforeEach(async () => {
  await miniProgram.callWxMethod('clearStorageSync');
});

afterAll(async () => {
  if (miniProgram) await miniProgram.teardown();
});

function e2e(name, run) {
  test(name, async () => {
    try {
      await run();
    } catch (error) {
      await saveFailureArtifacts(miniProgram, logs, name);
      throw error;
    }
  });
}

e2e('登录成功后保存登录态并进入发现页', async () => {
  await mockRequest(miniProgram, {
    'POST /api/v1/auth/login': { statusCode: 200, data: { code: 0, data: { token: 'e2e-token', user: USER } } },
    'GET /api/v1/posts?page=1&size=20': { statusCode: 200, data: { code: 0, data: { items: [], total: 0 } } },
  });
  const login = await miniProgram.open('/pages/login/index');
  await login.setData({ checked: true, nickname: USER.nickname, avatar: USER.avatar });
  await login.tap('#e2e-login-submit', 'handleWxLogin');

  await waitForPath(miniProgram, 'pages/feed/index');
  expect(await miniProgram.callWxMethod('getStorageSync', 'token')).toBe('e2e-token');
});

e2e('个人页展示资料并打开退出确认', async () => {
  await authenticate();
  await mockRequest(miniProgram, {
    'GET /api/v1/user/me': { statusCode: 200, data: { code: 0, data: USER } },
    'GET /api/v1/notifications/count': { statusCode: 200, data: { code: 0, data: { count: 2 } } },
  });
  await miniProgram.open('/pages/profile/index');
  const profile = await waitForPath(miniProgram, 'pages/profile/index');
  const loaded = await waitForData(profile, data => data.userInfo && data.userInfo.nickname);
  expect(loaded.userInfo.nickname).toBe(USER.nickname);
  await profile.callMethod('onLogout');
  expect((await profile.data()).showLogoutModal).toBe(true);
});

// Issue #108 验收「核心成功、鉴权失败和服务异常分支有断言」：
// 下面两条分别覆盖登录主流程的 Token 失效（401）与社区流程的服务异常（500）分支。

e2e('登录态 Token 失效（401）时清除登录态并停留登录页', async () => {
  await authenticate();
  // 前提：带 token 进入登录页才会走 _autoLogin 的 /auth/check 校验分支
  expect(await miniProgram.callWxMethod('getStorageSync', 'token')).toBe('e2e-token');
  await mockRequest(miniProgram, {
    // /auth/check 401 → _autoLogin catch → clearToken 并回落登录表单
    // （成功路径会 reLaunch 到 feed，页面不可能停在登录页，断言可区分分支）
    'GET /api/v1/auth/check': { statusCode: 401, data: { code: 1002, message: 'Token 已失效' } },
  });
  await miniProgram.open('/pages/login/index');
  const login = await waitForPath(miniProgram, 'pages/login/index');
  // checked 置 true 仅发生在校验失败回落后（未登录直入时也会置 true，故先断言了 token 前提）
  const checked = await waitForData(login, data => data.checked === true);
  expect(checked.checked).toBe(true);
  expect(await miniProgram.callWxMethod('getStorageSync', 'token')).toBe('');
  expect(await miniProgram.callWxMethod('getStorageSync', 'user_info')).toBe('');
});

e2e('服务异常（500）时 Feed 加载失败并优雅降级', async () => {
  await authenticate();
  await mockRequest(miniProgram, {
    // posts 500 → request reject → loadPosts catch → loading 复位
    // （成功路径会填充 posts 使长度不为 0；请求未发出则 loading 恒为 true → 轮询超时失败）
    'GET /api/v1/posts?page=1&size=20': { statusCode: 500, data: { code: 5000, message: '服务器错误' } },
  });
  await miniProgram.open('/pages/feed/index');
  const feed = await waitForPath(miniProgram, 'pages/feed/index');
  const settled = await waitForData(feed, data => data.loading === false && data.posts.length === 0);
  expect(settled.loading).toBe(false);
  expect(settled.posts).toHaveLength(0);
});

// 进入非 Tab 发布页后，当前 DevTools 的自动化协议不稳定；将该终止性导航
// 放在最后，避免页面栈影响后续用例。各用例的 storage 仍由 beforeEach 清空。
e2e('发现页展示帖子，支持点赞、评论并进入发布页', async () => {
  await authenticate();
  await mockRequest(miniProgram, {
    'GET /api/v1/posts?page=1&size=20': {
      statusCode: 200,
      data: { code: 0, data: { items: [{
        id: 101,
        content: 'E2E 帖子',
        created_at: new Date().toISOString(),
        user: USER,
        images: [],
        likers: [],
        comments: [],
        liked: false,
      }], total: 1 } },
    },
    'POST /api/v1/posts/101/like': { statusCode: 200, data: { code: 0, data: { liked: true } } },
    'POST /api/v1/posts/101/comments': { statusCode: 200, data: { code: 0, data: { id: 501 } } },
  });
  await miniProgram.open('/pages/feed/index');
  const feed = await waitForPath(miniProgram, 'pages/feed/index');
  const loaded = await waitForData(feed, data => data.posts.length === 1);
  expect(loaded.posts).toHaveLength(1);

  await feed.callMethod('onPostLike', { detail: { postId: 101 } });
  const liked = await waitForData(feed, data => data.posts[0]?.liked === true);
  expect(liked.posts[0].liked).toBe(true);

  await feed.callMethod('onPostComment', { detail: { postId: 101 } });
  await feed.callMethod('onCommentInput', { detail: { value: '自动化评论' } });
  await feed.callMethod('sendComment');
  const commented = await waitForData(feed, data => data.posts[0]?.comments[0]?.content === '自动化评论');
  expect(commented.posts[0].comments[0].content).toBe('自动化评论');

  await feed.tap('#e2e-create-post-entry', 'goToCreatePost');
  await waitForPath(miniProgram, 'pages/create-post/index');
});
