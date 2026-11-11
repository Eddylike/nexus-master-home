# -*- coding: utf-8 -*-
"""Sprachbefehle fuer das Handy (Live Talk). Ausfuehrung per ADB, ausser Eddie sagt 'am PC'."""
from __future__ import annotations

import re
import time
from typing import Any

_PENDING_CALL: dict[str, Any] | None = None

_YES = re.compile(r"^\W*(ja|jaa|yes|ok|okay|mach|tu es|anrufen|ruf an|bitte)\W*$", re.I)
_NO = re.compile(r"^\W*(nein|no|abbrechen|nicht|lass|stopp|stop)\W*$", re.I)

_NUM = {
    "null": 0, "eins": 1, "ein": 1, "eine": 1, "zwei": 2, "drei": 3, "vier": 4,
    "fuenf": 5, "fünf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9,
    "zehn": 10, "elf": 11, "zwoelf": 12, "zwölf": 12, "viertelstunde": 15,
    "zwanzig": 20, "dreissig": 30, "dreißig": 30, "45": 45, "sechzig": 60,
}


def _norm(text: str) -> str:
    t = str(text or "").strip()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
        t = t.replace(a.upper(), b)
    t = re.sub(r"\s+", " ", t)
    return t.strip().rstrip(".!?,;:")


def _num_word(s: str) -> int | None:
    s = s.strip().lower()
    if re.fullmatch(r"\d+", s):
        return int(s)
    return _NUM.get(s)


