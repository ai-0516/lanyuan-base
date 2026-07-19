"""补充生成小程序图片资源"""
from PIL import Image, ImageDraw
import os

SIZE = 80
TERRACOTTA = (196, 103, 60)
OLIVE = (107, 142, 90)
WHITE = (255, 255, 255, 200)

BASE = os.path.dirname(os.path.abspath(__file__))


def make_circle(draw, cx, cy, r, fill):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)


def gen_ai_avatar(filepath):
    """AI 头像：陶土色圆形 + 白色闪电符号（代表 AI）"""
    img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = SIZE // 2, SIZE // 2
    r = SIZE // 2
    make_circle(draw, cx, cy, r, TERRACOTTA)

    # 白色闪电图案（简化）
    pts = [
        (cx - 4, cy - 18),  # 左上
        (cx + 10, cy - 4),  # 右上拐点
        (cx + 2, cy - 2),   # 中心
        (cx + 10, cy + 6),  # 右下
        (cx - 6, cy + 14),  # 左下
        (cx + 2, cy + 4),   # 中心偏下
        (cx - 4, cy - 18),  # 闭合
    ]
    draw.polygon(pts, fill=WHITE)
    img.save(filepath, 'PNG')


def gen_default_avatar(filepath):
    """默认用户头像：橄榄绿圆形 + 白色人形剪影"""
    img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    make_circle(draw, SIZE//2, SIZE//2, SIZE//2, OLIVE)
    # 头部
    make_circle(draw, SIZE//2, 28, 12, WHITE)
    # 身体（圆角矩形）
    draw.rounded_rectangle([22, 44, 58, 74], radius=14, fill=WHITE)
    img.save(filepath, 'PNG')


def main():
    images_dir = os.path.join(BASE, 'images')
    os.makedirs(images_dir, exist_ok=True)
    gen_ai_avatar(os.path.join(images_dir, 'ai-avatar.png'))
    print('  ✓ images/ai-avatar.png')
    gen_default_avatar(os.path.join(images_dir, 'default-avatar.png'))
    print('  ✓ images/default-avatar.png')


if __name__ == '__main__':
    main()
