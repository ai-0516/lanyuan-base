/**
 * 兰园公共底座 — 全局入口
 *
 * App() 生命周期：
 *   - onLaunch: 检查登录态、获取设备信息、静默登录（微信模式）
 *   - onShow: 同步登录态变化
 *   - onHide: 清理不需要的临时数据
 */

const auth = require('./utils/auth')
const http = require('./utils/request')

App({
  /** 全局共享数据 */
  globalData: {
    /** 当前用户信息（登录后设置） */
    userInfo: null,
    /** 是否已登录 */
    isLoggedIn: false,
    /** 设备信息（onLaunch 时获取） */
    systemInfo: null,
    /** API 基础地址（可在运行时切换，如用户设置了自建服务器） */
    baseUrl: '',
  },

  /**
   * 小程序初始化
   */
  onLaunch() {
    const self = this

    // 获取设备信息
    try {
      const sysInfo = wx.getSystemInfoSync()
      self.globalData.systemInfo = sysInfo
    } catch (e) {
      console.warn('[App] 获取设备信息失败:', e)
    }

    // 检查本地登录态
    if (auth.isLoggedIn()) {
      self.globalData.isLoggedIn = true
      self.globalData.userInfo = auth.getUserInfo()

      // 后台校验 Token 是否仍然有效
      self._verifyToken()
    } else {
      self.globalData.isLoggedIn = false
      self.globalData.userInfo = null
    }

    // 云开发 / 其他三方 SDK 初始化可在此处补充
  },

  /**
   * 小程序从后台进入前台
   */
  onShow() {
    // 重新检查登录态（可能在其他页面登出后返回）
    const logged = auth.isLoggedIn()
    if (logged !== this.globalData.isLoggedIn) {
      this.globalData.isLoggedIn = logged
      this.globalData.userInfo = logged ? auth.getUserInfo() : null
    }
  },

  /**
   * 小程序从前台进入后台
   */
  onHide() {
    // 可在此处释放非关键资源
  },

  /**
   * 后台校验 Token 有效性
   * 调用 /auth/verify 接口，若失败则清除登录态
   */
  async _verifyToken() {
    try {
      const data = await http.get('/auth/check')
      if (data && data.valid) {
        // Token 有效，刷新用户信息缓存
        auth.setUserInfo(data.user || data)
        this.globalData.userInfo = data.user || data
      } else {
        // Token 无效
        this._handleTokenInvalid()
      }
    } catch {
      // 网络异常时不做处理，保留本地登录态
      console.warn('[App] Token 校验因网络异常跳过')
    }
  },

  /**
   * Token 无效处理
   */
  _handleTokenInvalid() {
    auth.clearToken()
    this.globalData.isLoggedIn = false
    this.globalData.userInfo = null
  },

  /**
   * 全局登录完成回调（各页面调用）
   * @param {object} userInfo
   */
  onLoginSuccess(userInfo) {
    this.globalData.isLoggedIn = true
    this.globalData.userInfo = userInfo
    auth.setUserInfo(userInfo)
  },

  /**
   * 全局登出回调
   */
  onLogout() {
    auth.clearToken()
    this.globalData.isLoggedIn = false
    this.globalData.userInfo = null
  },
})
