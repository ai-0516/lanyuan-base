const mockRequest = {
  post: jest.fn(),
}

jest.mock('../utils/request', () => mockRequest)

const loadAuth = () => {
  let auth
  jest.isolateModules(() => {
    auth = require('../utils/auth')
  })
  return auth
}

describe('utils/auth', () => {
  test('reads, writes, and clears credentials', () => {
    const auth = loadAuth()
    const user = { id: 7, nickname: '兰园用户' }

    expect(auth.isLoggedIn()).toBe(false)
    auth.setToken('token-7')
    auth.setUserInfo(user)
    expect(auth.isLoggedIn()).toBe(true)
    expect(auth.getToken()).toBe('token-7')
    expect(auth.getUserInfo()).toEqual(user)

    auth.clearToken()
    expect(auth.getToken()).toBe('')
    expect(auth.getUserInfo()).toBeNull()
  })

  test('logs in with an already-unwrapped request response', async () => {
    const auth = loadAuth()
    const result = { token: 'new-token', user: { id: 9 } }
    mockRequest.post.mockResolvedValue(result)

    await expect(auth.login('phone', { phone: '13800000000', password: 'secret' }))
      .resolves.toEqual(result)
    expect(mockRequest.post).toHaveBeenCalledWith(
      '/auth/login',
      { phone: '13800000000', password: 'secret' },
      { noAuth: true },
    )
    expect(wx.setStorageSync).toHaveBeenCalledWith('token', 'new-token')
    expect(wx.setStorageSync).toHaveBeenCalledWith('user_info', { id: 9 })
  })

  test('rejects unsupported login modes', async () => {
    const auth = loadAuth()
    await expect(auth.login('email', {})).rejects.toThrow('不支持的登录方式')
  })

  test('redirects unauthenticated users and logout clears credentials', () => {
    const auth = loadAuth()
    expect(auth.checkLogin()).toBe(false)
    expect(wx.reLaunch).toHaveBeenCalledWith({ url: '/pages/login/index' })

    wx.__storage.set('token', 'token')
    expect(auth.checkLogin()).toBe(true)

    auth.logout()
    expect(wx.removeStorageSync).toHaveBeenCalledWith('token')
    expect(wx.removeStorageSync).toHaveBeenCalledWith('user_info')
    expect(wx.reLaunch).toHaveBeenLastCalledWith({ url: '/pages/login/index' })
  })
})
