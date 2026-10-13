"""python -m app [--port 8080] [--reload] [--log FILE]

Under pythonw (no console, e.g. the logon task from app/autostart.ps1) there
is no stdout/stderr, so output goes to logs/app.log instead.
"""

from __future__ import annotations

import argparse
import socket
import sys

from app import config


def port_in_use(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Serve the offline map app")
    ap.add_argument("--host", default=config.HOST)
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--reload", action="store_true", help="auto-restart on code edits")
    ap.add_argument("--log", help="append output to this file (default under pythonw: logs/app.log)")
    args = ap.parse_args()

    log = args.log or (str(config.ROOT / "logs" / "app.log") if sys.stdout is None else None)
    if log:
        from pathlib import Path
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(log, "a", encoding="utf-8", buffering=1)

    if port_in_use(args.host, args.port):
        # already running (logon task + a manual start): nothing to do
        print(f"my-osrm app already serving on http://{args.host}:{args.port}", flush=True)
        return

    import uvicorn
    print(f"my-osrm app: http://{args.host}:{args.port}", flush=True)
    uvicorn.run("app.main:create_app", factory=True, host=args.host, port=args.port,
                reload=args.reload, reload_dirs=[str(config.ROOT / "app")] if args.reload else None,
                log_level="info", use_colors=False if log else None)


if __name__ == "__main__":      # uvicorn --reload spawns a child process; keep this guarded
    main()
