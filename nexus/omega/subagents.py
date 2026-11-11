"""OMEGA Layer 4: spezialisierte Subagenten.

Der Hauptagent delegiert Recherche und Code-Review, damit sein eigener Kontext
sauber bleibt. Beide Subagenten nutzen dieselbe Registry und damit dieselben
Guards/Logs wie der Hauptagent.
"""
from __future__ import annotations

import ast
import re

from nexus.omega import hooks, registry


def _terms(query: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[A-Za-z0-9\u00e4\u00f6\u00fc\u00df]{4,}", query or "")][:8]


def _excerpt(text: str, query: str, chars: int = 1_800) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    terms = _terms(query)
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    scored = []
    for para in paragraphs:
        low = para.lower()
        score = sum(1 for t in terms if t in low)
        if score:
            scored.append((score, para))
    if scored:
        scored.sort(key=lambda x: -x[0])
        out = "\n\n".join(p for _, p in scored[:3])
        return out[:chars]
    return text[:chars]


class WebResearcher:
    def __init__(self, invoke=None) -> None:
        self._invoke = invoke or registry.invoke

    def run(self, query: str, *, limit: int = 6, fetch_top: int = 2,
            run_id: str | None = None) -> dict:
        search = self._invoke("web_search", {"query": query, "limit": limit}, run_id=run_id)
        if not search.get("success"):
            return {"success": False, "status": search.get("status", "NO_RESULTS"),
                    "query": query, "error": search.get("error") or "Keine Treffer",
                    "search": search, "pages": [], "sources": [], "summary": ""}
        pages: list[dict] = []
        for hit in (search.get("results") or [])[:max(0, fetch_top)]:
            url = hit.get("url")
            if not url:
                continue
            page = self._invoke("fetch_page", {"url": url, "max_chars": 40_000}, run_id=run_id)
            if page.get("success"):
                pages.append({
                    "url": url,
                    "title": page.get("title") or hit.get("title"),
                    "excerpt": _excerpt(page.get("text") or "", query),
                    "suspicious": page.get("suspicious"),
                    "chars": page.get("chars"),
                })
        sources = [{"title": h.get("title"), "url": h.get("url")} for h in search.get("results") or []]
        lines = [f"Live-Recherche: {query}", ""]
        for i, hit in enumerate(search.get("results") or [], 1):
            lines.append(f"{i}. {hit.get('title')}")
            snippet = str(hit.get("snippet") or "").strip()
            if snippet:
                lines.append("   " + snippet[:300])
            lines.append("   " + str(hit.get("url")))
            page = next((p for p in pages if p["url"] == hit.get("url")), None)
            if page and page.get("excerpt"):
                lines.append("   Gelesen: " + re.sub(r"\s+", " ", page["excerpt"])[:500])
        lines += ["", f"Quellen: {len(sources)} (live, {search.get('source') or 'Web'})"]
        return {"success": True, "status": "OK", "query": query, "source": search.get("source"),
                "search": search, "pages": pages, "sources": sources,
                "summary": "\n".join(lines), "untrusted": True}


class CodeReviewer:
    """Statischer Reviewer: Syntax, gefaehrliche Muster, offensichtliche Risiken."""

    _HIGH = ("eval(", "exec(", "os.system(", "subprocess.", "shell=True", "pickle.loads(",
             "yaml.load(", "verify=False")

    def review(self, code: str) -> dict:
        text = str(code or "")
        lint = hooks.lint_python(text)
        findings: list[dict] = []
        for error in lint["errors"]:
            findings.append({"severity": "high", "issue": error})
        for warning in lint["warnings"]:
            findings.append({"severity": "medium", "issue": warning})
        for needle in self._HIGH:
            if needle in text:
                findings.append({"severity": "high", "issue": f"riskantes Muster: {needle}"})
        try:
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler) and node.type is None:
                    findings.append({"severity": "low", "issue": "bare except"})
                if isinstance(node, ast.FunctionDef) and len(node.body) > 80:
                    findings.append({"severity": "low",
                                     "issue": f"sehr lange Funktion: {node.name}"})
        except SyntaxError:
            pass
        high = [f for f in findings if f["severity"] == "high"]
        verdict = "pass" if lint["ok"] and not high else "needs_changes"
        return {"success": True, "status": "OK", "verdict": verdict,
                "lint": lint, "findings": findings,
                "high_count": len(high),
                "summary": ("Code besteht die statische Pruefung."
                            if verdict == "pass" else
                            f"{len(high)} hohe Befunde: " + "; ".join(f["issue"] for f in high)[:500])}
