/**
 * md-parser.js — Markdown 转 WeChat <rich-text> nodes
 *
 * 支持的语法：
 *   **粗体**  *斜体*  `行内代码`  ```代码块```
 *   # ~ ###### 标题  - 无序列表  1. 有序列表
 *   [链接](url)  > 引用
 *
 * 用法：
 *   const md = require('../../utils/md-parser');
 *   const nodes = md.parse(content);
 *   // <rich-text nodes="{{nodes}}">
 */

function parse(text) {
  if (!text) return [{ type: 'text', text: '' }];
  const blocks = splitBlocks(text);
  const nodes = [];
  for (const block of blocks) {
    const node = renderBlock(block);
    if (node) nodes.push(node);
  }
  return nodes.length ? nodes : [{ type: 'text', text }];
}

// ── 按行分割为块 ──────────────────────────
function splitBlocks(text) {
  const lines = text.split('\n');
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    // 代码块
    const codeMatch = line.match(/^```(\w*)/);
    if (codeMatch) {
      const lang = codeMatch[1];
      const codeLines = [];
      i++;
      while (i < lines.length && !lines[i].startsWith('```')) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // skip closing ```
      blocks.push({ type: 'code', lang, content: codeLines.join('\n') });
      continue;
    }
    // 引用块
    if (line.startsWith('> ')) {
      const quoteLines = [];
      while (i < lines.length && lines[i].startsWith('> ')) {
        quoteLines.push(lines[i].slice(2));
        i++;
      }
      blocks.push({ type: 'quote', content: parseInline(quoteLines.join('\n')) });
      continue;
    }
    // 标题
    const hMatch = line.match(/^(#{1,6})\s+(.+)/);
    if (hMatch) {
      blocks.push({ type: 'heading', level: hMatch[1].length, content: parseInline(hMatch[2]) });
      i++;
      continue;
    }
    // 无序列表
    if (line.match(/^[-*]\s+/)) {
      const items = [];
      while (i < lines.length && lines[i].match(/^[-*]\s+/)) {
        items.push(parseInline(lines[i].replace(/^[-*]\s+/, '')));
        i++;
      }
      blocks.push({ type: 'ul', items });
      continue;
    }
    // 有序列表
    if (line.match(/^\d+\.\s+/)) {
      const items = [];
      while (i < lines.length && lines[i].match(/^\d+\.\s+/)) {
        items.push(parseInline(lines[i].replace(/^\d+\.\s+/, '')));
        i++;
      }
      blocks.push({ type: 'ol', items });
      continue;
    }
    // 空行跳过
    if (line.trim() === '') {
      i++;
      continue;
    }
    // 普通段落（合并多行）
    const paraLines = [];
    while (i < lines.length && lines[i].trim() !== '' && !lines[i].match(/^(#{1,6}\s|```|[-*]\s|\d+\.\s|>\s)/)) {
      paraLines.push(lines[i]);
      i++;
    }
    blocks.push({ type: 'paragraph', content: parseInline(paraLines.join('\n')) });
  }
  return blocks;
}

// ── 行内解析 ──────────────────────────
function parseInline(text) {
  const children = [];
  let remaining = text;
  const patterns = [
    // **粗体**
    { re: /\*\*(.+?)\*\*/, tag: 'strong' },
    // *斜体*
    { re: /\*(.+?)\*/, tag: 'em' },
    // `行内代码`
    { re: /`([^`]+)`/, tag: 'code' },
    // [链接](url)
    { re: /\[([^\]]+)\]\(([^)]+)\)/, tag: 'a' },
  ];

  while (remaining.length > 0) {
    // 找最近匹配
    let best = null;
    let bestIdx = remaining.length;
    for (const p of patterns) {
      const m = remaining.match(p.re);
      if (m && m.index < bestIdx) {
        best = { match: m, tag: p.tag };
        bestIdx = m.index;
      }
    }

    if (!best) {
      // 没匹配 → 剩余全部是文本
      children.push({ type: 'text', text: remaining });
      break;
    }

    // 匹配前的文本
    if (bestIdx > 0) {
      children.push({ type: 'text', text: remaining.slice(0, bestIdx) });
    }

    const m = best.match;
    if (best.tag === 'a') {
      children.push({ type: 'text', text: m[1] });
    } else {
      children.push({
        type: 'text',
        text: m[1],
        style: best.tag === 'strong'
          ? 'font-weight:600'
          : best.tag === 'em'
            ? 'font-style:italic'
            : 'font-family:monospace;background:#f5efe9;padding:2rpx 8rpx;border-radius:6rpx;font-size:0.9em',
      });
    }

    remaining = remaining.slice(m.index + m[0].length);
  }

  return children;
}

// ── 块渲染 → rich-text node ──────────────────────────
function renderBlock(block) {
  switch (block.type) {
    case 'heading': {
      const size = { 1: '1.6em', 2: '1.35em', 3: '1.2em', 4: '1.1em', 5: '1em', 6: '0.95em' };
      const weight = block.level <= 3 ? '600' : '500';
      return {
        name: 'div',
        attrs: { style: `font-size:${size[block.level]};font-weight:${weight};margin:12rpx 0 8rpx` },
        children: block.content,
      };
    }
    case 'paragraph':
      return { name: 'p', attrs: { style: 'margin:6rpx 0;line-height:1.6' }, children: block.content };
    case 'code':
      return {
        name: 'pre',
        attrs: { style: 'background:#f5efe9;border-radius:12rpx;padding:20rpx 24rpx;margin:10rpx 0;overflow-x:auto;white-space:pre-wrap;word-break:break-all;font-size:24rpx;font-family:monospace;line-height:1.5' },
        children: [{ type: 'text', text: block.content, style: 'font-family:monospace' }],
      };
    case 'quote':
      return {
        name: 'blockquote',
        attrs: { style: 'border-left:4rpx solid var(--terracotta);padding-left:20rpx;margin:10rpx 0;color:#8a7a6a;font-style:italic' },
        children: block.content,
      };
    case 'ul': {
      const items = block.items.map(content => ({
        name: 'li',
        attrs: { style: 'margin:4rpx 0' },
        children: [{ type: 'text', text: '• ' }, ...content],
      }));
      return { name: 'ul', attrs: { style: 'margin:6rpx 0;padding-left:28rpx' }, children: items };
    }
    case 'ol': {
      const items = block.items.map((content, i) => ({
        name: 'li',
        attrs: { style: 'margin:4rpx 0' },
        children: [{ type: 'text', text: `${i + 1}. ` }, ...content],
      }));
      return { name: 'ol', attrs: { style: 'margin:6rpx 0;padding-left:28rpx' }, children: items };
    }
    default:
      return null;
  }
}

module.exports = { parse };
