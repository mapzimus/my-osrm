"""Restamp the subtitle on an already-rendered camera video (no re-render).

The subtitle is a static overlay, so a solid strip in the background colour
covers the old line and carries the new text; ffmpeg re-encodes in ~1 min
instead of a 23-min render. Also rewrites the GIF and the final PNG.

    python experiments/isochrone/patch_subtitle.py --format reel
"""

import argparse
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import animate as A  # noqa: E402
import camera_video as CV  # noqa: E402
import isochrone as iso  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="Boston")
    ap.add_argument("--format", choices=A.FORMATS, default="reel")
    args = ap.parse_args()
    (W, _), box, title_y = A.FORMATS[args.format]
    f_sub = A.font("segoeui.ttf", 26)
    lines = CV.subtitle_lines(args.name, f_sub, W - 84)
    top = title_y + 66              # below the title's descenders (56 clipped the y)
    bottom = min(box[1] - 4, title_y + 64 + 34 * len(lines) + 4)
    strip = Image.new("RGB", (W, bottom - top), CV.WATER_T)
    d = ImageDraw.Draw(strip)
    for k, line in enumerate(lines):
        d.text((42, title_y + 64 + 34 * k - top), line, font=f_sub, fill=A.MUTED)
    stem = iso.OUT / f"camera_{args.name.lower()}_{args.format}"
    png = stem.with_name(stem.name + "_subtitle.png")
    strip.save(png)
    src, tmp = stem.with_suffix(".mp4"), stem.with_name(stem.name + "_patched.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-i", str(png),
                    "-filter_complex", f"[0:v][1:v]overlay=0:{top}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "17", "-preset", "medium",
                    "-movflags", "+faststart", str(tmp)], check=True)
    tmp.replace(src)
    gif = src.with_suffix(".gif")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf",
                    "fps=15,scale=540:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];"
                    "[b][p]paletteuse=dither=sierra2_4a", str(gif)], check=True)
    final = stem.with_name(stem.name + "_final.png")
    if final.exists():
        im = Image.open(final).convert("RGB")
        im.paste(strip, (0, top))
        im.save(final)
    png.unlink()
    print(f"patched {src.name}, {gif.name}, {final.name}")


if __name__ == "__main__":
    main()
