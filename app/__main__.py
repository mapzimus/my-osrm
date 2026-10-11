"""python -m app [--port 8080] [--reload]"""

from __future__ import annotations

import argparse

import uvicorn

from app import config


def main() -> None:
    ap = argparse.ArgumentParser(description="Serve the offline map app")
    ap.add_argument("--host", default=config.HOST)
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--reload", action="store_true", help="auto-restart on code edits")
    args = ap.parse_args()
    print(f"my-osrm app: http://{args.host}:{args.port}", flush=True)
    uvicorn.run("app.main:create_app", factory=True, host=args.host, port=args.port,
                reload=args.reload, reload_dirs=[str(config.ROOT / "app")] if args.reload else None,
                log_level="info")


if __name__ == "__main__":      # uvicorn --reload spawns a child process; keep this guarded
    main()
