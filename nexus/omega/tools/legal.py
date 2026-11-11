"""Legal-Tools: EUR-Lex und offene Rechtsquellen (nur echte Treffer)."""
from __future__ import annotations

import re

from nexus.omega.sanitize import sanitize_url, sanitize_untrusted
from nexus.omega.tools import _http
from nexus.omega.tools.web import extract_text


def eu_lex(params: dict, ctx: dict) -> dict:
    celex = re.sub(r"[^0-9A-Za-z()_-]", "", str(params.get("celex") or ""))[:40]
    if not celex:
        return {"success": False, "status": "INVALID_PARAMS", "error": "celex_required"}
    lang = str(params.get("language") or "DE").upper()
    if lang not in ("DE", "EN", "FR"):
        lang = "DE"
    url = f"https://eur-lex.europa.eu/legal-content/{lang}/TXT/?uri=CELEX:{celex}"
    safe = sanitize_url(url)
    resp = _http.request("GET", safe, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "de-DE,en;q=0.8"},
                         timeout=30, max_bytes=4_000_000)
    if not resp.get("success"):
        return {"success": False, "status": resp.get("status", "FETCH_FAILED"),
                "error": resp.get("error") or "eurlex_fetch_failed", "url": url}
    extracted = extract_text(resp.get("body") or "", 120_000)
    clean = sanitize_untrusted(extracted["text"])
    if not clean["text"].strip():
        return {"success": False, "status": "EMPTY", "error": "Kein lesbarer Rechtstext extrahiert",
                "url": url}
    return {"success": True, "status": "OK", "celex": celex, "language": lang,
            "title": extracted["title"] or f"CELEX {celex}", "url": url,
            "text": clean["text"], "suspicious": clean["suspicious"],
            "findings": clean["findings"], "chars": len(clean["text"]),
            "untrusted": True, "source": "EUR-Lex"}


def legal_search(params: dict, ctx: dict) -> dict:
    query = str(params.get("query") or "").strip()
    limit = max(1, min(int(params.get("limit") or 5), 10))
    if not query:
        return {"success": False, "status": "INVALID_PARAMS", "error": "query_required"}
    from nexus.knowledge.research import Research

    research = Research()
    hits: list[dict] = []
    tried: list[str] = []
    for scoped in (f"{query} site:eur-lex.europa.eu", f"{query} site:gesetze-im-internet.de"):
        res = research.search(scoped, limit)
        tried.append(scoped)
        for item in res.get("results") or []:
            url = sanitize_url(item.get("url"))
            if url and all(url != h["url"] for h in hits):
                hits.append({
                    "title": sanitize_untrusted(item.get("title"), max_chars=500)["text"],
                    "url": url,
                    "snippet": sanitize_untrusted(item.get("snippet"), max_chars=1_500)["text"],
                })
        if len(hits) >= limit:
            break
    return {"success": bool(hits), "status": "OK" if hits else "NO_RESULTS",
            "query": query, "results": hits[:limit], "count": min(len(hits), limit),
            "tried": tried, "untrusted": True}


HANDLERS = {"eu_lex": eu_lex, "legal_search": legal_search}
