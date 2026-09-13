const DEFAULT_CONTRACT_NAME = '《小程序用户隐私保护指引》'

function getPrivacySetting() {
  if (typeof wx.getPrivacySetting !== 'function') {
    return Promise.resolve({
      needAuthorization: false,
      privacyContractName: DEFAULT_CONTRACT_NAME,
    })
  }

  return new Promise(resolve => {
    wx.getPrivacySetting({
      success: result => resolve({
        needAuthorization: !!result.needAuthorization,
        privacyContractName: result.privacyContractName || DEFAULT_CONTRACT_NAME,
      }),
      fail: () => resolve({
        needAuthorization: false,
        privacyContractName: DEFAULT_CONTRACT_NAME,
      }),
    })
  })
}

function openPrivacyContract() {
  if (typeof wx.openPrivacyContract !== 'function') {
    wx.showToast({ title: '当前微信版本暂不支持查看', icon: 'none' })
    return
  }

  wx.openPrivacyContract({
    fail: () => wx.showToast({ title: '隐私保护指引打开失败', icon: 'none' }),
  })
}

module.exports = {
  DEFAULT_CONTRACT_NAME,
  getPrivacySetting,
  openPrivacyContract,
}
