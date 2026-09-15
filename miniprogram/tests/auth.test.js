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

  test('redirects unauthenticated users and logout clears credentials', () => {
    const auth = loadAuth()
    expect(auth.checkLogin('/pages/create-post/index')).toBe(false)
    expect(wx.setStorageSync).toHaveBeenCalledWith('login_return_url', '/pages/create-post/index')
    expect(wx.navigateTo).toHaveBeenCalledWith({ url: '/pages/login/index' })

    wx.__storage.set('token', 'token')
    expect(auth.checkLogin()).toBe(true)

    auth.logout()
    expect(wx.removeStorageSync).toHaveBeenCalledWith('token')
    expect(wx.removeStorageSync).toHaveBeenCalledWith('user_info')
    expect(wx.switchTab).toHaveBeenLastCalledWith({ url: '/pages/feed/index' })
  })

  test('returns to the original target after login', () => {
    const auth = loadAuth()
    wx.__storage.set('login_return_url', '/pages/create-post/index')

    auth.returnAfterLogin()

    expect(wx.removeStorageSync).toHaveBeenCalledWith('login_return_url')
    expect(wx.reLaunch).toHaveBeenCalledWith({ url: '/pages/create-post/index' })
  })
})
