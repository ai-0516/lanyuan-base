const path = require('path');
const { loadPage } = require('./helpers/load-page');

jest.mock('../utils/request', () => ({ request: jest.fn() }));

const pagePath = path.join(__dirname, '../pages/ai-chat/index.js');

describe('AI chat viewport behavior', () => {
  beforeEach(() => {
    global.getApp = () => ({ towxml: jest.fn(() => []) });
  });

  afterEach(() => {
    delete global.getApp;
    jest.useRealTimers();
  });

  test('alternates static anchors to reissue native bottom positioning', () => {
    jest.useFakeTimers();
    const page = loadPage(pagePath);

    page.scrollToBottom();
    jest.runOnlyPendingTimers();
    expect(page.data.lastMsgId).toBe('msg-end-a');

    page.scrollToBottom();
    jest.runOnlyPendingTimers();
    expect(page.data.lastMsgId).toBe('msg-end-b');

    page.scrollToBottom();
    jest.runOnlyPendingTimers();
    expect(page.data.lastMsgId).toBe('msg-end-a');
  });

  test('polls native bottom positioning throughout streaming and stops after the turn', () => {
    jest.useFakeTimers();
    const page = loadPage(pagePath);
    page.scrollToBottom = jest.fn();

    page.startAutoScroll();
    expect(page.scrollToBottom).toHaveBeenCalledTimes(1);
    jest.advanceTimersByTime(150);
    expect(page.scrollToBottom).toHaveBeenCalledTimes(4);

    page.stopAutoScroll();
    expect(page.scrollToBottom).toHaveBeenCalledTimes(5);
    jest.advanceTimersByTime(150);
    expect(page.scrollToBottom).toHaveBeenCalledTimes(5);
  });

  test('enables the local stream mock only for develop builds', () => {
    const page = loadPage(pagePath);
    wx.setStorageSync('debugMockAiStream', true);
    wx.getAccountInfoSync = jest.fn(() => ({
      miniProgram: { envVersion: 'develop' },
    }));
    expect(page.isMockStreamEnabled()).toBe(true);

    wx.getAccountInfoSync.mockReturnValue({
      miniProgram: { envVersion: 'trial' },
    });
    expect(page.isMockStreamEnabled()).toBe(false);
  });

  test('mock stream follows the production event-dispatch path', () => {
    jest.useFakeTimers();
    const page = loadPage(pagePath);
    page.dispatchEvent = jest.fn();

    page.streamMockReply('Hi');
    jest.runAllTimers();

    expect(page.dispatchEvent).toHaveBeenNthCalledWith(1, 'turn/start', {});
    expect(page.dispatchEvent).toHaveBeenNthCalledWith(2, 'step/start', {});
    expect(page.dispatchEvent).toHaveBeenNthCalledWith(3, 'user/message', {
      content: [{ type: 'text', text: 'Hi' }],
    });
    expect(page.dispatchEvent).toHaveBeenCalledWith(
      'turn/end',
      { reason: { kind: 'stop' } },
    );
    expect(page.dispatchEvent.mock.calls.filter(([type]) => type === 'assistant/chunk'))
      .toHaveLength(36);
  });

  test('batches dense chunks and flushes before a turn boundary', () => {
    jest.useFakeTimers();
    const page = loadPage(pagePath);
    page.dispatchEvent = jest.fn();

    page.handleStreamFrame('assistant/chunk', { chunk: { text: '你' } });
    page.handleStreamFrame('assistant/chunk', { chunk: { text: '好' } });
    expect(page.dispatchEvent).not.toHaveBeenCalled();

    jest.advanceTimersByTime(100);
    expect(page.dispatchEvent).toHaveBeenCalledWith(
      'assistant/chunk',
      { chunk: { text: '你好' } },
    );

    page.handleStreamFrame('assistant/chunk', { chunk: { text: '呀' } });
    page.handleStreamFrame('turn/end', { reason: { kind: 'stop' } });
    expect(page.dispatchEvent.mock.calls.slice(-2)).toEqual([
      ['assistant/chunk', { chunk: { text: '呀' } }],
      ['turn/end', { reason: { kind: 'stop' } }],
    ]);
  });

});
