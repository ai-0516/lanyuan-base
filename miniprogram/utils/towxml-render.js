/**
 * towxml → <rich-text> 节点转换器
 *
 * app.towxml() 输出 towxml 格式的节点树，
 * 此函数将其转为 WeChat <rich-text> 能渲染的 nodes 数组。
 *
 * 用法：
 *   const nodes = renderTowxml(app.towxml(content, 'markdown', {theme: 'light'}));
 *   // <rich-text nodes="{{nodes}}">
 */

const STYLE_MAP = {
  p: 'margin:6rpx 0;line-height:1.6',
  strong: 'font-weight:600',
  em: 'font-style:italic',
  code: 'font-family:monospace;background:#f5efe9;padding:2rpx 8rpx;border-radius:6rpx;font-size:0.9em',
  pre: 'background:#f5efe9;border-radius:12rpx;padding:20rpx 24rpx;margin:10rpx 0;overflow-x:auto;white-space:pre-wrap;word-break:break-all;font-size:24rpx;font-family:monospace;line-height:1.5',
  a: 'color:#c07a5a;text-decoration:underline',
  blockquote: 'border-left:4rpx solid #c07a5a;padding-left:20rpx;margin:10rpx 0;color:#8a7a6a;font-style:italic',
  h1: 'font-size:1.6em;font-weight:600;margin:12rpx 0 8rpx',
  h2: 'font-size:1.35em;font-weight:600;margin:12rpx 0 8rpx',
  h3: 'font-size:1.2em;font-weight:500;margin:12rpx 0 8rpx',
  ul: 'margin:6rpx 0;padding-left:28rpx',
  ol: 'margin:6rpx 0;padding-left:28rpx',
  li: 'margin:4rpx 0',
};

function convert(node) {
  // 文本节点（leaf）
  if (node.tag === 'text' || (node.text !== undefined && !node.tag)) {
    return { type: 'text', text: node.text || '' };
  }

  // 有标签的元素
  const children = (node.children || []).map(convert);
  const name = node.tag || 'span';
  const style = STYLE_MAP[name] || '';

  // 列表项: 在 text 前追加标记
  if (name === 'li') {
    const prefix = node.listType === 'ol' ? '' : '• ';
    if (children.length > 0 && children[0].type === 'text') {
      children[0] = { type: 'text', text: prefix + children[0].text };
    }
  }

  return { name, attrs: style ? { style } : {}, children };
}

function render(towxmlResult) {
  if (!towxmlResult || !towxmlResult.children) return [];
  return towxmlResult.children.map(convert);
}

module.exports = { render };
