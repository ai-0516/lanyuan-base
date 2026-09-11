const { request } = require('../../utils/request');
const { V2_BASE_URL, CLOUD_CONFIG, USE_CLOUD } = require('../../utils/constants');
const app = getApp();
const MOCK_STREAM_STORAGE_KEY = 'debugMockAiStream';
const STREAM_LOG_STORAGE_KEY = 'debugLogAiStream';

Page({
  data: {
    messages: [],           // 消息列表 [{role, content, nodes, time, seq?}]
    inputValue: '',         // 输入框内容
    canSend: false,         // 输入框是否有内容（WXML 不能调 trim()）
    isLoading: false,       // 是否正在加载 AI 回复
    sessionId: '',          // 当前会话 ID（v2 纯 uuid，POST /api/v2/ai/session 获取）
    userAvatar: '',         // 用户头像
    lastMsgId: '',          // 在两个静态底部锚点间切换，强制重新执行原生定位
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

  onUnload() {
    this._mockStreamActive = false;
    this.stopAutoScroll(false);
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
      // 首次加载历史（空列表也算——判断是否新会话；加载失败返回 false
      // 不触发 greeting——避免网络抖动时给已有会话注入 Hi，PR #98 review 建议）
      const historyLoaded = await this.loadHistory(true);
      if (historyLoaded && this.data.messages.length === 0) {
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
    if (historyLoading || !hasMoreHistory || !sessionId) return false;
    if (!initial && messages.length === 0) return false;

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
      return true;
    } catch (err) {
      console.error('加载历史消息失败', err);
      this.setData({ historyLoading: false });
      // 返回 false：调用方（initSession）据此区分「加载成功但空」与「加载失败」
      // ——失败时不触发 silentGreeting，避免给已有会话注入 Hi（PR #98 review 建议）
      return false;
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

  /** WS 流式聊天（2026-09-04 路线2：SSE enableChunked → WebSocket 统一通道——
   *  wx.cloud.callContainer 不支持流式；一轮对话 = 一条连接）
   *
   * 传输协议（/api/v2/ai/chat/ws）：
   *   连接 → 首帧 {token, session_id, message} → 事件逐帧 {type, data}
   *   （后端失败先推 error 帧 {type:'error', data:{message}} 再关闭）
   *
   * 事件映射（§10.1 不变——只换传输，不换事件协议）：
   *   turn/start → 回合边界；user/message → 用户气泡；step/start → 新 AI 气泡；
   *   assistant/chunk → text-delta 追加；turn/end → 回合收尾；error 帧 → 错误收尾
   *
   * 通道（2026-09-04 修正——不走云托管公网域名，用 constants.USE_CLOUD 显式
   * 开关而非 envVersion 自动分流，开发者工具可置 true 直接联调云端）：
   * - USE_CLOUD=false → wx.connectSocket（ws://localhost，连本地后端）
   * - USE_CLOUD=true → wx.cloud.connectContainer（微信云托管私有链路，与
   *   callContainer 同源：免公网域名、免 mp 后台 socket 合法域名配置、不依赖
   *   「公网访问」开关 → WX_TRUST_OPENID_HEADER 信任门控部署前提（公网已关闭）
   *   可同时成立。socketTask 与 connectSocket 返回值同构，事件处理零差异；
   *   WS 鉴权走首帧 JWT，connectContainer「iOS 高性能+ 模式不带 x-wx-openid」
   *   的已知限制不影响本项目）
   */
  async streamChat(sessionId, message) {
    // towxml 子组件的排版可能晚于 chunk 的 setData 回调。流式期间持续重新
    // 请求绝对底部，才能在真实换行出现时立即跟随，而不是等 turn/end。
    this.startAutoScroll();
    if (this.isMockStreamEnabled()) {
      this.streamMockReply(message);
      return;
    }
    const token = wx.getStorageSync('token') || '';

    let socket;
    if (USE_CLOUD) {
      // 云托管：私有链路 connectContainer（需基础库 ≥2.21.1；缺失时
      // 显式可读提示，与 request.js callContainer 守卫同款语义）——
      // 开发者工具把 constants.USE_CLOUD 置 true 即可直接联调云端
      if (!wx.cloud || typeof wx.cloud.connectContainer !== 'function') {
        this.handleStreamError('云能力不可用：请升级微信基础库（≥2.21.1）后重试');
        return;
      }
      try {
        const { socketTask } = await wx.cloud.connectContainer({
          config: { env: CLOUD_CONFIG.ENV },
          service: CLOUD_CONFIG.SERVICE, // 云托管服务名（与 callContainer 同源）
          path: '/api/v2/ai/chat/ws',
        });
        socket = socketTask;
      } catch (err) {
        console.error('[ai-chat] connectContainer 连接失败', err);
        this.handleStreamError();
        return;
      }
    } else {
      // 本地开发：直连本地后端（V2_BASE_URL http://localhost:8000/api/v2 → ws）
      socket = wx.connectSocket({
        url: V2_BASE_URL.replace(/^http/, 'ws') + '/ai/chat/ws',
      });
    }

    socket.onOpen(() => {
      socket.send({
        data: JSON.stringify({ token, session_id: sessionId, message }),
      });
    });

    socket.onMessage((res) => {
      let frame = null;
      try {
        frame = JSON.parse(res.data);
      } catch (e) {
        console.warn('[ai-chat] 忽略非 JSON WS 帧:', res.data);
        return;
      }
      const type = frame.type;
      const data = frame.data || {};
      if (type === 'error') {
        // 后端错误帧（token 无效 4401 / 归属 4403 / 参数 1008 / 服务端异常
        // 1011）——文案后端给（「请重试」语义），显示后连接由后端关闭
        this.flushPendingStreamChunks();
        this.handleStreamError(data.message || 'AI 回复被中断，请重试');
        return;
      }
      this.handleStreamFrame(type, data);
    });

    socket.onError(() => {
      console.error('[ai-chat] WebSocket 连接错误');
      this.handleStreamError();
    });

    socket.onClose(() => {
      // 正常流：turn/end 已触发 finishTurn（isLoading=false）；异常断开
      // （无 turn/end 到达）→ 兜底收尾，避免 isLoading 卡死无提示
      if (this.data.isLoading) {
        this.handleStreamError();
      }
    });
  },

  /**
   * 合并高频 text-delta，避免每 1–2 个字符都把完整 messages 提交到视图层。
   * 非 chunk 事件到达前先冲刷，保证 step/turn 边界的原始顺序不变。
   */
  handleStreamFrame(type, data) {
    if (type !== 'assistant/chunk') {
      this.flushPendingStreamChunks();
      this.dispatchEvent(type, data);
      return;
    }

    const text = (data.chunk || {}).text || '';
    if (!text) return;
    this.recordStreamChunk(text);
    this._pendingStreamText = (this._pendingStreamText || '') + text;
    if (this._chunkFlushScheduled) return;
    this._chunkFlushScheduled = true;
    setTimeout(() => {
      this._chunkFlushScheduled = false;
      this.flushPendingStreamChunks();
    }, 100);
  },

  flushPendingStreamChunks() {
    const text = this._pendingStreamText || '';
    if (!text) return;
    this._pendingStreamText = '';
    if (this._streamMetrics) this._streamMetrics.renderBatchCount += 1;
    this.dispatchEvent('assistant/chunk', { chunk: { text } });
  },

  /**
   * 本地流式调试开关。仅 develop 生效，避免体验版或正式版误绕过后端。
   * 开启：wx.setStorageSync('debugMockAiStream', true)
   * 关闭：wx.removeStorageSync('debugMockAiStream')
   */
  isMockStreamEnabled() {
    try {
      const info = wx.getAccountInfoSync();
      return info.miniProgram.envVersion === 'develop'
        && wx.getStorageSync(MOCK_STREAM_STORAGE_KEY) === true;
    } catch (err) {
      return false;
    }
  },

  /** 固定内容、固定节奏的本地事件流，复用真实 dispatchEvent/towxml 路径。 */
  streamMockReply(message) {
    const baseChunks = [
      '这是本地模拟的流式回复。',
      '它不会连接后端，也不会调用大模型，',
      '用于反复观察消息气泡逐渐变高时，',
      '页面是否能够在每次出现新行后及时向上滚动。',
      '\n\n第二段继续增加内容，',
      '确保回复高度超过当前可视区域。',
      '如果滚动工作正常，',
      '输入框上方应始终能够看到最新生成的文字，',
      '而不是等整段回答结束以后才突然移动。',
    ];
    const chunks = [];
    for (let round = 0; round < 4; round += 1) {
      baseChunks.forEach((text, index) => {
        chunks.push(index === 0 ? `\n\n第 ${round + 1} 段：${text}` : text);
      });
    }
    this._mockStreamActive = true;
    this.dispatchEvent('turn/start', {});
    this.dispatchEvent('step/start', {});
    this.dispatchEvent('user/message', {
      content: [{ type: 'text', text: message }],
    });

    let index = 0;
    const emitNext = () => {
      if (!this._mockStreamActive) return;
      if (index >= chunks.length) {
        this._mockStreamActive = false;
        this.dispatchEvent('turn/end', { reason: { kind: 'stop' } });
        return;
      }
      this.dispatchEvent('assistant/chunk', {
        chunk: { text: chunks[index] },
      });
      index += 1;
      this._mockStreamTimer = setTimeout(emitNext, 350);
    };
    emitNext();
  },

  /** DSH 事件分发（§10.1 映射表） */
  dispatchEvent(type, data) {
    switch (type) {
      case 'turn/start':
        // 回合边界（一次 user_prompt 处理开始）：重置回合状态，不建气泡
        this._turnEnded = false;
        this.startStreamMetrics();
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
    this.setData({ messages }, () => this.scrollToBottom());
  },

  /** 追加内容到 AI 气泡（打字机效果，assistant/chunk text-delta 驱动） */
  appendToAiBubble(text) {
    if (!text) return;
    const messages = [...this.data.messages];
    const lastMsg = messages[messages.length - 1];
    if (lastMsg && lastMsg.role === 'assistant') {
      lastMsg.content += text;
      const towxmlStartedAt = Date.now();
      lastMsg.nodes = app.towxml(lastMsg.content, 'markdown', { theme: 'light' });
      this.recordMetric('towxmlMs', Date.now() - towxmlStartedAt);
      // towxml 是子组件；等当前 chunk 的节点提交到视图层后再测量高度，
      // 否则连续流式更新期间会一直读到旧高度，只在 turn/end 时滚动。
      const setDataStartedAt = Date.now();
      this.setData({ messages }, () => {
        this.recordMetric('setDataCallbackMs', Date.now() - setDataStartedAt);
        this.scrollToBottom();
      });
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
    this.setData({ messages }, () => this.scrollToBottom());
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
    this.setData({ messages, isLoading: false }, () => {
      this.stopAutoScroll();
      this.finishStreamMetrics();
    });
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
    this.setData({ messages, isLoading: false }, () => this.stopAutoScroll());
  },

  /** 流式输出期间持续请求绝对底部，捕获 towxml 异步产生的新行。 */
  startAutoScroll() {
    if (this._autoScrollActive) return;
    this._autoScrollActive = true;
    const poll = () => {
      if (!this._autoScrollActive) return;
      this.scrollToBottom();
      this._autoScrollTimer = setTimeout(poll, 50);
    };
    poll();
  },

  stopAutoScroll(finalScroll = true) {
    this._autoScrollActive = false;
    // 微信小程序运行时提供 clearTimeout；eslint 的项目全局表未包含它。
    // eslint-disable-next-line no-undef
    clearTimeout(this._autoScrollTimer);
    this._autoScrollTimer = null;
    if (finalScroll) this.scrollToBottom();
  },

  /** 在两个相邻的静态锚点间切换，强制 scroll-into-view 重新定位到底部。 */
  scrollToBottom() {
    if (this._scrollScheduled) return;
    this._scrollScheduled = true;
    const scheduledAt = Date.now();
    setTimeout(() => {
      this._scrollScheduled = false;
      this.recordMetric('scrollTimerMs', Date.now() - scheduledAt);
      this.setData({
        lastMsgId: this.data.lastMsgId === 'msg-end-a' ? 'msg-end-b' : 'msg-end-a',
      });
    }, 16);
  },

  /** 以下指标只在 develop + 手动开关下采集，不记录消息正文。 */
  isStreamLogEnabled() {
    try {
      const info = wx.getAccountInfoSync();
      return info.miniProgram.envVersion === 'develop'
        && wx.getStorageSync(STREAM_LOG_STORAGE_KEY) === true;
    } catch (err) {
      return false;
    }
  },

  startStreamMetrics() {
    if (!this.isStreamLogEnabled()) {
      this._streamMetrics = null;
      return;
    }
    this._streamMetrics = {
      startedAt: Date.now(),
      lastChunkAt: 0,
      chunkCount: 0,
      characterCount: 0,
      renderBatchCount: 0,
      chunkGapMs: [],
      chunkSize: [],
      towxmlMs: [],
      setDataCallbackMs: [],
      scrollTimerMs: [],
    };
  },

  recordStreamChunk(text) {
    const metrics = this._streamMetrics;
    if (!metrics) return;
    const now = Date.now();
    if (metrics.lastChunkAt) metrics.chunkGapMs.push(now - metrics.lastChunkAt);
    metrics.lastChunkAt = now;
    metrics.chunkCount += 1;
    metrics.characterCount += text.length;
    metrics.chunkSize.push(text.length);
  },

  recordMetric(name, value) {
    const metrics = this._streamMetrics;
    if (metrics && metrics[name]) metrics[name].push(value);
  },

  summarizeMetric(values) {
    if (!values.length) return { avg: 0, max: 0 };
    return {
      avg: Math.round(values.reduce((sum, value) => sum + value, 0) / values.length),
      max: Math.max(...values),
    };
  },

  finishStreamMetrics() {
    const metrics = this._streamMetrics;
    if (!metrics) return;
    this._streamMetrics = null;
    console.info('[ai-chat] stream metrics', {
      durationMs: Date.now() - metrics.startedAt,
      chunkCount: metrics.chunkCount,
      characterCount: metrics.characterCount,
      renderBatchCount: metrics.renderBatchCount,
      chunkGapMs: this.summarizeMetric(metrics.chunkGapMs),
      chunkSize: this.summarizeMetric(metrics.chunkSize),
      towxmlMs: this.summarizeMetric(metrics.towxmlMs),
      setDataCallbackMs: this.summarizeMetric(metrics.setDataCallbackMs),
      scrollTimerMs: this.summarizeMetric(metrics.scrollTimerMs),
    });
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
