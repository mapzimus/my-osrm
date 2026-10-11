"""Tool discovery. A tool is a module in app/tools that exports

    meta   = ToolMeta(id="route", title="Route", order=10)
    router = APIRouter()          # mounted at /api/<id>

plus a matching app/web/tools/<id>.js. Drop both files in and the tool shows up.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import asdict, dataclass

from fastapi import APIRouter


@dataclass(frozen=True)
class ToolMeta:
    id: str
    title: str
    order: int = 100
    needs_engine: bool = True
    icon: str = ""                 # short label for the tab, e.g. "A→B"

    def public(self) -> dict:
        d = asdict(self)
        d["js"] = f"/tools/{self.id}.js"
        return d


def discover() -> list[tuple[ToolMeta, APIRouter]]:
    import app.tools as pkg

    found: list[tuple[ToolMeta, APIRouter]] = []
    for m in pkgutil.iter_modules(pkg.__path__):
        if m.name.startswith("_"):
            continue
        mod = importlib.import_module(f"app.tools.{m.name}")
        meta, router = getattr(mod, "meta", None), getattr(mod, "router", None)
        if not isinstance(meta, ToolMeta) or not isinstance(router, APIRouter):
            raise RuntimeError(f"app/tools/{m.name}.py must export `meta` (ToolMeta) and `router` (APIRouter)")
        if meta.id != m.name:
            raise RuntimeError(f"app/tools/{m.name}.py: meta.id={meta.id!r} must equal the module name")
        found.append((meta, router))
    return sorted(found, key=lambda t: (t[0].order, t[0].id))
