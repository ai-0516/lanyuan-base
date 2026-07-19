"""生成小程序图标资源"""
from PIL import Image, ImageDraw
import os

SIZE = 81  # tabBar 图标标准尺寸
TERRACOTTA = (196, 103, 60)  # #c4673c
TERRACOTTA_DEEP = (155, 61, 26)  # 深色版本
GRAY = (138, 138, 138)  # #8a8a8a
WHITE = (255, 255, 255)
CREAM = (250, 247, 242)

BASE = os.path.dirname(os.path.abspath(__file__))


def make_circle_icon(draw, cx, cy, r, fill):
    """画实心圆"""
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)


def make_rounded_rect(draw, x1, y1, x2, y2, r, fill):
    """画圆角矩形"""
    draw.rounded_rectangle([x1, y1, x2, y2], radius=r, fill=fill)


def gen_ai_icon(color, filepath):
    """AI 聊天气泡图标"""
    img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # 气泡主体
    make_rounded_rect(draw, 14, 18, 67, 58, 10, color)
    # 气泡尾巴（小三角）
    draw.polygon([(52, 58), (58, 70), (62, 58)], fill=color)
    # 内部三个小圆点代表 AI / 智能
    dot_r = 3
    for i, dx in enumerate([-8, 0, 8]):
        make_circle_icon(draw, SIZE//2 + dx, 38, dot_r, WHITE)
    img.save(filepath, 'PNG')


def gen_feed_icon(color, filepath):
    """发现/网格图标（九宫格缩略）"""
    img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # 三行三列小方块
    gap = 5
    block_size = 19
    start = 14
    for row in range(3):
        for col in range(3):
            x = start + col * (block_size + gap)
            y = start + row * (block_size + gap)
            make_rounded_rect(draw, x, y, x + block_size, y + block_size, 4, color)
    img.save(filepath, 'PNG')


def gen_profile_icon(color, filepath):
    """个人中心/人物图标"""
    img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # 头部（圆形）
    make_circle_icon(draw, SIZE//2, 26, 14, color)
    # 身体（圆角矩形）
    body_w = 38
    body_h = 28
    body_x = (SIZE - body_w) // 2
    body_y = 46
    make_rounded_rect(draw, body_x, body_y, body_x + body_w, body_y + body_h, 14, color)
    img.save(filepath, 'PNG')


def gen_logo(filepath):
    """应用 Logo（陶土色圆形 + L 字母）"""
    img = Image.new('RGBA', (SIZE * 2, SIZE * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = SIZE, SIZE
    r = SIZE - 8
    # 外圈深陶土
    make_circle_icon(draw, cx, cy, r, TERRACOTTA)
    # 内圈浅色
    make_circle_icon(draw, cx, cy, r - 12, (232, 168, 124))
    # 中心 "兰" 字手写风格简化 — 用三条弧线装饰
    # 画一个简单的圆形装饰
    make_circle_icon(draw, cx, cy, r - 24, WHITE)
    img.save(filepath, 'PNG')


def gen_wechat_icon(filepath):
    """微信图标（绿底白色对话气泡）"""
    WECHAT_GREEN = (7, 193, 96)
    img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # 圆角矩形背景
    make_rounded_rect(draw, 10, 15, 71, 60, 12, WECHAT_GREEN)
    # 两个对话气泡尾巴
    draw.polygon([(55, 60), (62, 75), (70, 60)], fill=WECHAT_GREEN)
    # 内部白点装饰
    make_circle_icon(draw, SIZE//2 - 8, 37, 3, WHITE)
    make_circle_icon(draw, SIZE//2 + 8, 37, 3, WHITE)
    img.save(filepath, 'PNG')


def main():
    icons_dir = os.path.join(BASE, 'assets', 'icons')
    images_dir = os.path.join(BASE, 'images')
    os.makedirs(icons_dir, exist_ok=True)
    os.makedirs(images_dir, exist_ok=True)

    # TabBar 图标 (active = 陶土色, inactive = 灰色)
    icons = [
        ('ai.png', 'ai-active.png', gen_ai_icon),
        ('feed.png', 'feed-active.png', gen_feed_icon),
        ('profile.png', 'profile-active.png', gen_profile_icon),
    ]
    for name, active_name, gen_fn in icons:
        gen_fn(GRAY, os.path.join(icons_dir, name))
        gen_fn(TERRACOTTA, os.path.join(icons_dir, active_name))
        print(f'  ✓ {name} + {active_name}')

    # Logo + 微信图标
    gen_logo(os.path.join(images_dir, 'logo.png'))
    print(f'  ✓ images/logo.png')
    gen_wechat_icon(os.path.join(images_dir, 'wechat-icon.png'))
    print(f'  ✓ images/wechat-icon.png')


if __name__ == '__main__':
    main()
