"""Generate sharp 4x PNG assets for the 27px custom tab bar icons."""

from pathlib import Path

from PIL import Image, ImageDraw


OUTPUT_SIZE = 108
SUPERSAMPLE = 4
CANVAS_SIZE = OUTPUT_SIZE * SUPERSAMPLE
GRAY = "#6f7174"
ACTIVE = "#c4673c"
WHITE = "#ffffff"
OUTPUT_DIR = Path(__file__).parent / "assets" / "icons"


def scaled(value):
    if isinstance(value, tuple):
        return tuple(round(item * SUPERSAMPLE) for item in value)
    return round(value * SUPERSAMPLE)


def canvas():
    image = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
    return image, ImageDraw.Draw(image)


def save(image, name):
    alpha = image.getchannel("A")
    bounds = alpha.getbbox()
    artwork = image.crop(bounds)
    target = 96 * SUPERSAMPLE
    ratio = min(target / artwork.width, target / artwork.height)
    artwork = artwork.resize(
        (round(artwork.width * ratio), round(artwork.height * ratio)),
        Image.Resampling.LANCZOS,
    )
    image = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
    image.alpha_composite(
        artwork,
        ((CANVAS_SIZE - artwork.width) // 2, (CANVAS_SIZE - artwork.height) // 2),
    )
    image = image.resize((OUTPUT_SIZE, OUTPUT_SIZE), Image.Resampling.LANCZOS)
    image.save(
        OUTPUT_DIR / name,
        optimize=True,
    )


def draw_ai(color, active):
    image, draw = canvas()
    box = scaled((20, 18, 88, 74))
    width = scaled(6)
    if active:
        draw.rounded_rectangle(box, radius=scaled(12), fill=color)
        draw.polygon([scaled((35, 72)), scaled((30, 91)), scaled((52, 73))], fill=color)
        dot_color = WHITE
    else:
        draw.rounded_rectangle(box, radius=scaled(12), outline=color, width=width)
        draw.line(
            [scaled((38, 73)), scaled((31, 89)), scaled((53, 73))],
            fill=color,
            width=width,
            joint="curve",
        )
        dot_color = color
    for x in (39, 54, 69):
        draw.ellipse(scaled((x - 3, 43, x + 3, 49)), fill=dot_color)
    return image


def draw_feed(color, active):
    image, draw = canvas()
    circle = scaled((19, 13, 89, 83))
    if active:
        draw.ellipse(circle, fill=color)
        compass_color = WHITE
    else:
        draw.ellipse(circle, outline=color, width=scaled(6))
        compass_color = color
    points = [scaled((63, 35)), scaled((48, 45)), scaled((43, 65)), scaled((59, 55))]
    draw.line(points + [points[0]], fill=compass_color, width=scaled(5), joint="curve")
    draw.ellipse(scaled((52, 48, 58, 54)), fill=compass_color)
    return image


def draw_profile(color, active):
    image, draw = canvas()
    if active:
        draw.ellipse(scaled((39, 12, 69, 42)), fill=color)
        draw.rounded_rectangle(scaled((24, 51, 84, 91)), radius=scaled(20), fill=color)
    else:
        width = scaled(6)
        draw.ellipse(scaled((39, 12, 69, 42)), outline=color, width=width)
        draw.rounded_rectangle(
            scaled((24, 50, 84, 91)),
            radius=scaled(20),
            outline=color,
            width=width,
        )
    return image


def draw_parking(color, active):
    image, draw = canvas()
    circle = scaled((18, 12, 90, 84))
    width = scaled(6)
    if active:
        draw.ellipse(circle, fill=color)
        mark_color = WHITE
    else:
        draw.ellipse(circle, outline=color, width=width)
        mark_color = color

    # Build the P as one filled glyph instead of joining separate strokes.
    # This keeps the bowl smooth and avoids visible seams after downsampling.
    glyph = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
    glyph_draw = ImageDraw.Draw(glyph)
    glyph_draw.rectangle(scaled((39, 26, 48, 67.5)), fill=mark_color)
    glyph_draw.ellipse(scaled((39, 63, 48, 72)), fill=mark_color)
    glyph_draw.rounded_rectangle(
        scaled((43, 26, 75, 58)),
        radius=scaled(16),
        fill=mark_color,
    )
    glyph_draw.rounded_rectangle(
        scaled((50, 34, 66, 50)),
        radius=scaled(8),
        fill=(0, 0, 0, 0),
    )
    image.alpha_composite(glyph)
    return image


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    generators = {
        "ai": draw_ai,
        "feed": draw_feed,
        "parking": draw_parking,
        "profile": draw_profile,
    }
    for name, generator in generators.items():
        save(generator(GRAY, False), f"{name}.png")
        save(generator(ACTIVE, True), f"{name}-active.png")


if __name__ == "__main__":
    main()
