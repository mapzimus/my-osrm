"""One-time setup: everything the app needs that isn't in git.

    python -m app.setup                      # all stages, skips what's done
    python -m app.setup --only vendor,cli    # just some stages
    python -m app.setup --only basemap --maxzoom 10   # small quick basemap

Stages are idempotent: each writes to a .part/.tmp and renames on success,
so an interrupted run resumes instead of trusting a truncated file.
"""
