const loadRequest = () => {
  let http
  jest.isolateModules(() => {
    http = require('../utils/request')
  })
  return http
}

describe('utils/request', () => {
  test('sends a local request with token and unwraps a successful envelope', async () => {
    wx.__storage.set('token', 'test-token')
    const http = loadRequest()

    const pending = http.get('/posts', { 'X-Trace': 'trace-id' })
    expect(wx.request).toHaveBeenCalledWith(expect.objectContaining({
      url: 'http://localhost:8000/api/v1/posts',
      method: 'GET',
      header: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer test-token',
        'X-Trace': 'trace-id',
      },
    }))

    const options = wx.request.mock.calls[0][0]
    options.success({ statusCode: 200, data: { code: 0, data: { id: 1 } } })

    await expect(pending).resolves.toEqual({ id: 1 })
  })

  test('keeps absolute URLs and responses without the common envelope', async () => {
    const { request } = loadRequest()
    const pending = request({ url: 'https://example.test/health', method: 'POST', data: { ok: true } })
    const options = wx.request.mock.calls[0][0]

    expect(options.url).toBe('https://example.test/health')
    expect(options.data).toEqual({ ok: true })
    options.success({ statusCode: 201, data: { status: 'ok' } })

    await expect(pending).resolves.toEqual({ status: 'ok' })
  })

  test('rejects non-2xx responses and transport failures', async () => {
    const { request } = loadRequest()
    const httpFailure = request('GET', '/missing')
    wx.request.mock.calls[0][0].success({ statusCode: 404, data: { message: 'missing' } })
    await expect(httpFailure).rejects.toMatchObject({ statusCode: 404 })

    const transportFailure = request('GET', '/offline')
    wx.request.mock.calls[1][0].fail(new Error('offline'))
    await expect(transportFailure).rejects.toThrow('offline')
  })

  test('returns a readable error when cloud capability is unavailable', async () => {
    jest.doMock('../utils/constants', () => ({
      BASE_URL: 'http://localhost:8000/api/v1',
      CLOUD_CONFIG: { ENV: 'test-env', SERVICE: 'test-service' },
      USE_CLOUD: true,
    }))
    const { request } = loadRequest()

    await expect(request('GET', '/posts')).rejects.toThrow('云能力不可用')
    expect(wx.request).not.toHaveBeenCalled()

    jest.dontMock('../utils/constants')
  })
})
