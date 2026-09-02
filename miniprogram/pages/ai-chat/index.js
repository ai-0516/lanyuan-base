const { request } = require('../../utils/request');
const { V2_BASE_URL } = require('../../utils/constants');
const app = getApp();

Page({
  data: {
    messages: [],           // 消息列表 [{role, content, nodes, time, seq?}]
    inputValue: '',         // 输入框内容
    canSend: false,         // 输入框是否有内容（WXML 不能调 trim()）
    isLoading: false,       // 是否正在加载 AI 回复
    sessionId: '',          // 当前会话 ID（v2 纯 uuid，POST /api/v2/ai/session 获取）
    userAvatar: '',         // 用户头像
    lastMsgId: 'msg-end',   // 滚动定位锚点
    hasMoreHistory: true,   // 是否还有更早历史（触顶加载）
    historyLoading: false,  // 历史加载防抖
    lastCursor: '',         // 历史分页游标（turn/start seq，加载更早 = before_seq）
  },

  onLoad() {
    // 进入页面时获取/复用会话
    this.initSession();
  },

  onShow() {
    // 每次展示时滚动到底部
    this.scrollToBottom();
  },

  /** 初始化 AI 会话（v2：TECH_SPEC §9.1 统一创建点）
   *  1. POST /api/v2/ai/session → {session_id}（复用用户最近会话或新建）
   *  2. 历史列表从 DSH 日志派生加载（§10.4，GET /session/{id}/messages）
   *  3. 无历史（全新用户）→ silentGreeting 自动打招呼
   */
  async initSession() {
    try {
      const res = await request('POST', V2_BASE_URL + '/ai/session');
      const sessionId = res.session_id;
      this.setData({ sessionId });
      // 首次加载历史（空列表也算——判断是否新会话）
      await this.loadHistory(true);
      if (this.data.messages.length === 0) {
        this.silentGreeting(sessionId);
      }
      this.scrollToBottom();
    } catch (err) {
      console.error('获取 AI 会话失败', err);
      wx.showToast({ title: '会话创建失败', icon: 'none' });
    }
  },

  /** 触顶加载更早历史（§10.4：数据源 = DSH session 日志派生，v1 表不读）
   *  游标 = turn/start 事件 seq（后端按 turn 分页——同轮 user+assistant 成对，
   *  DSH 事件序 step/start 先于 user/message，按消息 seq 分页会拆散一轮对话）
   */
  async loadHistory(initial = false) {
    const { messages, historyLoading, hasMoreHistory, sessionId, lastCursor } = this.data;
    if (historyLoading || !hasMoreHistory || !sessionId) return;
    if (!initial && messages.length === 0) return;

    this.setData({ historyLoading: true });
    try {
      const qs = lastCursor ? `?before_seq=${lastCursor}&limit=20` : '?limit=20';
      const res = await request('GET', `${V2_BASE_URL}/ai/session/${sessionId}/messages${qs}`);
      const { messages: older, has_more, cursor } = res;
      // 后端返回倒序（最新在前）→ 反转成时间正序，prepend 到列表头部
      const formatted = (older || []).reverse().map(m => this._formatHistoryMessage(m));
      this.setData({
        messages: [...formatted, ...messages],
        hasMoreHistory: has_more,
        lastCursor: cursor || lastCursor,
        historyLoading: false,
      });
    } catch (err) {
      console.error('加载历史消息失败', err);
      this.setData({ historyLoading: false });
    }
  },

  /** 格式化历史消息为渲染结构（v2：无 id/tool_calls 字段，time = events.time 毫秒） */
  _formatHistoryMessage(m) {
    return {
      role: m.role,
      content: m.content,
      nodes: m.role === 'assistant' ? app.towxml(m.content || '', 'markdown', { theme: 'light' }) : [],
      time: this.formatTime(m.time),
    };
  },

  /** 新会话：自动发 Hi 让 AI 打招呼
   *  注意（v2 事件流单一数据源）：Hi 会作为 user/message 事件渲染成用户气泡
   *  （服务端记录了这条消息，前端以事件流为真源展示——v1「不显示 Hi」行为退役）
   */
  silentGreeting(sessionId) {
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

  /** 发送消息（v2：用户气泡由 user/message 事件渲染，前端不做本地乐观渲染 §10.1） */
  async onSend() {
    const { inputValue, sessionId, isLoading } = this.data;
    if (!inputValue.trim() || isLoading || !sessionId) return;

    this.setData({
      inputValue: '',
      canSend: false,
      isLoading: true,
    });
    this.streamChat(sessionId, inputValue.trim());
  },

  /** SSE 流式聊天（v2：DSH 事件分发，v1 5 事件 token/done/error/message:start/retry_wait 全部退役）
   *  事件映射（TECH_SPEC §10.1）：
   *    turn/start       → 回合边界（重置回合状态，不建气泡）
   *    user/message     → 用户气泡（单一数据源）
   *    step/start       → 开新 AI 气泡（若上一个气泡为空则先删——纯工具步骤无文字）
   *    assistant/chunk  → text-delta 追加当前气泡
   *    turn/end         → 回合收尾（done / reason=error 错误态；最终气泡仍空则丢弃）
   *    error 帧         → 后端错误（runtime 崩溃等，「请重试」）
   */
  streamChat(sessionId, message) {
    const token = wx.getStorageSync('token') || '';

    const task = wx.request({
      url: `${V2_BASE_URL}/ai/chat`,
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
    // 跨 chunk 的 UTF-8 解码器（stream:true 保留多字节字符跨 chunk 状态，#77：
    // 不复用会导致中文字符被 chunk 边界截断时出现 U+FFFD 乱码）
    let sseDecoder = null;
    try {
      sseDecoder = new TextDecoder('utf-8', { stream: true });
    } catch (e) {
      // TextDecoder 不可用时走 arrayBufferToString 降级路径
      console.warn('[ai-chat] TextDecoder 不可用，走降级解码:', e);
    }

    task.onChunkReceived((res) => {
      const chunk = this.arrayBufferToString(res.data, sseDecoder);
      buffer += chunk;

      // 按行解析 SSE 事件
      const lines = buffer.split('\n');
      buffer = lines.pop() || ''; // 保留不完整的行

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim();
        }
        if (line.startsWith('data: ')) {
          const dataStr = line.slice(6);
          // error 帧：后端错误（runtime 崩溃等），文案不硬编码——后端给什么显示什么
          if (currentEvent === 'error') {
            let msg = 'AI 回复被中断，请重试';
            try {
              const parsed = JSON.parse(dataStr);
              if (typeof parsed === 'string') msg = parsed;
              else msg = parsed.message || parsed.error || msg;
            } catch (e) {
              if (dataStr) msg = dataStr;
            }
            this.handleStreamError(msg);
            return;
          }
          // 白名单事件：{type, data}（event_layer 透传 DSH 原样，§4.1）
          try {
            const payload = JSON.parse(dataStr);
            this.dispatchEvent(currentEvent, payload.data || {});
          } catch (e) {
            // 非 JSON 数据：忽略（白名单事件必为 JSON）
            console.warn('[ai-chat] 忽略非 JSON SSE 数据:', dataStr);
          }
        }
      }
    });
  },

  /** DSH 事件分发（§10.1 映射表） */
  dispatchEvent(type, data) {
    switch (type) {
      case 'turn/start':
        // 回合边界（一次 user_prompt 处理开始）：重置回合状态，不建气泡
        this._turnEnded = false;
        break;
      case 'user/message':
        // 用户气泡数据源（事件流单一数据源，前端不做本地乐观渲染）
        this.appendUserBubble(this.extractUserContent(data.content));
        break;
      case 'step/start':
        // 气泡边界：承接 v1 message:start 粒度（step = 一次 LLM 调用）
        this.startNewAiBubble();
        break;
      case 'assistant/chunk':
        // text-delta 追加当前气泡（后端已过滤，只会收到 text-delta）
        this.appendToAiBubble((data.chunk || {}).text || '');
        break;
      case 'turn/end':
        this.finishTurn(data);
        break;
      default:
        // 白名单外事件不应到达（防御）
        console.warn('[ai-chat] 未知事件类型:', type);
    }
  },

  /** user/message content 提取（真实 DSH 事件 = content block 数组
   *  [{"type": "text", "text": "..."}]；兼容裸字符串形态）
   */
  extractUserContent(content) {
    if (typeof content === 'string') return content;
    if (Array.isArray(content)) {
      return content
        .filter(b => b && b.type === 'text')
        .map(b => b.text || '')
        .join('');
    }
    return content ? String(content) : '';
  },

  /** 用户气泡（user/message 事件驱动）
   *  真实 DSH 事件序：turn/start → step/start → user/message（agent.ts 先开
   *  LLM 调用再 append 用户消息）——step/start 已先建空气泡，用户气泡要插入
   *  到它前面（UI 上用户消息在前，chunk 才能追加到最后的 AI 气泡）
   */
  appendUserBubble(content) {
    const messages = [...this.data.messages];
    const lastMsg = messages[messages.length - 1];
    const userMsg = {
      role: 'user',
      content,
      nodes: [],
      time: this.formatTime(Date.now()),
    };
    if (lastMsg && lastMsg.role === 'assistant' && !lastMsg.content) {
      messages.splice(messages.length - 1, 0, userMsg);
    } else {
      messages.push(userMsg);
    }
    this.setData({ messages });
    this.scrollToBottom();
  },

  /** 追加内容到 AI 气泡（打字机效果，assistant/chunk text-delta 驱动） */
  appendToAiBubble(text) {
    if (!text) return;
    const messages = [...this.data.messages];
    const lastMsg = messages[messages.length - 1];
    if (lastMsg && lastMsg.role === 'assistant') {
      lastMsg.content += text;
      lastMsg.nodes = app.towxml(lastMsg.content, 'markdown', { theme: 'light' });
      this.setData({ messages });
      this.scrollToBottom();
    }
  },

  /** 新开一条 AI 气泡（step/start 事件驱动，§10.1：每次 LLM 调用一条气泡）
   *  若上一个气泡为空则先删——纯工具步骤无文字不显示
   */
  startNewAiBubble() {
    const messages = [...this.data.messages];
    const lastMsg = messages[messages.length - 1];
    if (lastMsg && lastMsg.role === 'assistant' && !lastMsg.content) {
      messages.pop();
    }
    messages.push({
      role: 'assistant',
      content: '',
      nodes: [],
      time: this.formatTime(Date.now()),
    });
    this.setData({ messages });
    this.scrollToBottom();
  },

  /** 回合收尾（turn/end 事件驱动，§10.1）
   *  - reason.kind=error → 错误态（文案「请重试」）
   *  - 正常收尾：最终气泡仍空则丢弃（纯工具回合/空回复不显示）
   */
  finishTurn(data) {
    const reason = data.reason || {};
    if (reason.kind === 'error') {
      this.handleStreamError();
      return;
    }
    const messages = [...this.data.messages];
    const lastMsg = messages[messages.length - 1];
    if (lastMsg && lastMsg.role === 'assistant' && !lastMsg.content) {
      messages.pop();
    }
    this.setData({ messages, isLoading: false });
    this.scrollToBottom();
  },

  /** 处理流式错误
   *  message: 错误文案（后端 error 帧的 data；缺省用通用文案「请重试」语义）
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

  /** ArrayBuffer 转字符串（UTF-8 安全）
   *  @param {ArrayBuffer} buf - 本次 chunk 的二进制数据
   *  @param {TextDecoder|null} [decoder] - 持久 stream 解码器（跨 chunk 保留多字节状态）；
   *    传 null/不传时退化为一次性解码（无跨 chunk 状态）
   */
  arrayBufferToString(buf, decoder) {
    if (decoder) {
      return decoder.decode(buf, { stream: true });
    }
    try {
      return new TextDecoder('utf-8').decode(buf);
    } catch {
      // 降级: percent-encode 后 decodeURIComponent（#77：解码失败不再抛异常，
      // 多字节序列被 chunk 截断时保留原始字节，避免 URIError 中断整个流）
      const bytes = new Uint8Array(buf);
      let binary = '';
      for (let i = 0; i < bytes.length; i++) {
        binary += '%' + bytes[i].toString(16).padStart(2, '0');
      }
      try {
        return decodeURIComponent(binary);
      } catch {
        // 降级仍失败（chunk 截断的不完整 UTF-8 序列）：按 latin1 逐字节解码保留可读文本
        let text = '';
        for (let i = 0; i < bytes.length; i++) {
          text += String.fromCharCode(bytes[i]);
        }
        return text;
      }
    }
  },

  /** 滚动到底部 */
  scrollToBottom() {
    setTimeout(() => {
      this.setData({ lastMsgId: 'msg-end' });
    }, 50);
  },

  /** 格式化时间
   *  v2：后端返回 events.time（毫秒时间戳，number）→ 直接构造本地时间
   */
  formatTime(timestamp) {
    if (!timestamp) return '';
    // Unix 毫秒时间戳（number 类型）→ 直接构造
    if (typeof timestamp === 'number') {
      const date = new Date(timestamp);
      const h = String(date.getHours()).padStart(2, '0');
      const m = String(date.getMinutes()).padStart(2, '0');
      return `${h}:${m}`;
    }
    // ISO 字符串（兼容）→ 取 HH:MM 部分直接显示
    const match = String(timestamp).match(/(\d{2}):(\d{2})/);
    return match ? `${match[1]}:${match[2]}` : '';
  },
});