def parse(text: str) -> dict | None:
    """Erkennt uebliche Handy-Assistenten-Befehle. None = kein Handy-Befehl."""
    raw = str(text or "").strip()
    if not raw:
        return None
    t = _norm(raw)
    low = t.lower()

    global _PENDING_CALL
    if _PENDING_CALL and time.time() - float(_PENDING_CALL.get("ts") or 0) < 75:
        if _YES.fullmatch(low):
            return {"intent": "phone_call_confirm", "text": t}
        if _NO.fullmatch(low):
            return {"intent": "phone_call_cancel", "text": t}

    # Mediensteuerung (kurz)
    if re.fullmatch(r"(pause|pausieren|anhalten|halt|stopp|stop|stoppe)", low):
        return {"intent": "phone_media", "action": "pause" if "pause" in low or "anhalten" in low else "stop", "text": t}
    if re.fullmatch(r"(weiter|spiel(?:e)?\s+(?:weiter|das(?:\s+ab)?|ab)|abspielen|play)", low):
        return {"intent": "phone_media", "action": "play", "text": t}
    if re.search(r"\b(naechste(?:r|s)?|nächste(?:r|s)?|skip|weiter\s+(?:titel|lied|song|track))\b", low) and len(low.split()) <= 5:
        if not re.search(r"\b(spiel|youtube)\b", low):
            return {"intent": "phone_media", "action": "next", "text": t}
    if re.search(r"\b(vorherige(?:r|s)?|letzter titel|vorheriger titel|zurück zum vorherigen)\b", low):
        return {"intent": "phone_media", "action": "previous", "text": t}

    # Lautstaerke
    if re.search(r"\b(lauter|leiser|stumm|ton aus|stummschalten)\b", low) and not re.search(r"\b(pc|rechner)\b", low):
        act = "down" if "leiser" in low else "mute" if ("stumm" in low or "ton aus" in low) else "up"
        return {"intent": "phone_volume", "action": act, "text": t}

    # YouTube: "spiel X auf YouTube" / "such X auf YouTube"
    m = re.search(r"(?:spiel(?:e)?|such(?:e)?)\s+(.+?)\s+auf\s+youtube\b", low)
    if m:
        q = m.group(1).strip()
        q = re.sub(r"^(mal\s+|bitte\s+)", "", q)
        if q and q not in ("es", "das", "ab"):
            return {"intent": "youtube_play", "query": q, "text": t}

    # "spiel Musik" = Player starten; "spiel techno" / "spiel <Song>" = YouTube wirklich abspielen
    m = re.match(r"^spiel(?:e)?\s+(?:mal\s+)?(.+)$", low)
    if m:
        rest = m.group(1).strip()
        rest = re.sub(r"\s+ab$", "", rest).strip()
        if rest in ("musik", "die musik", "musik ab"):
            return {"intent": "start_music", "text": t}
        if rest in ("weiter", "das", "es", "ab"):
            return {"intent": "phone_media", "action": "play", "text": t}
        if rest:
            return {"intent": "youtube_play", "query": rest, "text": t}

    if re.fullmatch(r"(mach(?:e)?\s+)?(die\s+)?musik(\s+an)?|starte\s+musik|musik\s+an", low):
        return {"intent": "start_music", "text": t}

    # Navigation zurueck / home
    if re.fullmatch(r"(zurueck|zurück|geh(?:e)?\s+zurueck|eine\s+seite\s+zurueck|back)", low):
        return {"intent": "phone_nav", "action": "back", "text": t}
    if re.fullmatch(r"(home|startbildschirm|zum\s+start(?:bildschirm)?|homescreen|auf\s+den\s+homescreen)", low):
        return {"intent": "phone_nav", "action": "home", "text": t}
    if re.search(r"\b(letzte apps|app.uebersicht|app.übersicht|zuletzt geöffnet)\b", low):
        return {"intent": "phone_nav", "action": "recents", "text": t}

    # App schliessen
    m = re.match(r"^(?:schliess(?:e)?|schliesse|mach\s+zu|beende)\s+(?:die\s+app\s+)?(.+)$", low)
    if m:
        app = re.sub(r"\s+(app|bitte)$", "", m.group(1)).strip()
        if app:
            return {"intent": "close_phone_app", "app": app, "text": t}

    # Taschenlampe
    if re.search(r"\btaschenlampe\b", low):
        on = not re.search(r"\b(aus|ausmachen|ausschalten)\b", low)
        return {"intent": "phone_flashlight", "on": on, "text": t}

    # Wecker
    m = re.search(r"\bwecker\b.*?\b(?:auf|um|fuer|für)\s+(\d{1,2})(?:[:.](\d{2}))?\s*(?:uhr)?", low)
    if not m:
        m = re.search(r"\bstell(?:e)?\s+(?:einen\s+)?wecker\s+(?:auf|um)\s+(\d{1,2})(?:[:.](\d{2}))?", low)
    if m:
        return {"intent": "set_alarm", "hour": int(m.group(1)), "minute": int(m.group(2) or 0), "text": t}

    # Timer
    m = re.search(r"\btimer\b.*?(?:auf\s+)?(\d+|[a-zäöüß]+)\s*(sekunden|sekunde|minuten|minute|stunden|stunde)?", low)
    if m and re.search(r"\btimer\b", low):
        n = _num_word(m.group(1) or "")
        unit = (m.group(2) or "minuten").lower()
        if n is not None:
            sec = n
            if unit.startswith("min"):
                sec = n * 60
            elif unit.startswith("stund"):
                sec = n * 3600
            return {"intent": "set_timer", "seconds": sec, "text": t}

    # Anrufen (nur vorbereiten)
    m = re.match(r"^(?:ruf(?:e)?|anrufen)\s+(?:mal\s+)?(.+?)(?:\s+an)?$", low)
    if m and not re.search(r"\b(youtube|musik)\b", low):
        who = re.sub(r"\s+an$", "", m.group(1)).strip()
        who = re.sub(r"^(an|bei)\s+", "", who)
        if who and who not in ("an", "mal"):
            return {"intent": "phone_call_prepare", "who": who, "text": t}

    # Uhrzeit / Datum lokal
    if re.search(r"\b(wie\s+spaet|wie\s+spät|wieviel\s+uhr|wie\s+viel\s+uhr|welche\s+uhrzeit)\b", low):
        return {"intent": "phone_time", "text": t}
    if re.search(r"\b(welches\s+datum|welcher\s+tag|was\s+fuer\s+ein\s+tag|was für ein tag)\b", low):
        return {"intent": "phone_date", "text": t}

    # Akku
    if re.search(r"\b(akku|batterie|ladestand)\b", low):
        return {"intent": "phone_battery", "text": t}

    # Screenshot
    if re.search(r"\b(screenshot|bildschirmfoto|foto vom bildschirm)\b", low):
        return {"intent": "phone_screenshot", "text": t}

    # Websuche auf dem Handy
    m = re.match(r"^(?:google(?:r)?|such(?:e)?(?:\s+im\s+internet)?|im\s+web\s+nach)\s+(?:nach\s+)?(.+)$", low)
    if m and "youtube" not in low:
        return {"intent": "phone_web_search", "query": m.group(1).strip(), "text": t}

    # Navigation
    m = re.search(r"\b(?:navigiere|navigation|zeig(?:e)?\s+(?:mir\s+)?den\s+weg)\s+(?:nach|zu)\s+(.+)$", low)
    if m:
        return {"intent": "phone_navigate", "place": m.group(1).strip(), "text": t}

    # Bildschirm
    if re.search(r"\b(bildschirm\s+an|weck(?:e)?\s+das\s+handy|display\s+an)\b", low):
        return {"intent": "phone_keyevent", "code": "224", "text": t, "reply": "Bildschirm an."}
    if re.search(r"\b(bildschirm\s+aus|display\s+aus|handy\s+sperren)\b", low):
        return {"intent": "phone_keyevent", "code": "223", "text": t, "reply": "Bildschirm aus."}

    # Kamera
    if re.search(r"\b(mach(?:e)?\s+(?:ein\s+)?foto|oeffne\s+die\s+kamera|öffne\s+die\s+kamera)\b", low):
        return {"intent": "open_phone_app", "app": "kamera", "text": t}

    return None


