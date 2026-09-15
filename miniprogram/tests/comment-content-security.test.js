const path = require('path');
const { loadPage } = require('./helpers/load-page');
const { request } = require('../utils/request');

jest.mock('../utils/request', () => ({ request: jest.fn() }));
jest.mock('../utils/constants', () => ({ fullUrl: value => value }));
jest.mock('../utils/auth', () => ({ getUserInfo: jest.fn(() => ({ id: 1, nickname: '我' })) }));

const unsafeResponse = {
  data: { code: 40010, message: '发布内容含有违规信息，请修改后重试' },
};

describe.each([
  {
    name: 'feed comment sheet',
    pagePath: path.join(__dirname, '../pages/feed/index.js'),
    setup(page) {
      Object.assign(page.data, {
        commentText: '违规评论',
        canSend: true,
        commentSheetPostId: 7,
      });
    },
  },
  {
    name: 'post detail comment sheet',
    pagePath: path.join(__dirname, '../pages/post-detail/index.js'),
    setup(page) {
      page.postId = 7;
      Object.assign(page.data, { commentText: '违规评论', canSend: true });
    },
  },
])('$name', ({ pagePath, setup }) => {
  test('shows only the unified unsafe-content message', async () => {
    jest.spyOn(console, 'error').mockImplementation(() => {});
    request.mockRejectedValue(unsafeResponse);
    const page = loadPage(pagePath);
    setup(page);

    await page.sendComment();

    expect(wx.showToast).toHaveBeenCalledWith({
      title: '发布内容含有违规信息，请修改后重试',
      icon: 'none',
    });
  });
});
