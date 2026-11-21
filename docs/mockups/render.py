"""Render each mockup screen to a PNG (iPhone-sized, 2x) and build an overview sheet."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
SCREENS = sorted((HERE / "screens").glob("*.html"))
PNG = HERE / "png"
PNG.mkdir(exist_ok=True)
W, H = 390, 844

WW, WH = 1440, 900  # desktop screens: files starting with "web-"
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome")
    mob = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
    web = b.new_page(viewport={"width": WW, "height": WH}, device_scale_factor=1.5)
    for s in SCREENS:
        pg = web if s.stem.startswith("web-") else mob
        pg.goto(s.as_uri())
        pg.wait_for_timeout(250)
        pg.screenshot(path=str(PNG / f"{s.stem}.png"))
    b.close()

WEB = [s for s in SCREENS if s.stem.startswith("web-")]
SCREENS = [s for s in SCREENS if not s.stem.startswith("web-")]

# web overview: 2 per row
wimgs = [Image.open(PNG / f"{s.stem}.png") for s in WEB]
cw, ch, cols_w, gap_w, lab = 1080, 675, 2, 48, 60
rows_w = (len(wimgs) + cols_w - 1) // cols_w
wsheet = Image.new("RGB", (cols_w * cw + (cols_w + 1) * gap_w, rows_w * (ch + lab) + (rows_w + 1) * gap_w), "#CFD8DD")
wd = ImageDraw.Draw(wsheet)
try:
    wf = ImageFont.truetype(str(HERE.parent.parent / "app/static/fonts/public-sans.woff2"), 26)
except Exception:
    wf = ImageFont.load_default()
for i, (im, s) in enumerate(zip(wimgs, WEB)):
    r, c = divmod(i, cols_w)
    x = gap_w + c * (cw + gap_w)
    y = gap_w + r * (ch + lab + gap_w)
    frame = im.resize((cw, ch), Image.LANCZOS)
    wsheet.paste(frame, (x, y + lab))
    wd.rectangle([x - 1, y + lab - 1, x + cw, y + lab + ch], outline="#B7C3CA")
    wd.text((x, y + 16), s.stem.split("-", 2)[2].replace("-", " ").capitalize(), fill="#15304A", font=wf)
wsheet.save(HERE / "overview-web.png", optimize=True)

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
print("rendered", len(imgs), "mobile +", len(WEB), "web screens + overview.png + overview-web.png")
