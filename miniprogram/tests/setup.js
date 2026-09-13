function createWxMock() {
  const storage = new Map()

  return {
    __storage: storage,
    getStorageSync: jest.fn(key => storage.get(key)),
    setStorageSync: jest.fn((key, value) => storage.set(key, value)),
    removeStorageSync: jest.fn(key => storage.delete(key)),
    request: jest.fn(),
    login: jest.fn(),
    getUserProfile: jest.fn(),
    getPrivacySetting: jest.fn(),
    openPrivacyContract: jest.fn(),
    getFileSystemManager: jest.fn(),
    reLaunch: jest.fn(),
    navigateTo: jest.fn(),
    navigateBack: jest.fn(),
    switchTab: jest.fn(),
    showToast: jest.fn(),
    showModal: jest.fn(),
    showActionSheet: jest.fn(),
    previewImage: jest.fn(),
  }
}

beforeEach(() => {
  global.wx = createWxMock()
})

afterEach(() => {
  delete global.Page
  delete global.Component
  jest.restoreAllMocks()
})
