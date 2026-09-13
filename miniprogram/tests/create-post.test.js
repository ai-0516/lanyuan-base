const path = require('path');
const { loadPage } = require('./helpers/load-page');
const { request } = require('../utils/request');
const { uploadPostImages, deleteCloudFiles } = require('../utils/cloud-storage');

jest.mock('../utils/request', () => ({ request: jest.fn() }));
jest.mock('../utils/cloud-storage', () => ({
  uploadPostImages: jest.fn(),
  deleteCloudFiles: jest.fn(() => Promise.resolve()),
}));

const pagePath = path.join(__dirname, '../pages/create-post/index.js');

describe('create post cloud images', () => {
  beforeEach(() => {
    jest.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  test('publishes cloud file IDs instead of backend upload URLs', async () => {
    jest.useFakeTimers();
    uploadPostImages.mockResolvedValue(['cloud://env/posts/a.jpg']);
    request.mockResolvedValue({ id: 1 });
    const page = loadPage(pagePath);
    Object.assign(page.data, {
      content: '带图片的帖子',
      tempImages: ['wxfile://tmp/a.jpg'],
      canPublish: true,
    });

    await page.onPublish();

    expect(uploadPostImages).toHaveBeenCalledWith(['wxfile://tmp/a.jpg']);
    expect(request).toHaveBeenCalledWith({
      method: 'POST',
      url: '/posts',
      data: { content: '带图片的帖子', images: ['cloud://env/posts/a.jpg'] },
    });
    expect(deleteCloudFiles).not.toHaveBeenCalled();
    jest.runOnlyPendingTimers();
    expect(wx.switchTab).toHaveBeenCalledWith({ url: '/pages/feed/index' });
  });

  test('removes newly uploaded files when post creation fails', async () => {
    uploadPostImages.mockResolvedValue(['cloud://env/posts/orphan.jpg']);
    request.mockRejectedValue(new Error('post failed'));
    const page = loadPage(pagePath);
    Object.assign(page.data, {
      content: '失败帖子',
      tempImages: ['wxfile://tmp/a.jpg'],
      canPublish: true,
    });

    await page.onPublish();

    expect(deleteCloudFiles).toHaveBeenCalledWith(['cloud://env/posts/orphan.jpg']);
    expect(wx.showToast).toHaveBeenCalledWith({ title: '发布失败', icon: 'error' });
  });
});
