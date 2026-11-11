"""Autobuild target scaffold helper (LISTE 2026-11-09)."""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any

def ensure_target_scaffold(*, root: Path, check_write, item: dict[str, Any]) -> str | None:
    f = item.get("file")
    if not f:
        return None
    path = check_write(root / f)
    if path.exists():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    anchor = str(item.get("anchor") or "").strip()
    iid = str(item.get("id") or "ITEM")
    if suffix == ".md":
        title = str(item.get("title") or path.stem)
        path.write_text(f"# {title}\n\nStatus: NOT_TESTED\n\nStub fuer Autobuild ({iid}).\n", encoding="utf-8", newline="\n")
    elif suffix == ".js":
        path.write_text(
            f"/* stub {iid} */\nexport function initSprechzimmer() {{\n  return {{ ok: false, status: \"NOT_TESTED\" }};\n}}\n",
            encoding="utf-8",
            newline="\n",
        )
    elif suffix == ".py":
        if anchor.startswith("class "):
            name = anchor.split()[1].split("(")[0].split(":")[0]
            path.write_text(
                f'"""Stub {iid}."""\nfrom __future__ import annotations\n\n\nclass {name}:\n    def __init__(self, *args, **kwargs):\n        raise NotImplementedError\n',
                encoding="utf-8",
                newline="\n",
            )
        else:
            m = re.search(r"def\s+([A-Za-z_][A-Za-z0-9_]*)", anchor.replace("\\", ""))
            name = m.group(1) if m else "stub_fn"
            path.write_text(
                f'"""Stub {iid}."""\nfrom __future__ import annotations\n\n\ndef {name}(*args, **kwargs):\n    raise NotImplementedError\n',
                encoding="utf-8",
                newline="\n",
            )
    else:
        path.write_text("", encoding="utf-8", newline="\n")
    return f"scaffold:{f}"