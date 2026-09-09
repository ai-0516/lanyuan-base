const path = require('path')
const { loadComponent } = require('./helpers/load-component')

const mockRequest = jest.fn()
jest.mock('../utils/request', () => ({ request: mockRequest }))

const componentPath = path.join(__dirname, '../components/comment-sheet/index.js')
const flushPromises = () => new Promise(resolve => setTimeout(resolve, 0))

describe('comment-sheet component behavior', () => {
  test('stores the unwrapped comments response', async () => {
    const comments = [{ id: 42, content: '评论内容' }]
    mockRequest.mockResolvedValue(comments)
    const component = loadComponent(componentPath, { postId: 8 })

    component.fetchComments()
    await flushPromises()

    expect(mockRequest).toHaveBeenCalledWith({ url: '/posts/8/comments', method: 'GET' })
    expect(component.data.comments).toEqual(comments)
  })

  test('sends the parent comment id with the backend field name', async () => {
    mockRequest
      .mockResolvedValueOnce({ id: 99 })
      .mockResolvedValueOnce([])
    const component = loadComponent(componentPath, {
      postId: 8,
      inputValue: ' 回复内容 ',
    })

    component.onReplyTap({
      currentTarget: {
        dataset: {
          commentId: 42,
          user: { id: 7, nickname: '被回复用户' },
        },
      },
    })
    component.onSendTap()
    await flushPromises()

    expect(mockRequest).toHaveBeenNthCalledWith(1, {
      url: '/posts/8/comments',
      method: 'POST',
      data: { content: '回复内容', parent_comment_id: 42 },
    })
    expect(component.data.inputValue).toBe('')
    expect(component.triggerEvent).toHaveBeenCalledWith('refresh')
  })

  test('does not send an empty comment', () => {
    const component = loadComponent(componentPath, { postId: 8, inputValue: '   ' })
    component.onSendTap()
    expect(mockRequest).not.toHaveBeenCalled()
  })
})
