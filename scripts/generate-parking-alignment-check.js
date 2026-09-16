const fs = require('fs');
const path = require('path');

const repoRoot = path.resolve(__dirname, '..');
const source = JSON.parse(fs.readFileSync(
  path.join(repoRoot, 'docs/parking/parking_spots_buildings_gates_roads.json'),
  'utf8',
));
const outputPath = path.join(repoRoot, 'docs/parking/alignment-check.html');
const spots = source.spots.map(({ id, area, stitched_x, stitched_y, rect_w, rect_h, landscape }) => ({
  id,
  area,
  x: stitched_x,
  y: stitched_y,
  w: landscape ? rect_h : rect_w,
  h: landscape ? rect_w : rect_h,
  landscape,
}));

const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>停车位数据对齐校验</title>
  <style>
    * { box-sizing: border-box; }
    html, body { height: 100%; margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #27231f; }
    body { display: grid; grid-template-rows: auto 1fr; overflow: hidden; background: #eee9e2; }
    .toolbar { z-index: 10; display: flex; flex-wrap: wrap; align-items: center; gap: 12px 18px; padding: 10px 16px; background: rgba(255, 252, 248, .97); box-shadow: 0 2px 12px #0002; }
    .group { display: flex; align-items: center; gap: 7px; }
    label { font-size: 13px; color: #655d55; }
    input, select, button { height: 32px; border: 1px solid #d6cdc4; border-radius: 7px; background: white; padding: 0 9px; }
    input[type=number] { width: 72px; }
    input[type=range] { width: 100px; padding: 0; }
    button { cursor: pointer; }
    .legend { margin-left: auto; font-size: 12px; color: #766d65; }
    .viewport { position: relative; overflow: hidden; cursor: grab; }
    .viewport.dragging { cursor: grabbing; }
    .stage { position: absolute; left: 0; top: 0; width: 3370px; height: 4299px; transform-origin: 0 0; }
    .map { display: block; width: 3370px; height: 4299px; user-select: none; pointer-events: none; }
    .overlay { position: absolute; inset: 0; width: 3370px; height: 4299px; overflow: visible; }
    .spot { fill: rgba(0, 148, 255, .12); stroke: #0079d8; stroke-width: 3; vector-effect: non-scaling-stroke; cursor: pointer; }
    .spot:hover { fill: rgba(255, 193, 7, .48); stroke: #e4571a; stroke-width: 5; }
    .spot.selected { fill: rgba(255, 58, 48, .5); stroke: #d70000; stroke-width: 6; }
    .cross { pointer-events: none; stroke: #d70000; stroke-width: 3; vector-effect: non-scaling-stroke; }
    .info { position: absolute; left: 14px; bottom: 14px; min-width: 220px; padding: 10px 13px; border-radius: 9px; background: rgba(25, 23, 21, .88); color: white; font-size: 13px; line-height: 1.55; pointer-events: none; }
    .hint { color: #ffd28d; }
  </style>
</head>
<body>
  <div class="toolbar">
    <div class="group"><label for="mode">坐标含义</label><select id="mode"><option value="topLeft">左上角</option><option value="center">中心点</option></select></div>
    <div class="group"><label for="area">区域</label><select id="area"><option value="all">全部</option><option>A区</option><option>B区</option><option>C区</option><option>D区</option><option>F区</option></select></div>
    <div class="group"><label for="search">车位</label><input id="search" placeholder="如 B194"><button id="locate">定位</button></div>
    <div class="group"><label for="dx">整体 X</label><input id="dxRange" type="range" min="-60" max="60" value="0"><input id="dx" type="number" value="0"></div>
    <div class="group"><label for="dy">整体 Y</label><input id="dyRange" type="range" min="-60" max="60" value="0"><input id="dy" type="number" value="0"></div>
    <button id="fit">全图</button><button id="reset">重置校准</button>
    <span class="legend">蓝框：数据矩形　红十字：stitched_x / stitched_y</span>
  </div>
  <main class="viewport" id="viewport">
    <div class="stage" id="stage">
      <img class="map" src="stitched_final.png" alt="停车场地图">
      <svg class="overlay" id="overlay" viewBox="0 0 3370 4299"></svg>
    </div>
    <div class="info" id="info">滚轮缩放，拖动画布；点击任意矩形查看数据。<br><span class="hint">先切换“中心点 / 左上角”观察哪一种整体吻合。</span></div>
  </main>
  <script>
    const spots = ${JSON.stringify(spots)};
    const viewport = document.getElementById('viewport');
    const stage = document.getElementById('stage');
    const overlay = document.getElementById('overlay');
    const mode = document.getElementById('mode');
    const area = document.getElementById('area');
    const search = document.getElementById('search');
    const info = document.getElementById('info');
    const ns = 'http://www.w3.org/2000/svg';
    let scale = 0.15, tx = 0, ty = 0, dragging = false, startX = 0, startY = 0, selected = null;

    function offsets() { return { dx: Number(document.getElementById('dx').value) || 0, dy: Number(document.getElementById('dy').value) || 0 }; }
    function rectPosition(spot) {
      const { dx, dy } = offsets();
      return mode.value === 'center'
        ? { x: spot.x - spot.w / 2 + dx, y: spot.y - spot.h / 2 + dy }
        : { x: spot.x + dx, y: spot.y + dy };
    }
    function render() {
      overlay.replaceChildren();
      const fragment = document.createDocumentFragment();
      spots.filter(spot => area.value === 'all' || spot.area === area.value).forEach(spot => {
        const pos = rectPosition(spot);
        const rect = document.createElementNS(ns, 'rect');
        rect.setAttribute('x', pos.x); rect.setAttribute('y', pos.y);
        rect.setAttribute('width', spot.w); rect.setAttribute('height', spot.h);
        rect.setAttribute('class', 'spot' + (spot.id === selected ? ' selected' : ''));
        rect.addEventListener('click', event => { event.stopPropagation(); selectSpot(spot); });
        const crossA = document.createElementNS(ns, 'line');
        crossA.setAttribute('x1', spot.x - 5); crossA.setAttribute('y1', spot.y); crossA.setAttribute('x2', spot.x + 5); crossA.setAttribute('y2', spot.y); crossA.setAttribute('class', 'cross');
        const crossB = document.createElementNS(ns, 'line');
        crossB.setAttribute('x1', spot.x); crossB.setAttribute('y1', spot.y - 5); crossB.setAttribute('x2', spot.x); crossB.setAttribute('y2', spot.y + 5); crossB.setAttribute('class', 'cross');
        fragment.append(rect, crossA, crossB);
      });
      overlay.append(fragment);
    }
    function applyTransform() { stage.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + scale + ')'; }
    function fit() {
      scale = Math.min(viewport.clientWidth / 3370, viewport.clientHeight / 4299) * .96;
      tx = (viewport.clientWidth - 3370 * scale) / 2; ty = (viewport.clientHeight - 4299 * scale) / 2; applyTransform();
    }
    function selectSpot(spot) {
      selected = spot.id; search.value = spot.id; render();
      const pos = rectPosition(spot); const cx = pos.x + spot.w / 2, cy = pos.y + spot.h / 2;
      scale = Math.max(scale, 2.4); tx = viewport.clientWidth / 2 - cx * scale; ty = viewport.clientHeight / 2 - cy * scale; applyTransform();
      info.innerHTML = '<b>' + spot.id + '</b>（' + spot.area + '）<br>方向：' + (spot.landscape ? '横向' : '纵向') + '<br>数据点：(' + spot.x.toFixed(2) + ', ' + spot.y.toFixed(2) + ')<br>绘制矩形：' + spot.w + ' × ' + spot.h + '<br>当前模式：' + (mode.value === 'center' ? '坐标为中心点' : '坐标为左上角');
    }
    function locate() {
      const query = search.value.trim().toUpperCase(); const spot = spots.find(item => item.id === query);
      if (spot) { area.value = spot.area; selectSpot(spot); } else info.textContent = '没有找到车位：' + query;
    }
    function bindPair(rangeId, numberId) {
      const range = document.getElementById(rangeId), number = document.getElementById(numberId);
      range.addEventListener('input', () => { number.value = range.value; render(); });
      number.addEventListener('input', () => { range.value = number.value; render(); });
    }
    mode.addEventListener('change', render); area.addEventListener('change', render);
    document.getElementById('locate').addEventListener('click', locate);
    search.addEventListener('keydown', event => { if (event.key === 'Enter') locate(); });
    document.getElementById('fit').addEventListener('click', fit);
    document.getElementById('reset').addEventListener('click', () => { mode.value = 'topLeft'; area.value = 'all'; document.getElementById('dx').value = document.getElementById('dxRange').value = 0; document.getElementById('dy').value = document.getElementById('dyRange').value = 0; selected = null; render(); fit(); });
    bindPair('dxRange', 'dx'); bindPair('dyRange', 'dy');
    viewport.addEventListener('wheel', event => { event.preventDefault(); const rect = viewport.getBoundingClientRect(); const px = event.clientX - rect.left, py = event.clientY - rect.top; const old = scale; scale = Math.min(5, Math.max(.05, scale * (event.deltaY < 0 ? 1.15 : .87))); tx = px - (px - tx) * scale / old; ty = py - (py - ty) * scale / old; applyTransform(); }, { passive: false });
    viewport.addEventListener('pointerdown', event => { dragging = true; startX = event.clientX - tx; startY = event.clientY - ty; viewport.classList.add('dragging'); viewport.setPointerCapture(event.pointerId); });
    viewport.addEventListener('pointermove', event => { if (!dragging) return; tx = event.clientX - startX; ty = event.clientY - startY; applyTransform(); });
    viewport.addEventListener('pointerup', () => { dragging = false; viewport.classList.remove('dragging'); });
    window.addEventListener('resize', fit);
    render(); requestAnimationFrame(fit);
  </script>
</body>
</html>`;

fs.writeFileSync(outputPath, html);
console.log(`Generated alignment check page at ${outputPath}`);
