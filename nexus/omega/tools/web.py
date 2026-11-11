"""Web-Tools: Live-Suche, Seitenabruf, RAG."""
from __future__ import annotations

import html as html_lib
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

from nexus.omega import rag, ratelimit
from nexus.omega.sanitize import sanitize_untrusted, sanitize_url, wrap_untrusted
from nexus.omega.tools import _http

_UA = "Mozilla/5.0 (compatible; NEXUS-OMEGA/1.0; +local)"
_MAX_PAGE = 120_000

_SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "template", "form", "nav", "footer"}


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section", "article"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
        elif not self._skip:
            self.parts.append(data)


def _collapse(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_text(html: str, max_chars: int = _MAX_PAGE) -> dict:
    title = ""
    description = ""
    text = ""
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(html or "", "html.parser")
        if soup.title and soup.title.string:
            title = str(soup.title.string).strip()
        meta = soup.find("meta", attrs={"name": re.compile("description", re.I)})
        if meta and meta.get("content"):
            description = str(meta.get("content")).strip()
        for tag in soup(list(_SKIP_TAGS)):
            tag.decompose()
        main = soup.find("main") or soup.find("article") or soup.body or soup
        text = _collapse(main.get_text("\n", strip=True))
    except Exception:
        parser = _TextParser()
        try:
            parser.feed(html or "")
            parser.close()
            title = _collapse("".join(parser.title_parts))
            text = _collapse("\n".join(parser.parts))
        except Exception:
            text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html or "")
            text = _collapse(html_lib.unescape(re.sub(r"(?s)<[^>]+>", " ", text)))
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[OMEGA: Text gekuerzt]"
    return {"title": title[:500], "description": description[:1000], "text": text}


def handlers_map():
    return {}


def web_search(params: dict, ctx: dict) -> dict:
    query = str(params.get("query") or "").strip()
    limit = int(params.get("limit") or 6)
    news = bool(params.get("news"))
    cache_key = f"omega:web_search:{query}:{limit}:{news}"
    cached = ratelimit.cache_get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}
    from nexus.knowledge.research import Research

    res = Research().search(query, limit, news=news)
    hits = []
    for item in (res.get("results") or [])[:limit]:
        url = sanitize_url(item.get("url"))
        if not url:
            continue
        title = sanitize_untrusted(item.get("title"), max_chars=500)
        snippet = sanitize_untrusted(item.get("snippet"), max_chars=1_500)
        hits.append({
            "title": title["text"],
            "url": url,
            "snippet": snippet["text"],
            "suspicious": title["suspicious"] or snippet["suspicious"],
        })
    result = {
        "success": bool(res.get("success")) and bool(hits),
        "status": "OK" if hits else "NO_RESULTS",
        "query": query,
        "source": res.get("source"),
        "tried": res.get("tried") or [],
        "results": hits,
        "count": len(hits),
        "untrusted": True,
        "error": None if hits else str(res.get("error") or "no_verified_results"),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if hits:
        ratelimit.cache_set(cache_key, result, 120)
    return result


def fetch_page(params: dict, ctx: dict) -> dict:
    url = sanitize_url(params.get("url"))
    if not url:
        return {"success": False, "status": "BAD_URL", "error": "Ungueltige oder unsichere URL"}
    max_chars = int(params.get("max_chars") or _MAX_PAGE)
    resp = _http.request("GET", url, headers={"User-Agent": _UA, "Accept-Language": "de-DE,en;q=0.8"},
                         timeout=25, max_bytes=3_000_000)
    if not resp.get("success"):
        return {"success": False, "status": resp.get("status", "FETCH_FAILED"),
                "error": resp.get("error") or "fetch_failed", "status_code": resp.get("status_code"),
                "url": url}
    content_type = str(resp.get("content_type") or "text/html")
    body = resp.get("body") or ""
    if "html" in content_type.lower() or body.lstrip()[:1] == "<":
        extracted = extract_text(body, max_chars)
        title = extracted["title"] or url
        text = extracted["text"]
        description = extracted["description"]
    else:
        title = url
        text = body[:max_chars]
        description = ""
    wrapped = wrap_untrusted(text, source=url, max_chars=max_chars)
    saved = None
    if params.get("save"):
        doc = rag.add_document(url, title, wrapped["text"], {"description": description,
                                                             "suspicious": wrapped["suspicious"]})
        saved = doc.get("id") if doc.get("success") else None
    return {
        "success": bool(text.strip()),
        "status": "OK" if text.strip() else "EMPTY",
        "url": url,
        "title": title,
        "description": description,
        "text": wrapped["text"],
        "wrapped": wrapped["wrapped"],
        "suspicious": wrapped["suspicious"],
        "findings": wrapped["findings"],
        "truncated": wrapped["truncated"],
        "chars": len(wrapped["text"]),
        "saved_doc_id": saved,
        "untrusted": True,
        "status_code": resp.get("status_code"),
    }


def rag_add(params: dict, ctx: dict) -> dict:
    content = str(params.get("content") or "")
    clean = sanitize_untrusted(content)
    doc = rag.add_document(str(params.get("source") or "manual"),
                           str(params.get("title") or ""), clean["text"],
                           {**dict(params.get("meta") or {}), "suspicious": clean["suspicious"],
                            "findings": clean["findings"]})
    if doc.get("success"):
        doc["suspicious"] = clean["suspicious"]
    return doc


def rag_search(params: dict, ctx: dict) -> dict:
    query = str(params.get("query") or "").strip()
    hits = rag.search(query, int(params.get("limit") or 5))
    for hit in hits:
        hit["snippet"] = sanitize_untrusted(hit.get("snippet"), max_chars=2_000)["text"]
    return {"success": bool(hits), "status": "OK" if hits else "NO_RESULTS",
            "query": query, "results": hits, "count": len(hits), "untrusted": True}


HANDLERS = {
    "web_search": web_search,
    "fetch_page": fetch_page,
    "rag_add": rag_add,
    "rag_search": rag_search,
}
