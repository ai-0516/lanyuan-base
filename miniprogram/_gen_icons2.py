"""生成 empty-icon 和 liked-icon"""
from PIL import Image, ImageDraw
import os

SIZE = 80
SIZE_SM = 36
TERRACOTTA = (196, 103, 60)
GRAY = (138, 138, 138)
SAND = (242, 232, 220)
WHITE = (255, 255, 255, 200)

BASE = os.path.dirname(os.path.abspath(__file__))
images_dir = os.path.join(BASE, 'images')
os.makedirs(images_dir, exist_ok=True)


def make_circle(draw, cx, cy, r, fill):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)


def rounded_rect(draw, x1, y1, x2, y2, r, fill):
    draw.rounded_rectangle([x1, y1, x2, y2], radius=r, fill=fill)


# ── empty-icon.png （空状态：沙色方框+虚线圆圈） ──
img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)
# 盒子轮廓
rounded_rect(draw, 14, 16, 66, 64, 8, SAND)
# 放大镜圆圈
draw.ellipse([28, 26, 54, 52], outline=GRAY, width=3)
# 放大镜手柄
draw.line([(48, 48), (58, 58)], fill=GRAY, width=3)
img.save(os.path.join(images_dir, 'empty-icon.png'))
print('  ✓ images/empty-icon.png')

# ── liked-icon.png （已点赞：红色实心爱心） ──
HEART_RED = (233, 85, 85)
img2 = Image.new('RGBA', (SIZE_SM, SIZE_SM), (0, 0, 0, 0))
draw2 = ImageDraw.Draw(img2)
cx, cy = SIZE_SM // 2, SIZE_SM // 2
# 用两个圆 + 一个三角组成爱心
r = 7
make_circle(draw2, cx - 5, cy - 4, r, HEART_RED)
make_circle(draw2, cx + 5, cy - 4, r, HEART_RED)
draw2.polygon([(cx - 13, cy - 2), (cx + 13, cy - 2), (cx, cy + 10)], fill=HEART_RED)
img2.save(os.path.join(images_dir, 'liked-icon.png'))
print('  ✓ images/liked-icon.png')
