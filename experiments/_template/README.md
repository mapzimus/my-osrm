# A new Optimal Drives tour video, in five steps

The template behind `experiments/new_video.py <slug>`. The full written standard is
[docs/optimal-drives-format.md](../../docs/optimal-drives-format.md); the catalogue of
every video so far is [docs/videos.md](../../docs/videos.md).

1. **Scaffold** (10 s): `python experiments/new_video.py bass_pro --subject "Bass Pro Shops"`.
   Makes `experiments/bass_pro/{video.py,inventory.py,assets/,cache/}` from this folder.
2. **Inventory** (15 min, data first: prove the data supports the claim before building):
   run the AllThePlaces spider into `cache/atp/`, finish `inventory.py`, check the count
   against what the chain claims. Viewer request: screenshot the comment to `assets/comment.jpg`.
   Logo to `assets/logo.png` (format doc 6a: official, 400 px+ wide).
3. **Build the tour** (5 to 60 min, engine up, no render running):
   `python experiments/walmart/build.py --cache experiments/bass_pro/cache --max-leg-h 30`.
   Note the printed hours and miles; the clock and the end card must match them.
4. **Stills** (1 min): `python experiments/bass_pro/video.py --platform tiktok --stills 1,15,30,45,63`.
   Fill in `TITLE`, `QUESTION`, `GROUPS`, `METROS`, `HIGHLIGHTS`; look at `out/*.png`.
5. **Render** (10 to 20 min per platform, in a hidden process):
   `.\experiments\render.ps1 bass_pro` then `.\experiments\render.ps1 bass_pro -Platform reels -Shorts`.
   Watch `out/<platform>_render.log` for `ALL DONE`. Then the checklist in format doc section 13.

What the template already does: the kit's shot camera and zoom cap, metro dives, highlight holds,
ferries as dashed lines with a boat, the logo + question opener, the comment card, the answer card
with state bars, chips per region, the "now" label, parts/join/workers for `render.ps1`.

What it does not do (copy from the reference, `experiments/chickfila/video.py`): flights to
islands (Hawaii, Puerto Rico), Alaska's own shot, a landing hold, an "Islands" chip.
