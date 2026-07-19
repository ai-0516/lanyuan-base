// chat-bubble 组件 —— 聊天气泡
// user 气泡右对齐（陶土渐变背景），assistant 气泡左对齐（暖白背景）
Component({
  properties: {
    role: {
      type: String,
      value: 'user' // 'user' | 'assistant'
    },
    content: {
      type: String,
      value: '' // 消息文本
    },
    time: {
      type: String,
      value: '' // 发送时间（格式由调用方决定）
    }
  }
});
