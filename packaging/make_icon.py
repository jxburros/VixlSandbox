"""Draw the app icon (packaging/icon.ico) with Pillow, so the repository needs no binary image."""

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 256


def draw():
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # A rounded square in the app's accent colours, with two offset layers: a sandbox of layered images.
    for y in range(SIZE):
        t = y / (SIZE - 1)
        colour = (int(75 + (47 - 75) * t), int(63 + (111 - 63) * t), int(214 + (179 - 214) * t), 255)
        draw.line([(0, y), (SIZE, y)], fill=colour)
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((8, 8, SIZE - 8, SIZE - 8), radius=56, fill=255)
    image.putalpha(mask)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((62, 62, 166, 166), radius=18, fill=(255, 255, 255, 110))
    draw.rounded_rectangle((90, 90, 194, 194), radius=18, fill=(255, 255, 255, 235))
    draw.line([(118, 122), (142, 168), (166, 122)], fill=(75, 63, 214, 255), width=14, joint="curve")
    return image


if __name__ == "__main__":
    out = Path(__file__).with_name("icon.ico")
    draw().save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"wrote {out}")
