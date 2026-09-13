const ALLOWED_EXTENSIONS = new Set(['jpg', 'jpeg', 'png', 'gif', 'webp']);

function fileExtension(filePath) {
  const cleanPath = String(filePath || '').split(/[?#]/)[0];
  const match = cleanPath.match(/\.([a-zA-Z0-9]+)$/);
  const extension = match ? match[1].toLowerCase() : 'jpg';
  return ALLOWED_EXTENSIONS.has(extension) ? extension : 'jpg';
}

function createCloudPath(filePath) {
  const random = Math.random().toString(36).slice(2, 10);
  return `posts/${Date.now()}-${random}.${fileExtension(filePath)}`;
}

function uploadPostImage(filePath) {
  if (!wx.cloud || typeof wx.cloud.uploadFile !== 'function') {
    return Promise.reject(new Error('云存储不可用：请检查云环境配置后重试'));
  }
  return new Promise((resolve, reject) => {
    wx.cloud.uploadFile({
      cloudPath: createCloudPath(filePath),
      filePath,
      success: ({ fileID }) => {
        if (fileID) resolve(fileID);
        else reject(new Error('云存储未返回文件 ID'));
      },
      fail: reject,
    });
  });
}

function deleteCloudFiles(fileIDs) {
  if (!fileIDs?.length || !wx.cloud || typeof wx.cloud.deleteFile !== 'function') {
    return Promise.resolve();
  }
  return new Promise(resolve => {
    wx.cloud.deleteFile({
      fileList: fileIDs,
      success: () => resolve(),
      fail: () => resolve(),
    });
  });
}

async function uploadPostImages(filePaths) {
  const results = await Promise.all((filePaths || []).map(filePath =>
    uploadPostImage(filePath)
      .then(fileID => ({ fileID }))
      .catch(error => ({ error }))
  ));
  const uploadedFileIDs = results.filter(result => result.fileID).map(result => result.fileID);
  const failedResult = results.find(result => result.error);

  if (failedResult) {
    await deleteCloudFiles(uploadedFileIDs);
    throw failedResult.error;
  }
  return uploadedFileIDs;
}

module.exports = {
  createCloudPath,
  uploadPostImage,
  uploadPostImages,
  deleteCloudFiles,
};
