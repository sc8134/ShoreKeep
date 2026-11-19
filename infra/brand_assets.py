from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONT = Path("C:/Windows/Fonts/seguisb.ttf")
MASTER = ROOT / "assets" / "brand" / "shorekeep-icon.png"
SIZES = (512, 256, 192, 128, 64, 48, 32, 24, 16)


def clean_master(path: Path) -> Image.Image:
    image = Image.open(path).convert("RGBA")
    red, green, blue, alpha = image.split()

    def white(channel):
        return channel.point(lambda value: 255 if value >= 235 else 0)

    white_mask = ImageChops.multiply(ImageChops.multiply(white(red), white(green)), white(blue))
    image.putalpha(ImageChops.subtract(alpha, white_mask))
    return image


def tray_silhouette(master: Image.Image) -> Image.Image:
    red, green, blue, _alpha = master.split()
    light = ImageChops.add(ImageChops.add(red, green), blue)
    mask = light.point(lambda value: 0 if value <= 150 else min(255, (value - 150) * 255 // 200))
    silhouette = Image.new("RGBA", master.size, (255, 255, 255, 255))
    silhouette.putalpha(mask)
    return silhouette


def lockup(master: Image.Image, path: Path, color: tuple[int, int, int, int]) -> None:
    icon = master.resize((200, 200), Image.LANCZOS)
    font = ImageFont.truetype(str(FONT), 128)
    text = "Shorekeep"
    spacing = 2
    width = sum(font.getlength(char) for char in text) + spacing * (len(text) - 1)
    canvas = Image.new("RGBA", (int(240 + width) + 32, 240), (0, 0, 0, 0))
    canvas.alpha_composite(icon, (0, 20))
    draw = ImageDraw.Draw(canvas)
    x = 240
    for char in text:
        draw.text((x, 48), char, font=font, fill=color)
        x += font.getlength(char) + spacing
    canvas.save(path, optimize=True)


def main() -> None:
    brand = ROOT / "assets" / "brand"
    master = clean_master(MASTER)
    master.save(brand / "shorekeep-icon.png", optimize=True)

    for size in SIZES:
        master.resize((size, size), Image.LANCZOS).save(brand / f"shorekeep-{size}.png", optimize=True)

    master.resize((256, 256), Image.LANCZOS).save(
        brand / "shorekeep.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )

    tray = tray_silhouette(master)
    for size in (16, 24, 32, 48, 64):
        tray.resize((size, size), Image.LANCZOS).save(brand / f"shorekeep-tray-{size}.png", optimize=True)
    lockup(master, brand / "shorekeep-lockup-light.png", (11, 31, 42, 255))
    lockup(master, brand / "shorekeep-lockup-dark.png", (232, 238, 252, 255))

    desktop_assets = ROOT / "services" / "desktop" / "mydl_desktop" / "assets"
    desktop_assets.mkdir(parents=True, exist_ok=True)
    master.resize((256, 256), Image.LANCZOS).save(desktop_assets / "shorekeep.png", optimize=True)
    tray.resize((64, 64), Image.LANCZOS).save(desktop_assets / "shorekeep-tray.png", optimize=True)

    static = ROOT / "services" / "backend" / "backend" / "static"
    master.resize((192, 192), Image.LANCZOS).save(static / "shorekeep-192.png", optimize=True)
    master.resize((64, 64), Image.LANCZOS).save(static / "shorekeep-64.png", optimize=True)
    (static / "favicon.ico").write_bytes((brand / "shorekeep.ico").read_bytes())

    print(f"brand:   {brand}")
    print(f"ico:     {brand / 'shorekeep.ico'}")
    print(f"desktop: {desktop_assets}")
    print(f"static:  {static / 'favicon.ico'}")


if __name__ == "__main__":
    main()
