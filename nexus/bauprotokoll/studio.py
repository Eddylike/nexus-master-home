"""Phase 7b: NEXUS Studio - Vorlagen fuer Social-Posts, Thumbnails, Portfolio.

Erzeugt eigenstaendige HTML-Dateien (keine externen Abhaengigkeiten) mit festen
Markenfarben/Schriften und Textueberlagerung. Batch-Export in einen Ordner.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

from nexus.bauprotokoll.config import load_config

BRAND = {
    "name": "NEXUS",
    "colors": {"bg": "#0b1020", "fg": "#f8fafc", "accent": "#38bdf8", "muted": "#94a3b8"},
    "font": "Inter, 'Segoe UI', system-ui, sans-serif",
}

TEMPLATE_DEFS = {
    "social_post": {"breite": 1080, "hoehe": 1080, "stil": "zentriert",
                    "beschreibung": "Quadratischer Social-Media-Post"},
    "thumbnail": {"breite": 1280, "hoehe": 720, "stil": "links",
                  "beschreibung": "YouTube/Video-Thumbnail mit Akzentbalken"},
    "portfolio": {"breite": 1600, "hoehe": 900, "stil": "geteilt",
                  "beschreibung": "Portfolio-Karte mit grossem Titel"},
}


class Studio:
    def __init__(self, templates_dir=None, output_dir=None, config=None):
        cfg = config or load_config()
        self.root = Path(cfg.root).resolve()
        self.templates_dir = Path(templates_dir or (self.root / "studio" / "templates"))
        self.output_dir = Path(output_dir or (self.root / "workspace" / "studio_export"))
        self.templates_dir.mkdir(parents=True, exist_ok=True)

    def ensure_templates(self) -> list[str]:
        created = []
        for name, spec in TEMPLATE_DEFS.items():
            path = self.templates_dir / f"{name}.json"
            if path.is_file():
                continue
            data = {"name": name, **spec, "brand": BRAND}
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
            created.append(name)
        return created

    def list_templates(self) -> list[dict]:
        self.ensure_templates()
        out = []
        for path in sorted(self.templates_dir.glob("*.json")):
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except ValueError:
                continue
        return out

    def _template(self, name: str) -> dict:
        path = self.templates_dir / f"{name}.json"
        if not path.is_file():
            self.ensure_templates()
        if not path.is_file():
            raise KeyError(f"Unbekannte Vorlage: {name}")
        return json.loads(path.read_text(encoding="utf-8"))

    def render(self, template: str, titel: str, untertitel: str = "",
               bild: str | None = None, out_dir=None) -> Path:
        tpl = self._template(template)
        color = tpl.get("brand", BRAND)["colors"]
        font = tpl.get("brand", BRAND).get("font", BRAND["font"])
        width, height = tpl["breite"], tpl["hoehe"]
        stil = tpl.get("stil", "zentriert")
        title = html.escape(titel)
        sub = html.escape(untertitel)
        img_html = f'<img src="{html.escape(bild, quote=True)}" alt="">' if bild else ""
        align = {"zentriert": "center", "links": "flex-start", "geteilt": "space-between"}.get(
            stil, "center"
        )
        doc = f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
:root {{ --bg:{color['bg']}; --fg:{color['fg']}; --accent:{color['accent']}; --muted:{color['muted']}; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; width:{width}px; height:{height}px; background:var(--bg); color:var(--fg);
  font-family:{font}; display:flex; align-items:center; justify-content:{align}; overflow:hidden; }}
.card {{ width:100%; padding:8%; display:flex; flex-direction:column; gap:24px; }}
.brand {{ font-size:22px; letter-spacing:.28em; color:var(--accent); text-transform:uppercase; }}
h1 {{ margin:0; font-size:{max(38, width // 16)}px; line-height:1.05; }}
p {{ margin:0; font-size:{max(18, width // 44)}px; color:var(--muted); }}
.accent {{ width:120px; height:10px; background:var(--accent); border-radius:6px; }}
img {{ max-width:100%; max-height:{height // 2}px; object-fit:cover; border-radius:18px; }}
</style></head>
<body><main class="card">
<span class="brand">{html.escape(tpl.get('brand', BRAND)['name'])}</span>
<div class="accent"></div>
<h1>{title}</h1>
<p>{sub}</p>
{img_html}
</main></body></html>
"""
        out_dir = Path(out_dir or self.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in titel).strip() or template
        path = out_dir / f"{template}-{safe[:60]}.html"
        path.write_text(doc, encoding="utf-8")
        return path

    def batch_export(self, items, out_dir=None) -> list[str]:
        self.ensure_templates()
        exported = []
        for item in items:
            path = self.render(
                item.get("template", "social_post"), item.get("titel", ""),
                item.get("untertitel", ""), item.get("bild"),
                out_dir=out_dir,
            )
            exported.append(str(path))
        return exported

    def stats(self) -> dict:
        return {"templates": [t["name"] for t in self.list_templates()],
                "output_dir": str(self.output_dir)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="NEXUS Studio Export")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--template", default="social_post")
    ap.add_argument("--titel", default="Demo")
    ap.add_argument("--untertitel", default="")
    ap.add_argument("--bild", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--demo", action="store_true", help="3 Vorlagen exportieren")
    a = ap.parse_args(argv)
    st = Studio()
    if a.init:
        print("angelegt:", ", ".join(st.ensure_templates()) or "nichts")
    if a.demo:
        paths = st.batch_export([
            {"template": "social_post", "titel": "NEXUS Update", "untertitel": "Phase 7"},
            {"template": "thumbnail", "titel": "Selbstbau", "untertitel": "Validator zuerst"},
            {"template": "portfolio", "titel": "Portfolio", "untertitel": "Ausgewaehlte Arbeiten"},
        ])
        print(json.dumps(paths, indent=2, ensure_ascii=False))
    elif a.init is False:
        print(st.render(a.template, a.titel, a.untertitel, a.bild, out_dir=a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
