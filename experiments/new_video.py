"""Start a new Optimal Drives tour video from experiments/_template.

    python experiments/new_video.py bass_pro --subject "Bass Pro Shops" [--spider bass_pro_shops]
                                             [--requested-by parkermanh]

Makes experiments/<slug>/ with video.py, inventory.py, assets/ and cache/, the placeholders
filled in. Then follow experiments/_template/README.md (inventory, build, stills, render).
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "_template"


def fill(text: str, subs: dict[str, str]) -> str:
    for k, v in subs.items():
        text = text.replace(k, v)
    return text


def main() -> None:
    ap = argparse.ArgumentParser(description="scaffold a new tour video")
    ap.add_argument("slug", help="folder name under experiments/, e.g. bass_pro")
    ap.add_argument("--subject", required=True, help='what the video visits, e.g. "Bass Pro Shops"')
    ap.add_argument("--spider", help="AllThePlaces spider name (default: the slug)")
    ap.add_argument("--requested-by", help="viewer's handle for a request (adds the comment card)")
    ap.add_argument("--into", help="parent folder (default experiments/; for trying the scaffold out)")
    args = ap.parse_args()
    if not re.fullmatch(r"[a-z0-9_]+", args.slug):
        sys.exit("slug: lowercase letters, digits and underscores only")
    dest = (Path(args.into) if args.into else HERE) / args.slug
    if dest.exists():
        sys.exit(f"{dest} exists; pick another slug or delete it first")
    today = dt.date.today()
    noun = args.subject
    subs = {
        "SOURCE_NOTE": f"started {today.isoformat()}",
        "SUBJECTS": f"{noun}s" if not noun.endswith("s") else noun,
        "SUBJECT": noun,
        "SPIDER": args.spider or args.slug,
        "SLUG": args.slug,
        "MONTH YEAR": today.strftime("%b %Y"),
        "YYYY-MM-DD": today.isoformat(),
    }
    dest.mkdir(parents=bool(args.into))
    (dest / "assets").mkdir()
    (dest / "cache").mkdir()
    for name in ("video.py", "inventory.py"):
        text = fill((TEMPLATE / name).read_text(encoding="utf-8"), subs)
        if args.requested_by and name == "video.py":
            text = text.replace("REQUESTED_BY = None", f'REQUESTED_BY = "{args.requested_by}"')
        (dest / name).write_text(text, encoding="utf-8")
    shutil.copy(TEMPLATE / "README.md", dest / "README.md")
    shown = dest.relative_to(HERE.parent) if dest.is_relative_to(HERE.parent) else dest
    print(f"made {shown}/: video.py, inventory.py, assets/, cache/")
    print("next: experiments/_template/README.md step 2 (inventory), then build, stills, render")


if __name__ == "__main__":
    main()
