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
