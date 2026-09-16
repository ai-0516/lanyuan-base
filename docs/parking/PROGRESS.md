# 东方兰园车位数据 - 今日进展

## 已完成

### 1. 数据提取
- 解析 5 个 Excalidraw 文件（A区234 / B区228 / C区324 / D区244 / F区355）
- 通过标签 + 箭头推演出全部 **1385 个车位**的精确坐标
- 生成 `parking_spots.json`（每区独立坐标）

### 2. PDF 拼接
- PDF 第2页(3370×2384) + 第3页(3370×1965) 拼接为 3370×4299
- 确认两页重叠 = 50px
- 生成 `stitched_final.png`

### 3. 特征匹配
- ORB 特征匹配将 5 个区域背景图定位到拼接大图
- 生成 `parking_spots_unified.json`（统一坐标系）

### 4. 验证工具
- `spots_verify/verify.html` — 分区验证（底图+车位编号）
- `verify_stitched.html` — 拼接大图验证（可拖动区域对齐 + 车位编号）

## 待完成

1. **区域对齐**：在 verify_stitched.html 中拖动 5 个区域小图与大图对齐
2. **配置保存**：点击"复制区域配置"保存到服务器
3. **车位验证**：勾选"显示车位"检查编号位置
4. **进入设计**：开始小程序 UI 原型

## 文件位置
```
docs/
  parking_spots.json           ← 1385 车位（分区坐标）
  parking_spots_unified.json   ← 1385 车位（拼接图统一坐标）
  design/
    stitched_final.png         ← 拼接大图 3370×4299
    parking_spots_unified.json ← 同上的副本
    verify_stitched.html       ← 对齐验证工具
    spots_verify/              ← 分区底图 + 验证页面
    stitch_compare2.html       ← 拼接对比页
```
