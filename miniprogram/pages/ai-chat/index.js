const { request, BASE_URL } = require('../../utils/request');

Page({
  data: {
    messages: [],           // 消息列表 [{role, content, time}]
    inputValue: '',         // 输入框内容
    canSend: false,         // 输入框是否有内容（WXML 不能调 trim()）
    isLoading: false,       // 是否正在加载 AI 回复
    sessionId: '',          // 当前会话 ID
    userAvatar: '',         // 用户头像
    lastMsgId: 'msg-end',   // 滚动定位锚点
  },

  onLoad() {
    // 进入页面时获取/创建会话
    this.initSession();
  },

  onShow() {
    // 每次展示时滚动到底部
    this.scrollToBottom();
  },

  /** 初始化 AI 会话 */
  async initSession() {
    try {
      const res = await request('POST', '/ai/session');
      const { session_id, messages } = res;
      // 格式化历史消息
      const formatted = (messages || []).map(msg => ({
        role: msg.role,
        content: msg.content,
        time: this.formatTime(msg.created_at),
      }));
      this.setData({
        sessionId: session_id,
        messages: formatted,
      });
      this.scrollToBottom();
    } catch (err) {
      console.error('获取 AI 会话失败', err);
      wx.showToast({ title: '会话创建失败', icon: 'none' });
    }
  },

  /** 输入框内容变化 */
  onInputChange(e) {
    const value = e.detail.value;
    this.setData({
      inputValue: value,
      canSend: value.trim().length > 0,
    });
  },

  /** 发送消息 */
  async onSend() {
    const { inputValue, sessionId, isLoading } = this.data;
    if (!inputValue.trim() || isLoading || !sessionId) return;

    // 1. 添加用户消息到列表
    const userMsg = {
      role: 'user',
      content: inputValue.trim(),
      time: this.formatTime(Date.now()),
    };
    const newMessages = [...this.data.messages, userMsg];
    this.setData({
      messages: newMessages,
      inputValue: '',
      isLoading: true,
    });
    this.scrollToBottom();

    // 2. 添加空的 AI 气泡用于打字机效果
    const aiMsg = {
      role: 'assistant',
      content: '',
      time: this.formatTime(Date.now()),
    };
    this.setData({
      messages: [...this.data.messages, aiMsg],
    });
    this.scrollToBottom();

    // 3. 发起 SSE 流式请求
    this.streamChat(sessionId, userMsg.content);
  },

  /** SSE 流式聊天 */
  streamChat(sessionId, message) {
    const app = getApp();
    const token = wx.getStorageSync('token') || '';

    const task = wx.request({
      url: `${BASE_URL}/ai/chat`,
      method: 'POST',
      header: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
      },
      data: { session_id: sessionId, message },
      enableChunked: true,
      responseType: 'text',
      success: () => {
        // 流已结束（在 onChunkReceived 中处理）
      },
      fail: (err) => {
        console.error('流式请求失败', err);
        this.handleStreamError();
      },
    });

    let buffer = '';

    task.onChunkReceived((res) => {
      const chunk = this.arrayBufferToString(res.data);
      buffer += chunk;

      // 按行解析 SSE 事件
      const lines = buffer.split('\n');
      buffer = lines.pop() || ''; // 保留不完整的行

      for (const line of lines) {
        if (line.startsWith('event: done')) {
          // 流结束
          this.setData({ isLoading: false });
          this.scrollToBottom();
          return;
        }
        if (line.startsWith('event: error')) {
          this.handleStreamError();
          return;
        }
        if (line.startsWith('data: ')) {
          const dataStr = line.slice(6);
          try {
            const parsed = JSON.parse(dataStr);
            // parsed 可能是 {"content":"..."} 或 裸字符串 "内容"（token 事件）
            const content = parsed.content || parsed.data || parsed;
            if (content) this.appendToAiBubble(content);
          } catch {
            // 非 JSON 数据，直接追加
            this.appendToAiBubble(dataStr);
          }
        }
      }
    });
  },

  /** 追加内容到 AI 气泡（打字机效果） */
  appendToAiBubble(text) {
    const messages = [...this.data.messages];
    const lastMsg = messages[messages.length - 1];
    if (lastMsg && lastMsg.role === 'assistant') {
      lastMsg.content += text;
      this.setData({ messages });
      this.scrollToBottom();
    }
  },

  /** 处理流式错误 */
  handleStreamError() {
    const messages = [...this.data.messages];
    const lastMsg = messages[messages.length - 1];
    if (lastMsg && lastMsg.role === 'assistant' && !lastMsg.content) {
      lastMsg.content = 'AI 回复被中断，请重试';
    } else {
      messages.push({
        role: 'assistant',
        content: 'AI 回复被中断，请重试',
        time: this.formatTime(Date.now()),
      });
    }
    this.setData({ messages, isLoading: false });
    this.scrollToBottom();
  },

  /** ArrayBuffer 转字符串 */
  arrayBufferToString(buf) {
    const bytes = new Uint8Array(buf);
    let result = '';
    for (let i = 0; i < bytes.length; i++) {
      result += String.fromCharCode(bytes[i]);
    }
    return result;
  },

  /** 滚动到底部 */
  scrollToBottom() {
    setTimeout(() => {
      this.setData({ lastMsgId: 'msg-end' });
    }, 50);
  },

  /** 格式化时间 */
  formatTime(timestamp) {
    if (!timestamp) return '';
    const date = new Date(timestamp);
    const h = String(date.getHours()).padStart(2, '0');
    const m = String(date.getMinutes()).padStart(2, '0');
    return `${h}:${m}`;
  },
});
