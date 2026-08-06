const { request, BASE_URL } = require('../../utils/request');
const app = getApp();

Page({
  data: {
    messages: [],           // 消息列表 [{id, role, content, nodes, time}]
    inputValue: '',         // 输入框内容
    canSend: false,         // 输入框是否有内容（WXML 不能调 trim()）
    isLoading: false,       // 是否正在加载 AI 回复
    sessionId: '',          // 当前会话 ID
    userAvatar: '',         // 用户头像
    lastMsgId: 'msg-end',   // 滚动定位锚点
    hasMoreHistory: true,   // 是否还有更早历史（#48 下拉加载）
    historyLoading: false,  // 历史加载防抖
  },

  /** 是否展示该消息（#48：tool 结果 + tool_call 消息不渲染）
   *  tool 角色（压缩摘要等 AI 内部结果）不对用户展示；
   *  assistant 的 tool_calls 消息 content 为空（纯工具调用轮次），无可显示文字
   */
  _shouldShow(msg) {
    if (msg.role === 'tool') return false;
    if (msg.role === 'assistant' && msg.tool_calls) return false;
    return true;
  },

  /** 格式化消息为渲染结构（保留 id 供历史分页游标） */
  _formatMessage(msg) {
    return {
      id: msg.id,
      role: msg.role,
      content: msg.content,
      nodes: msg.role === 'assistant' ? app.towxml(msg.content || '', 'markdown', { theme: 'light' }) : [],
      time: this.formatTime(msg.created_at),
    };
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
      // 格式化历史消息（过滤 tool 角色 + tool_call 消息，AI 内部过程不对用户展示）
      const formatted = (messages || [])
        .filter(msg => this._shouldShow(msg))
        .map(msg => this._formatMessage(msg));
      this.setData({
        sessionId: session_id,
        messages: formatted,
        hasMoreHistory: true,
      });
      this.scrollToBottom();

      // 新会话：自动发 Hi 让 AI 打招呼（不显示 Hi 气泡）
      if ((messages || []).length === 0) {
        this.silentGreeting(session_id);
      }
    } catch (err) {
      console.error('获取 AI 会话失败', err);
      wx.showToast({ title: '会话创建失败', icon: 'none' });
    }
  },

  /** 触顶加载更早历史（#48，TECH_SPEC 8.5 方案 B：默认最新 + 下拉加载） */
  async loadHistory() {
    const { messages, historyLoading, hasMoreHistory } = this.data;
    if (historyLoading || !hasMoreHistory || messages.length === 0) return;

    this.setData({ historyLoading: true });
    try {
      const beforeId = messages[0].id;
      const res = await request('GET', `/ai/messages?before_id=${beforeId}&limit=20`);
      const { messages: older, has_more } = res;
      // 后端返回 id 倒序（最新在前）→ 反转成时间正序，prepend 到列表头部
      const formatted = (older || []).reverse()
        .filter(msg => this._shouldShow(msg))
        .map(msg => this._formatMessage(msg));
      this.setData({
        messages: [...formatted, ...messages],
        hasMoreHistory: has_more,
        historyLoading: false,
      });
    } catch (err) {
      console.error('加载历史消息失败', err);
      this.setData({ historyLoading: false });
    }
  },

  /** 新会话：自动发送 Hi，但只显示 AI 回复 */
  silentGreeting(sessionId) {
    // AI 气泡由 message:start 事件创建（#22），这里只置 loading 状态
    this.setData({ isLoading: true });
    this.streamChat(sessionId, 'Hi');
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

    // 1. 添加用户消息到列表（AI 气泡由 message:start 事件创建，#22）
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

    // 2. 发起 SSE 流式请求
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
    let currentEvent = '';  // 跟踪当前 SSE 事件类型

    task.onChunkReceived((res) => {
      const chunk = this.arrayBufferToString(res.data);
      buffer += chunk;

      // 按行解析 SSE 事件
      const lines = buffer.split('\n');
      buffer = lines.pop() || ''; // 保留不完整的行

      for (const line of lines) {
        // 记录事件类型
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim();
        }
        if (line.startsWith('event: done')) {
          // 流结束
          this.setData({ isLoading: false });
          this.scrollToBottom();
          return;
        }
        if (line.startsWith('data: ')) {
          const dataStr = line.slice(6);
          // message:start 事件：多轮调用的新一轮 AI 回复开始，
          // 结束当前气泡、新开一个（issue #22：多轮多条 message 不拼成一条）
          if (currentEvent === 'message:start') {
            this.startNewAiBubble();
            currentEvent = '';
            continue;
          }
          // error 事件：显示后端错误文案（如「Agent 循环超过上限」），
          // 不能硬编码固定文案——_MAX_TURNS 超限等场景后端有具体提示
          if (currentEvent === 'error') {
            let msg = 'AI 回复被中断，请重试';
            try {
              const parsed = JSON.parse(dataStr);
              if (typeof parsed === 'string') msg = parsed;
              else msg = parsed.message || parsed.error || msg;
            } catch (e) {
              // 非 JSON 原文
              if (dataStr) msg = dataStr;
            }
            this.handleStreamError(msg);
            currentEvent = '';
            return;
          }
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
      lastMsg.nodes = app.towxml(lastMsg.content, 'markdown', { theme: 'light' });
      this.setData({ messages });
      this.scrollToBottom();
    }
  },

  /** 新开一条 AI 气泡（message:start 事件驱动，#22）
   *  纯 tool_call 轮次无 token 不发 message:start，前端不建气泡（无文字可显示）
   */
  startNewAiBubble() {
    const messages = [...this.data.messages];
    messages.push({
      role: 'assistant',
      content: '',
      nodes: [],
      time: this.formatTime(Date.now()),
    });
    this.setData({ messages });
    this.scrollToBottom();
  },

  /** 处理流式错误
   *  message: 错误文案（后端 error 事件的 data；缺省用通用文案）
   *  注意：必须同时重建 nodes——WXML 对 AI 气泡只渲染 <towxml nodes="{{item.nodes}}"/>，
   *  只改 content 不改 nodes 会导致气泡空白（issue #19 根因）。
   */
  handleStreamError(message) {
    const messages = [...this.data.messages];
    const lastMsg = messages[messages.length - 1];
    const text = (typeof message === 'string' && message) ? message : 'AI 回复被中断，请重试';
    if (lastMsg && lastMsg.role === 'assistant' && !lastMsg.content) {
      lastMsg.content = text;
      lastMsg.nodes = app.towxml(text, 'markdown', { theme: 'light' });
    } else {
      messages.push({
        role: 'assistant',
        content: text,
        nodes: app.towxml(text, 'markdown', { theme: 'light' }),
        time: this.formatTime(Date.now()),
      });
    }
    this.setData({ messages, isLoading: false });
    this.scrollToBottom();
  },

  /** ArrayBuffer 转字符串（UTF-8 安全） */
  arrayBufferToString(buf) {
    try {
      return new TextDecoder('utf-8').decode(buf);
    } catch {
      // 降级: percent-encode 后 decodeURIComponent
      const bytes = new Uint8Array(buf);
      let binary = '';
      for (let i = 0; i < bytes.length; i++) {
        binary += '%' + bytes[i].toString(16).padStart(2, '0');
      }
      return decodeURIComponent(binary);
    }
  },

  /** 滚动到底部 */
  scrollToBottom() {
    setTimeout(() => {
      this.setData({ lastMsgId: 'msg-end' });
    }, 50);
  },

  /** 格式化时间
   *  后端 func.now() 受数据库时区影响（SQLite 返回 UTC，MySQL 返回 session 时区），
   *  但无论如何结果都是"后端认为的本地时间"。
   *  作为简易方案，直接按字符串解析，不转时区。
   */
  formatTime(timestamp) {
    if (!timestamp) return '';
    // Unix 时间戳（number 类型，如 Date.now()）→ 直接构造
    if (typeof timestamp === 'number') {
      const date = new Date(timestamp);
      const h = String(date.getHours()).padStart(2, '0');
      const m = String(date.getMinutes()).padStart(2, '0');
      return `${h}:${m}`;
    }
    // ISO 字符串（后端 isoformat()）→ 取 HH:MM 部分直接显示
    const match = String(timestamp).match(/(\d{2}):(\d{2})/);
    return match ? `${match[1]}:${match[2]}` : '';
  },
});
