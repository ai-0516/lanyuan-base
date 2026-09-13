const {
  createCloudPath,
  uploadPostImage,
  uploadPostImages,
  deleteCloudFiles,
} = require('../utils/cloud-storage');
const { fullUrl } = require('../utils/constants');

describe('utils/cloud-storage', () => {
  test('creates a private, extension-preserving post path', () => {
    jest.spyOn(Date, 'now').mockReturnValue(123456789);
    jest.spyOn(Math, 'random').mockReturnValue(0.5);

    expect(createCloudPath('wxfile://tmp/photo.PNG?x=1'))
      .toMatch(/^posts\/123456789-[a-z0-9]+\.png$/);
    expect(createCloudPath('wxfile://tmp/no-extension'))
      .toMatch(/\.jpg$/);
  });

  test('keeps cloud file IDs unchanged for image components', () => {
    expect(fullUrl('cloud://test-env/posts/a.jpg'))
      .toBe('cloud://test-env/posts/a.jpg');
  });

  test('uploads images and returns cloud file IDs', async () => {
    wx.cloud = {
      uploadFile: jest.fn(({ cloudPath, success }) => {
        success({ fileID: `cloud://test-env.${cloudPath}` });
      }),
    };

    await expect(uploadPostImages(['a.jpg', 'b.webp'])).resolves.toEqual([
      expect.stringMatching(/^cloud:\/\/test-env\.posts\//),
      expect.stringMatching(/^cloud:\/\/test-env\.posts\//),
    ]);
    expect(wx.cloud.uploadFile).toHaveBeenCalledTimes(2);
  });

  test('removes successful uploads when another image fails', async () => {
    wx.cloud = {
      uploadFile: jest.fn(({ filePath, success, fail }) => {
        if (filePath === 'broken.jpg') fail(new Error('upload failed'));
        else success({ fileID: 'cloud://test-env/posts/uploaded.jpg' });
      }),
      deleteFile: jest.fn(({ success }) => success()),
    };

    await expect(uploadPostImages(['uploaded.jpg', 'broken.jpg']))
      .rejects.toThrow('upload failed');
    expect(wx.cloud.deleteFile).toHaveBeenCalledWith(expect.objectContaining({
      fileList: ['cloud://test-env/posts/uploaded.jpg'],
    }));
  });

  test('rejects missing cloud capability and missing file ID', async () => {
    await expect(uploadPostImage('a.jpg')).rejects.toThrow('云存储不可用');

    wx.cloud = { uploadFile: jest.fn(({ success }) => success({})) };
    await expect(uploadPostImage('a.jpg')).rejects.toThrow('未返回文件 ID');
  });

  test('best-effort deletion never masks the publishing error', async () => {
    wx.cloud = { deleteFile: jest.fn(({ fail }) => fail(new Error('offline'))) };
    await expect(deleteCloudFiles(['cloud://test/a.jpg'])).resolves.toBeUndefined();
    expect(wx.cloud.deleteFile).toHaveBeenCalledWith(expect.objectContaining({
      fileList: ['cloud://test/a.jpg'],
    }));
  });
});
