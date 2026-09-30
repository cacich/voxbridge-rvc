"""Regenerate the checked-in icon; requires Pillow only for maintainers."""

from pathlib import Path

from PIL import Image, ImageDraw


root = Path(__file__).resolve().parents[1]
target = root / "src" / "voxbridge" / "resources" / "voxbridge.ico"
scale = 4
size = 256 * scale
im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
d.rounded_rectangle((8 * scale, 8 * scale, 248 * scale, 248 * scale), radius=54 * scale, fill="#101b29")
points = [(42, 128), (72, 128), (87, 98), (109, 160), (130, 76),
          (152, 175), (170, 128), (214, 128)]
d.line([(x * scale, y * scale) for x, y in points], fill="#41c9b5", width=13 * scale, joint="curve")
for x, y in (points[0], points[-1]):
    r = 6.5 * scale
    d.ellipse((x * scale - r, y * scale - r, x * scale + r, y * scale + r), fill="#41c9b5")
d.arc((50 * scale, 153 * scale, 206 * scale, 209 * scale), 18, 162, fill="#5aa9e8", width=11 * scale)
icon = im.resize((256, 256), Image.Resampling.LANCZOS)
icon.save(target, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(target)
