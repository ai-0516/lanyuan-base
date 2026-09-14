const path = require('path');
const { loadPage } = require('./helpers/load-page');
const { request } = require('../utils/request');
const { uploadPostImages, getTempFileURLs, deleteCloudFiles } = require('../utils/cloud-storage');

jest.mock('../utils/request', () => ({ request: jest.fn() }));
jest.mock('../utils/cloud-storage', () => ({
  uploadPostImages: jest.fn(),
  getTempFileURLs: jest.fn(() => Promise.resolve([])),
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
    getTempFileURLs.mockResolvedValue(['https://test.tcb.qcloud.la/posts/a.jpg']);
    request.mockResolvedValue({ id: 1, moderation_status: 'pending' });
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
      data: {
        content: '带图片的帖子',
        images: ['cloud://env/posts/a.jpg'],
        image_urls: ['https://test.tcb.qcloud.la/posts/a.jpg'],
      },
    });
    expect(wx.showToast).toHaveBeenCalledWith({
      title: '图片审核中，通过后将自动发布',
      icon: 'none',
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

  test('shows only the unified unsafe-content message', async () => {
    request.mockRejectedValue({
      data: { code: 40010, message: '发布内容含有违规信息，请修改后重试' },
    });
    const page = loadPage(pagePath);
    Object.assign(page.data, {
      content: '违规内容',
      tempImages: [],
      canPublish: true,
    });

    await page.onPublish();

    expect(wx.showToast).toHaveBeenCalledWith({
      title: '发布内容含有违规信息，请修改后重试',
      icon: 'none',
    });
  });

  test('shows the image submit param message', async () => {
    uploadPostImages.mockResolvedValue(['cloud://env/posts/a.jpg']);
    getTempFileURLs.mockResolvedValue(['https://test.tcb.qcloud.la/posts/a.jpg']);
    request.mockRejectedValue({
      data: { code: 40014, message: '图片送检参数有误，请重新选择图片后发布' },
    });
    const page = loadPage(pagePath);
    Object.assign(page.data, {
      content: '图片参数错误',
      tempImages: ['wxfile://tmp/a.jpg'],
      canPublish: true,
    });

    await page.onPublish();

    expect(deleteCloudFiles).toHaveBeenCalledWith(['cloud://env/posts/a.jpg']);
    expect(wx.showToast).toHaveBeenCalledWith({
      title: '图片送检参数有误，请重新选择图片后发布',
      icon: 'none',
    });
  });
});
