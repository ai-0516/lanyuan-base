const path = require('path')
const simulate = require('miniprogram-simulate')

describe('chat-bubble component', () => {
  test('renders message content and role-specific classes', () => {
    const componentId = simulate.load(path.join(__dirname, '../components/chat-bubble/index'))
    const component = simulate.render(componentId, {
      role: 'assistant',
      content: '你好，兰园',
      time: '12:00',
    })
    const parent = document.createElement('parent-wrapper')
    component.attach(parent)

    expect(component.querySelector('.bubble-text').dom.textContent).toBe('你好，兰园')
    expect(component.querySelector('.bubble-time').dom.textContent).toBe('12:00')
    expect(component.querySelector('.chat-bubble-wrapper').dom.className).toContain('assistant')

    component.detach()
  })
})
