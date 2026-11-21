"""Render each mockup screen to a PNG (iPhone-sized, 2x) and build an overview sheet."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
SCREENS = sorted((HERE / "screens").glob("*.html"))
PNG = HERE / "png"
PNG.mkdir(exist_ok=True)
W, H = 390, 844

with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome")
    pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
    for s in SCREENS:
        pg.goto(s.as_uri())
        pg.wait_for_timeout(250)
        pg.screenshot(path=str(PNG / f"{s.stem}.png"))
    b.close()

# overview: 5 per row
imgs = [Image.open(PNG / f"{s.stem}.png") for s in SCREENS]
tw, th = 390, 844
cols, gap, label_h = 5, 40, 56
rows = (len(imgs) + cols - 1) // cols
sheet = Image.new("RGB", (cols * tw + (cols + 1) * gap, rows * (th + label_h) + (rows + 1) * gap), "#CFD8DD")
d = ImageDraw.Draw(sheet)
try:
    f = ImageFont.truetype(str(HERE.parent.parent / "app/static/fonts/public-sans.woff2"), 22)
except Exception:
    f = ImageFont.load_default()
for i, (im, s) in enumerate(zip(imgs, SCREENS)):
    r, c = divmod(i, cols)
    x = gap + c * (tw + gap)
    y = gap + r * (th + label_h + gap)
    sheet.paste(im.resize((tw, th), Image.LANCZOS), (x, y + label_h))
    d.text((x, y + 14), s.stem.split("-", 1)[1].replace("-", " ").capitalize(), fill="#15304A", font=f)
sheet.save(HERE / "overview.png", optimize=True)
print("rendered", len(imgs), "screens + overview.png")
