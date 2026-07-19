"""补充生成 notification-icon 和 comment-icon"""
from PIL import Image, ImageDraw
import os

SIZE = 44
TERRACOTTA = (196, 103, 60)
GRAY = (138, 138, 138)
WHITE = (255, 255, 255, 200)

BASE = os.path.dirname(os.path.abspath(__file__))


def make_circle(draw, cx, cy, r, fill):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)


def rounded_rect(draw, x1, y1, x2, y2, r, fill):
    draw.rounded_rectangle([x1, y1, x2, y2], radius=r, fill=fill)


def gen_notification_icon(filepath):
    """通知铃铛图标"""
    img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx = SIZE // 2
    # 铃身
    rounded_rect(draw, 10, 12, 34, 30, 6, TERRACOTTA)
    # 铃舌（小圆）
    make_circle(draw, cx, 34, 4, TERRACOTTA)
    # 顶部半圆环
    draw.arc([cx - 5, 6, cx + 5, 14], 180, 360, fill=(232, 168, 124), width=3)
    img.save(filepath, 'PNG')


def gen_comment_icon(filepath):
    """评论气泡图标"""
    img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx = SIZE // 2
    # 气泡主体
    rounded_rect(draw, 7, 11, 37, 31, 5, GRAY)
    # 气泡尾巴
    draw.polygon([(30, 31), (34, 39), (38, 31)], fill=GRAY)
    # 内三点（省略号）
    for i, dx in enumerate([-5, 0, 5]):
        make_circle(draw, cx + dx, 21, 2, WHITE)
    img.save(filepath, 'PNG')


def main():
    images_dir = os.path.join(BASE, 'images')
    os.makedirs(images_dir, exist_ok=True)
    gen_notification_icon(os.path.join(images_dir, 'notification-icon.png'))
    print('  ✓ images/notification-icon.png')
    gen_comment_icon(os.path.join(images_dir, 'comment-icon.png'))
    print('  ✓ images/comment-icon.png')


if __name__ == '__main__':
    main()
