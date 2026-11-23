from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "assets" / "brand" / "shorekeep-icon.png"
TRAY = ROOT / "assets" / "brand" / "shorekeep-seal.png"
SIZES = (512, 256, 192, 128, 64, 48, 32, 24, 16)

FONT_CANDIDATES = (
    "C:/Windows/Fonts/seguisb.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/local/share/fonts/DejaVuSans-Bold.ttf",
)
FONT_DIRS = (
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".fonts",
    Path.home() / "Library" / "Fonts",
)


def font_path() -> Path | None:
    override = os.environ.get("SHOREKEEP_FONT")
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            return path
        raise SystemExit(f"SHOREKEEP_FONT does not point at a font file: {path}")
    for candidate in FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return path
    for directory in FONT_DIRS:
        if not directory.is_dir():
            continue
        matches = sorted(directory.rglob("*Bold*.ttf"))
        if matches:
            return matches[0]
    return None


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = font_path()
    if path is None:
        print("warning: no bold TrueType font found, using Pillow default", file=sys.stderr)
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()
    return ImageFont.truetype(str(path), size)


def lockup(master: Image.Image, path: Path, color: tuple[int, int, int, int]) -> None:
    icon = master.resize((200, 200), Image.LANCZOS)
    font = load_font(128)
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
    master = Image.open(MASTER).convert("RGBA")
    master.save(brand / "shorekeep-icon.png", optimize=True)

    for size in SIZES:
        master.resize((size, size), Image.LANCZOS).save(brand / f"shorekeep-{size}.png", optimize=True)

    master.resize((256, 256), Image.LANCZOS).save(
        brand / "shorekeep.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )

    tray = Image.open(TRAY).convert("RGBA")
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
