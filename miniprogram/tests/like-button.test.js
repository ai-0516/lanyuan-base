const path = require('path')
const { loadComponent } = require('./helpers/load-component')

const mockRequest = jest.fn()
jest.mock('../utils/request', () => ({ request: mockRequest }))

const componentPath = path.join(__dirname, '../components/like-button/index.js')
const flushPromises = () => new Promise(resolve => setTimeout(resolve, 0))

describe('like-button component behavior', () => {
  test('does nothing without a post id', () => {
    const component = loadComponent(componentPath)
    component.onToggle()
    expect(mockRequest).not.toHaveBeenCalled()
  })

  test('likes a post and emits the updated state', async () => {
    mockRequest.mockResolvedValue({ liked: true, likeCount: 3 })
    const component = loadComponent(componentPath, { postId: 8, liked: false, count: 2 })

    component.onToggle()
    await flushPromises()

    expect(mockRequest).toHaveBeenCalledWith({ url: '/posts/8/like', method: 'POST' })
    expect(component.data).toMatchObject({ liked: true, count: 3 })
    expect(component.triggerEvent).toHaveBeenCalledWith('change', { liked: true, count: 3 })
  })

  test('keeps a valid zero count when unliking', async () => {
    mockRequest.mockResolvedValue({ liked: false, likeCount: 0 })
    const component = loadComponent(componentPath, { postId: 8, liked: true, count: 1 })

    component.onToggle()
    await flushPromises()

    expect(mockRequest).toHaveBeenCalledWith({ url: '/posts/8/like', method: 'DELETE' })
    expect(component.data.count).toBe(0)
  })

  test('leaves local state unchanged when the request fails', async () => {
    mockRequest.mockRejectedValue(new Error('offline'))
    const component = loadComponent(componentPath, { postId: 8, liked: false, count: 2 })

    component.onToggle()
    await flushPromises()

    expect(component.data).toMatchObject({ liked: false, count: 2 })
    expect(component.triggerEvent).not.toHaveBeenCalled()
  })
})
