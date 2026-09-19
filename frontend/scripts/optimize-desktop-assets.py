"""Desktop asset optimizer: giant wallpaper + favicon.

The shipped wallpaper is 6400x3552 / 16 MB. Nothing in the desktop shell needs
that resolution:

* the renderer <img> layer gets WebP variants (2560 + 3840 wide) picked through
  srcset, which cuts the payload by ~99%;
* the native liquid-glass addon loads a real file path and decodes it itself, so
  it keeps a JPEG at the same filename -- just resampled down.

The case-study JPEGs that used to live under public/images were removed (git
history keeps them): all six were byte-corrupted in the repository -- JPEG
headers rewritten as U+FFFD sequences -- so the landing page now renders
text-only cards. The integrity check below is retained as a guard so a newly
committed file that is not really a JPEG gets reported instead of shipping
silently.
"""

from __future__ import annotations

import pathlib
import sys

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
WALL_DIR = ROOT / "public" / "third-party" / "appletechie-macos"
WALL_JPEG = WALL_DIR / "wallpaper-day2.jpg"
WALL_WEBP_WIDTHS = [2560, 3840]
JPEG_WIDTH = 2560
JPEG_QUALITY = 85
WEBP_QUALITY = 82


def human(n: float) -> str:
    return f"{n / 1048576:.2f} MB"


def report_corruption() -> None:
    """Guard against shipping non-JPEG payloads under public/images."""
    candidates = sorted((ROOT / "public" / "images").glob("*.jpg"))
    if not candidates:
        print("== public/images: no JPEG assets (corrupted set removed) ==")
        return
    print("== public/images JPEG integrity ==")
    for path in candidates:
        data = path.read_bytes()
        marker = data.count(b"\xef\xbf\xbd")
        try:
            Image.open(path).load()
            state = "readable"
        except Exception:
            state = "not decodable"
        print(f"  {path.name}: {len(data) / 1024:.0f} KB, U+FFFD x{marker}, {state}")


def optimize_wallpaper() -> None:
    before = WALL_JPEG.stat().st_size
    with Image.open(WALL_JPEG) as im:
        print(f"== wallpaper ==\n  source: {im.size[0]}x{im.size[1]}, {human(before)}")
        rgb = im.convert("RGB")
        for width in WALL_WEBP_WIDTHS:
            height = round(rgb.height * width / rgb.width)
            frame = rgb.resize((width, height), Image.LANCZOS)
            out = WALL_DIR / f"wallpaper-day2-{width}.webp"
            frame.save(out, "WEBP", quality=WEBP_QUALITY, method=6)
            print(f"  wrote {out.name}: {frame.size[0]}x{frame.size[1]}, {human(out.stat().st_size)}")

        if rgb.width > JPEG_WIDTH:
            height = round(rgb.height * JPEG_WIDTH / rgb.width)
            resampled = rgb.resize((JPEG_WIDTH, height), Image.LANCZOS)
            tmp = WALL_JPEG.with_suffix(".jpg.tmp")
            resampled.save(tmp, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
            tmp.replace(WALL_JPEG)
            print(
                f"  rewrote {WALL_JPEG.name} for the native addon: "
                f"{resampled.size[0]}x{resampled.size[1]}, {human(WALL_JPEG.stat().st_size)}"
            )
    print(f"  wallpaper total: {human(before)} -> "
          f"{human(sum(p.stat().st_size for p in WALL_DIR.iterdir() if p.is_file()))}")


def optimize_favicon() -> None:
    src = ROOT / "public" / "favicon.ico"
    before = src.stat().st_size
    with Image.open(src) as im:
        frames = {size: im.copy() for size in [(16, 16), (32, 32), (48, 48)]}
    large = frames[(48, 48)].resize((256, 256), Image.LANCZOS)
    tmp = src.with_suffix(".ico.tmp")
    frames[(16, 16)].save(
        tmp,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (256, 256)],
        append_images=[frames[(32, 32)], frames[(48, 48)], large],
    )
    after = tmp.stat().st_size
    if after < before:
        tmp.replace(src)
    else:
        tmp.unlink()
    print(f"== favicon ==\n  favicon.ico: {human(before)} -> {human(src.stat().st_size)}")


def main() -> int:
    report_corruption()
    optimize_wallpaper()
    optimize_favicon()
    return 0


if __name__ == "__main__":
    sys.exit(main())
