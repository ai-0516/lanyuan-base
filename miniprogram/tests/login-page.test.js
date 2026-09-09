const path = require('path')
const { loadPage } = require('./helpers/load-page')

const mockRequest = jest.fn()
const mockAuth = {
  clearToken: jest.fn(),
  isLoggedIn: jest.fn(),
  setToken: jest.fn(),
  setUserInfo: jest.fn(),
}

jest.mock('../utils/request', () => ({ request: mockRequest }))
jest.mock('../utils/auth', () => mockAuth)

const pagePath = path.join(__dirname, '../pages/login/index.js')

describe('login page', () => {
  test('restores a cached profile without requesting user information', async () => {
    wx.__storage.set('lastProfile', { avatar: 'cached-avatar', nickname: 'cached-name' })
    const page = loadPage(pagePath)

    await page._tryAutoProfile()

    expect(page.data).toMatchObject({ avatar: 'cached-avatar', nickname: 'cached-name' })
    expect(wx.getUserProfile).not.toHaveBeenCalled()
  })

  test('loads and caches a WeChat profile', async () => {
    const page = loadPage(pagePath)
    wx.getUserProfile.mockImplementation(({ success }) => success({
      userInfo: { nickName: '微信用户', avatarUrl: '/tmp/avatar.jpg' },
    }))
    wx.getFileSystemManager.mockReturnValue({ readFileSync: jest.fn(() => 'base64-data') })

    await page._tryAutoProfile()

    expect(page.data).toMatchObject({
      avatar: 'data:image/jpeg;base64,base64-data',
      nickname: '微信用户',
    })
    expect(wx.setStorageSync).toHaveBeenCalledWith('lastProfile', {
      avatar: 'data:image/jpeg;base64,base64-data',
      nickname: '微信用户',
    })
  })

  test('updates and caches manually selected profile fields', () => {
    const page = loadPage(pagePath)
    page.data.nickname = '旧昵称'
    wx.getFileSystemManager.mockReturnValue({ readFileSync: jest.fn(() => 'png-data') })

    page.onChooseAvatar({ detail: { avatarUrl: '/tmp/avatar.png' } })
    page.onNicknameInput({ detail: { value: '新昵称' } })

    expect(page.data).toMatchObject({ avatar: 'data:image/png;base64,png-data', nickname: '新昵称' })
    expect(wx.setStorageSync).toHaveBeenLastCalledWith('lastProfile', {
      avatar: 'data:image/png;base64,png-data',
      nickname: '新昵称',
    })
  })

  test('requires an avatar and nickname before login', async () => {
    const page = loadPage(pagePath)
    await page.handleWxLogin()

    expect(wx.showToast).toHaveBeenCalledWith({ title: '请先设置头像和昵称', icon: 'none' })
    expect(wx.login).not.toHaveBeenCalled()
  })

  test('stores credentials and redirects after successful login', async () => {
    const page = loadPage(pagePath)
    page.data.avatar = 'data:image/jpeg;base64,avatar'
    page.data.nickname = ' 兰园用户 '
    wx.login.mockImplementation(({ success }) => success({ code: 'wx-code' }))
    mockRequest.mockResolvedValue({ token: 'token', user: { id: 1 } })

    await page.handleWxLogin()

    expect(mockRequest).toHaveBeenCalledWith({
      method: 'POST',
      url: '/auth/login',
      data: {
        code: 'wx-code',
        nickname: '兰园用户',
        avatar: 'data:image/jpeg;base64,avatar',
      },
    })
    expect(mockAuth.setToken).toHaveBeenCalledWith('token')
    expect(mockAuth.setUserInfo).toHaveBeenCalledWith({ id: 1 })
    expect(wx.reLaunch).toHaveBeenCalledWith({ url: '/pages/feed/index' })
    expect(page.data.logging).toBe(false)
  })

  test('shows an error and restores button state when login fails', async () => {
    jest.spyOn(console, 'error').mockImplementation(() => {})
    const page = loadPage(pagePath)
    page.data.avatar = 'avatar'
    page.data.nickname = '用户'
    wx.login.mockImplementation(({ success }) => success({ code: 'wx-code' }))
    mockRequest.mockRejectedValue(new Error('服务不可用'))

    await page.handleWxLogin()

    expect(wx.showToast).toHaveBeenCalledWith({
      title: '服务不可用',
      icon: 'none',
      duration: 2000,
    })
    expect(page.data.logging).toBe(false)
  })

  test('clears an invalid token during automatic login', async () => {
    const page = loadPage(pagePath)
    mockRequest.mockRejectedValue(new Error('unauthorized'))

    await page._autoLogin()

    expect(mockAuth.clearToken).toHaveBeenCalled()
    expect(page.data.checked).toBe(true)
  })

  test('redirects when automatic login succeeds', async () => {
    const page = loadPage(pagePath)
    mockRequest.mockResolvedValue({ valid: true })

    await page._autoLogin()

    expect(wx.reLaunch).toHaveBeenCalledWith({ url: '/pages/feed/index' })
    expect(mockAuth.clearToken).not.toHaveBeenCalled()
  })
})