def run(cmd: dict, source: str = "livetalk") -> dict:
    """Fuehrt einen von parse() erkannten Befehl aus."""
    from nexus import phone_actions as pa
    intent = cmd.get("intent")
    global _PENDING_CALL

    if intent == "youtube_play":
        return pa.youtube_play(str(cmd.get("query") or ""), source=source)
    if intent == "start_music":
        return pa.start_music(source=source)
    if intent == "phone_media":
        return pa.phone_media(str(cmd.get("action") or "play"), source=source)
    if intent == "phone_volume":
        return pa.phone_volume(str(cmd.get("action") or "up"), source=source)
    if intent == "phone_nav":
        return pa.phone_nav(str(cmd.get("action") or "back"), source=source)
    if intent == "close_phone_app":
        return pa.close_phone_app(str(cmd.get("app") or ""), source=source)
    if intent == "phone_flashlight":
        return pa.phone_flashlight(bool(cmd.get("on")), source=source)
    if intent == "set_alarm":
        return pa.set_alarm(int(cmd.get("hour") or 0), int(cmd.get("minute") or 0), source=source)
    if intent == "set_timer":
        return pa.set_timer(int(cmd.get("seconds") or 0), source=source)
    if intent == "phone_web_search":
        return pa.phone_web_search(str(cmd.get("query") or ""), source=source)
    if intent == "phone_navigate":
        return pa.phone_navigate(str(cmd.get("place") or ""), source=source)
    if intent == "phone_keyevent":
        r = pa.phone_keyevent(str(cmd.get("code") or ""), source=source)
        if r.get("success") and cmd.get("reply"):
            r["reply"] = cmd["reply"]
        return r
    if intent == "phone_battery":
        return pa.phone_battery(source=source)
    if intent == "phone_screenshot":
        return pa.phone_screenshot(source=source)
    if intent == "open_phone_app":
        return pa.open_phone_app(str(cmd.get("app") or ""), source=source)
    if intent == "phone_time":
        from nexus.livetalk.agents import _now_berlin
        return {"success": True, "reply": _now_berlin()}
    if intent == "phone_date":
        from nexus.livetalk.agents import _now_berlin
        # _now_berlin enthaelt Datum und Uhrzeit
        return {"success": True, "reply": _now_berlin()}
    if intent == "phone_call_prepare":
        r = pa.phone_call_prepare(str(cmd.get("who") or ""), source=source)
        if r.get("pending"):
            _PENDING_CALL = {"name": r.get("name"), "number": r.get("number"), "ts": time.time()}
        return r
    if intent == "phone_call_confirm":
        if not _PENDING_CALL:
            return {"success": False, "reply": "Es liegt kein Anruf zur Bestaetigung vor."}
        rec = _PENDING_CALL
        _PENDING_CALL = None
        return pa.phone_call_confirm(str(rec.get("number") or ""), str(rec.get("name") or ""), source=source)
    if intent == "phone_call_cancel":
        _PENDING_CALL = None
        return {"success": True, "reply": "Alles klar, ich rufe nicht an."}
    return {"success": False, "reply": "Diese Handy-Aktion kenne ich so nicht."}