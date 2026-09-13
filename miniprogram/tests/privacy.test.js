const {
  DEFAULT_CONTRACT_NAME,
  getPrivacySetting,
  openPrivacyContract,
} = require('../utils/privacy')

describe('utils/privacy', () => {
  test('returns the pending official privacy contract', async () => {
    wx.getPrivacySetting.mockImplementation(({ success }) => success({
      needAuthorization: true,
      privacyContractName: '《兰园小程序用户隐私保护指引》',
    }))

    await expect(getPrivacySetting()).resolves.toEqual({
      needAuthorization: true,
      privacyContractName: '《兰园小程序用户隐私保护指引》',
    })
  })

  test('falls back safely when the API is unavailable', async () => {
    delete wx.getPrivacySetting

    await expect(getPrivacySetting()).resolves.toEqual({
      needAuthorization: false,
      privacyContractName: DEFAULT_CONTRACT_NAME,
    })
  })

  test('opens the contract managed by WeChat', () => {
    openPrivacyContract()

    expect(wx.openPrivacyContract).toHaveBeenCalled()
  })
})
