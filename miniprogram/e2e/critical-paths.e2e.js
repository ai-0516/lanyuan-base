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
    await page.waitFor(100);
  }
  return page.data();
}

beforeAll(async () => {
  ({ miniProgram, logs } = await launchMiniProgram());
  await miniProgram.mockWxMethod('login', { code: 'e2e-login-code', errMsg: 'login:ok' });
});

afterAll(async () => {
  if (miniProgram) await miniProgram.close();
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
  await miniProgram.callWxMethod('clearStorageSync');
  await mockRequest(miniProgram, {
    'POST /api/v1/auth/login': { statusCode: 200, data: { code: 0, data: { token: 'e2e-token', user: USER } } },
    'GET /api/v1/posts?page=1&size=20': { statusCode: 200, data: { code: 0, data: { items: [], total: 0 } } },
  });
  const login = await miniProgram.reLaunch('/pages/login/index');
  await login.setData({ checked: true, nickname: USER.nickname, avatar: USER.avatar });
  const button = await login.$('.wx-login-btn');
  await button.tap();

  await waitForPath(miniProgram, 'pages/feed/index');
  expect(await miniProgram.callWxMethod('getStorageSync', 'token')).toBe('e2e-token');
});

e2e('个人页展示资料并打开退出确认', async () => {
  await authenticate();
  await mockRequest(miniProgram, {
    'GET /api/v1/user/me': { statusCode: 200, data: { code: 0, data: USER } },
    'GET /api/v1/notifications/count': { statusCode: 200, data: { code: 0, data: { count: 2 } } },
  });
  await miniProgram.reLaunch('/pages/profile/index');
  const profile = await waitForPath(miniProgram, 'pages/profile/index');
  const loaded = await waitForData(profile, data => data.userInfo && data.userInfo.nickname);
  expect(loaded.userInfo.nickname).toBe(USER.nickname);
  await profile.callMethod('onLogout');
  expect((await profile.data()).showLogoutModal).toBe(true);
});

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
  await miniProgram.reLaunch('/pages/feed/index');
  const feed = await waitForPath(miniProgram, 'pages/feed/index');
  const loaded = await waitForData(feed, data => data.posts.length === 1);
  expect(loaded.posts).toHaveLength(1);

  await feed.callMethod('onPostLike', { detail: { postId: 101 } });
  await feed.waitFor(200);
  expect((await feed.data()).posts[0].liked).toBe(true);

  await feed.callMethod('onPostComment', { detail: { postId: 101 } });
  await feed.callMethod('onCommentInput', { detail: { value: '自动化评论' } });
  await feed.callMethod('sendComment');
  await feed.waitFor(200);
  expect((await feed.data()).posts[0].comments[0].content).toBe('自动化评论');

  const createButton = await feed.$('.fab');
  await createButton.tap();
  await waitForPath(miniProgram, 'pages/create-post/index');
});
