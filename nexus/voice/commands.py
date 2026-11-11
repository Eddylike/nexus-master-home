"""NEXUS Sprachbefehle (heynexus-20261006).

POST /api/voice/command  {text | audio_b64, alt_texts?, source?, chat_fallback?, llm_fallback?}
POST /api/voice/confirm  {id, answer}
GET  /api/voice/wakeword/state      (Pause-/Ein-Aus-Status fuer den Weckwort-Dienst)
POST /api/voice/wakeword/config     {enabled}
POST /api/voice/wakeword/heartbeat  (vom Dienst: pid, rss_mb, cpu_pct, state)
POST /api/voice/live_state          {active}   (Browser-Live-Funktion an/aus)
GET  /api/voice/commands/status

Intents: open_pc_app | open_phone_app | whatsapp_send | chat
Erkennung zuerst per Regeln/Regex, LLM nur als Fallback (nur wenn Schluesselwoerter vorkommen).
WhatsApp wird NIE ohne bestaetigtes "Ja" gesendet (ausstehende Bestaetigung mit ID, Ablauf 60 s).
"""
from __future__ import annotations

import base64
import difflib
import json
import re
import tempfile
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from nexus import phone_actions as pa

ROOT = Path(__file__).resolve().parents[2]
WAKE_CFG = ROOT / "workspace" / "voice" / "wakeword.json"
PENDING_TTL = 60.0
LIVE_TTL = 45.0

_lock = threading.RLock()
_pending: dict[str, dict[str, Any]] = {}
_state: dict[str, Any] = {"live_until": 0.0, "tts_until": 0.0, "service": {}, "last_command": None}

# ----------------------------------------------------------------- text utils
_WAKE_RE = re.compile(
    r"^\W*(?:(?:hey|hei|hi|hai|he|herr|hallo|ok|okay|hier|hej)\W+)?"
    r"(?:in\s*nexus|ein\s*nexus|nexus|nexxus|nexis|neksus|necksus|next\s+us|next\s+source|nexa|sexus|plexus|lexus|next)\b[\s,.:;!?\-]*",
    re.IGNORECASE,
)


def strip_wake(text: str) -> str:
    t = (text or "").strip()
    t2 = _WAKE_RE.sub("", t, count=1).strip()
    return t2


def _fold(s: str) -> str:
    s = (s or "").lower().replace("ß", "ss")
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def _clean(s: str) -> str:
    s = _fold(s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _tidy_message(t: str) -> str:
    t = (t or "").strip().strip(" ,:;-\"'„“”")
    t = re.sub(r"^(?:dass|das|folgendes|text)\W+", "", t, flags=re.IGNORECASE).strip()
    if t and t[0].islower():
        t = t[0].upper() + t[1:]
    return t


# ----------------------------------------------------------------- PC apps
PC_ALIASES = {
    "spotify": "spotify", "chrome": "chrome", "google chrome": "chrome", "edge": "edge",
    "microsoft edge": "edge", "brave": "brave", "firefox": "firefox", "explorer": "explorer",
    "datei explorer": "explorer", "dateiexplorer": "explorer", "windows explorer": "explorer",
    "notepad": "notepad", "editor": "notepad", "notizblock": "notepad", "texteditor": "notepad",
    "rechner": "rechner", "taschenrechner": "rechner", "calculator": "rechner", "calc": "rechner",
    "fotos": "fotos", "vlc": "vlc", "media player": "wmplayer", "windows media player": "wmplayer",
    "whatsapp": "whatsapp", "whats app": "whatsapp", "discord": "discord", "telegram": "telegram",
    "steam": "steam", "vs code": "vscode", "vscode": "vscode", "visual studio code": "vscode",
    "cursor": "cursor", "youtube": "youtube", "google maps": "maps", "maps": "maps", "gmail": "gmail",
    "kalender": "calendar", "google kalender": "calendar", "google drive": "drive", "drive": "drive",
    "google docs": "docs", "teams": "teams", "microsoft teams": "teams",
}
_ARTICLES = r"^(?:bitte|mal|mir|mich|den|die|das|dem|der|meinen|meine|mein|einen|eine|ein|app|programm)\s+"


def resolve_pc_app(name: str) -> str | None:
    n = _clean(name)
    for _ in range(4):
        n2 = re.sub(_ARTICLES, "", n).strip()
        if n2 == n:
            break
        n = n2
    n = re.sub(r"\s+(app|programm|bitte|mal|auf)$", "", n).strip()
    if not n:
        return None
    if n in PC_ALIASES:
        return PC_ALIASES[n]
    m = difflib.get_close_matches(n, list(PC_ALIASES.keys()), n=1, cutoff=0.8)
    return PC_ALIASES[m[0]] if m else None


def open_pc_app(app_id: str, engine) -> dict[str, Any]:
    try:
        apps = engine.master.apps
        if apps.resolve_app(app_id):
            r = apps.open(app_id)
            label = (r.get("app") or {}).get("name") or app_id
            ok = bool(r.get("success"))
            return {"success": ok, "label": label, "detail": {"resolved_id": r.get("resolved_id")},
                    "reply": f"Ich öffne {label}." if ok else f"Ich konnte {label} nicht öffnen."}
        from nexus.computer.os_actions import APP_MAP, get_os_actions
        if app_id in APP_MAP:
            r = get_os_actions().launch_app(app_id)
            ok = bool(r.get("success"))
            label = app_id.capitalize()
            return {"success": ok, "label": label,
                    "reply": f"Ich öffne {label}." if ok else f"Ich konnte {label} nicht öffnen."}
    except Exception as e:
        return {"success": False, "error": type(e).__name__, "reply": "Beim Öffnen ist ein Fehler passiert."}
    return {"success": False, "error": "unknown_app", "reply": "Diese App kenne ich am PC nicht."}


# ----------------------------------------------------------------- parsing
_OPEN = r"(?:öffne|oeffne|öffner|öffnen|öffnet|offne|starte|startet|start|mach|zeig|zeige|lade)"
_PHONE = r"(?:handy|telefon|smartphone|phone|mobiltelefon)"
_WRITE = r"(?:schreib|schreibe|schreibt|schreiben|schick|schicke|schickt|schicken|sende|send|senden|sag|sage|sagt)"
_WA = r"(?:whats\s?app|whatsapp|watsapp|whatsup|whats\s?up|what's\s?app)"
_VIA = r"(?:auf|per|über|ueber|via|bei|in|mit)"

RE_PHONE_OPEN_1 = re.compile(
    rf"^(?:bitte\s+)?{_OPEN}\s+(?:mir\s+)?(?:bitte\s+)?(?P<app>.+?)\s+(?:auf|am|im)\s+(?:dem\s+|meinem\s+|mein\s+|das\s+)?{_PHONE}(?:\s+(?:auf|an))?\W*$",
    re.IGNORECASE)
RE_PHONE_OPEN_2 = re.compile(
    rf"^(?:bitte\s+)?(?:auf|am)\s+(?:dem\s+|meinem\s+)?{_PHONE}\W+{_OPEN}\s+(?:mir\s+)?(?P<app>.+?)(?:\s+auf)?\W*$",
    re.IGNORECASE)
RE_PHONE_OPEN_3 = re.compile(
    rf"^(?:bitte\s+)?{_OPEN}\s+(?:auf|am)\s+(?:dem\s+|meinem\s+)?{_PHONE}\s+(?P<app>.+?)\W*$", re.IGNORECASE)
RE_PC_OPEN = re.compile(
    rf"^(?:bitte\s+)?{_OPEN}\s+(?:mir\s+)?(?:bitte\s+)?(?P<app>.+?)(?:\s+(?:am|auf\s+dem|auf\s+meinem)\s+(?:pc|computer|laptop|desktop))?(?:\s+auf)?\W*$",
    re.IGNORECASE)
RE_WA_1 = re.compile(
    rf"^(?:bitte\s+)?{_WRITE}\s+(?:mal\s+)?(?:eine?\s+(?:nachricht|whatsapp)\s+)?(?:an\s+)?(?P<name>.+?)\s+{_VIA}\s+{_WA}(?:\W+(?P<text>.*))?$",
    re.IGNORECASE)
RE_WA_2 = re.compile(
    rf"^(?:bitte\s+)?{_WRITE}\s+(?:mal\s+)?(?:eine?\s+nachricht\s+)?{_VIA}\s+{_WA}\s+(?:an\s+)?(?P<name>[^,:;.!?]+?)(?:\s*[,:;.!?]\s*|\s+(?:dass|das)\s+)(?P<text>.+)$",
    re.IGNORECASE)
RE_WA_3 = re.compile(
    rf"^(?:bitte\s+)?(?:{_WA}|eine?\s+{_WA}|eine?\s+nachricht)\s+an\s+(?P<name>[^,:;.!?]+?)(?:\s*[,:;.!?]\s*|\s+(?:dass|das)\s+)(?P<text>.+)$",
    re.IGNORECASE)
RE_WA_4 = re.compile(
    rf"^(?:bitte\s+)?{_WRITE}\s+(?:mal\s+)?(?:eine?\s+nachricht\s+)?{_VIA}\s+{_WA}\s+an\s+(?P<name>\S+(?:\s+\S+)?)\W*$",
    re.IGNORECASE)

KEYWORDS = re.compile(rf"\b(?:{_OPEN}|{_WRITE}|{_WA}|{_PHONE}|app)\b", re.IGNORECASE)


def _clean_name(n: str) -> str:
    n = (n or "").strip(" ,.:;!?\"'")
    n = re.sub(r"^(?:an|zu|der|die|dem|den|meiner|meinem|meinen|meine|mein)\s+", "", n, flags=re.IGNORECASE)
    return n.strip()


def parse(text: str) -> dict[str, Any]:
    t = strip_wake(text)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return {"intent": "empty", "text": ""}
    t_noend = t.rstrip(" .!?")
    for rx in (RE_WA_2, RE_WA_3, RE_WA_1, RE_WA_4):
        m = rx.match(t_noend)
        if m:
            name = _clean_name(m.group("name"))
            msg = _tidy_message(m.groupdict().get("text") or "")
            if name and len(name) <= 40:
                return {"intent": "whatsapp_send", "contact": name, "message": msg, "text": t}
    for rx in (RE_PHONE_OPEN_1, RE_PHONE_OPEN_2, RE_PHONE_OPEN_3):
        m = rx.match(t_noend)
        if m:
            app = m.group("app").strip()
            if pa.find_phone_app(app):
                return {"intent": "open_phone_app", "app": app, "text": t}
            return {"intent": "open_phone_app", "app": app, "unknown": True, "text": t}
    m = RE_PC_OPEN.match(t_noend)
    if m:
        app_id = resolve_pc_app(m.group("app"))
        if app_id:
            return {"intent": "open_pc_app", "app": app_id, "text": t}
    return {"intent": "chat", "text": t}


def _llm_parse(text: str, engine) -> dict[str, Any] | None:
    """Fallback ueber das lokale Modell. Nur gueltige, validierte Felder werden uebernommen."""
    try:
        sysmsg = (
            "Du klassifizierst deutsche Sprachbefehle. Antworte NUR mit JSON, ohne Erklaerung. "
            'Schema: {"intent":"open_pc_app|open_phone_app|whatsapp_send|chat","app":"","contact":"","message":""}. '
            "open_phone_app nur wenn Handy/Telefon erwaehnt wird. whatsapp_send nur bei WhatsApp-Nachricht."
        )
        r = engine.master.llm.chat([{"role": "system", "content": sysmsg}, {"role": "user", "content": text}],
                                   temperature=0.0)
        raw = (r or {}).get("reply") or ""
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        d = json.loads(m.group(0))
    except Exception:
        return None
    it = str(d.get("intent") or "")
    if it == "open_pc_app":
        app_id = resolve_pc_app(str(d.get("app") or ""))
        if app_id:
            return {"intent": "open_pc_app", "app": app_id, "text": text, "via": "llm"}
    if it == "open_phone_app" and pa.find_phone_app(str(d.get("app") or "")):
        return {"intent": "open_phone_app", "app": str(d.get("app")), "text": text, "via": "llm"}
    if it == "whatsapp_send" and str(d.get("contact") or "").strip():
        return {"intent": "whatsapp_send", "contact": _clean_name(str(d["contact"]))[:40],
                "message": _tidy_message(str(d.get("message") or ""))[:1000], "text": text, "via": "llm"}
    return None


# ----------------------------------------------------------------- STT (wiederverwendet)
def _stt_prompt() -> str:
    names = []
    try:
        names = [c["name"] for c in pa._load_local_contacts()][:20]
    except Exception:
        pass
    base = ("Hey Nexus, öffne Spotify. Hey Nexus, öffne WhatsApp auf dem Handy. "
            "Hey Nexus, schreib Mama auf WhatsApp, ich komme später. Ja, senden. Nein, abbrechen.")
    if names:
        base += " Kontakte: " + ", ".join(names) + "."
    return base


def transcribe_audio(raw: bytes, filename: str = "audio.wav") -> dict[str, Any]:
    """Nutzt die vorhandene Server-STT (nexus.voice.stt, faster-whisper tiny) mit Befehls-Prompt."""
    from nexus.voice import stt as stt_mod
    if not stt_mod.status().get("available"):
        return {"success": False, "error": "stt_unavailable"}
    t0 = time.time()
    tmp = None
    try:
        model = stt_mod._ensure_model("tiny")
        with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix or ".wav", delete=False) as tf:
            tf.write(raw)
            tmp = tf.name
        segs, info = model.transcribe(tmp, language="de", beam_size=1, initial_prompt=_stt_prompt(),
                                      condition_on_previous_text=False)
        text = " ".join((s.text or "").strip() for s in segs).strip()
        return {"success": True, "text": text, "ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return {"success": False, "error": type(e).__name__}
    finally:
        if tmp:
            try:
                Path(tmp).unlink(missing_ok=True)
            except Exception:
                pass


# ----------------------------------------------------------------- pending
def _gc() -> None:
    now = time.time()
    with _lock:
        for k in [k for k, v in _pending.items() if v["expires"] < now]:
            _pending.pop(k, None)


def _new_pending(kind: str, data: dict[str, Any], prompt: str) -> dict[str, Any]:
    _gc()
    pid = uuid.uuid4().hex[:12]
    with _lock:
        _pending[pid] = {"id": pid, "kind": kind, "data": data, "created": time.time(),
                         "expires": time.time() + PENDING_TTL}
    return {"id": pid, "kind": kind, "expires_in": int(PENDING_TTL), "prompt": prompt}


_NO_RE = re.compile(r"\b(nein|nee|ne|no|nö|noe|abbrechen|abbruch|stopp|stop|halt|verwerfen|nicht|lass|vergiss|cancel|falsch)\b")
_YES_RE = re.compile(r"\b(ja|jawohl|jo|jop|jup|yes|genau|richtig|okay|ok|klar|senden|sende|schick|abschicken|passt|bestaetigt|bestätigt|mach)\b")


def classify_answer(answer: str) -> str:
    a = _fold(strip_wake(answer or ""))
    a = re.sub(r"[^a-z ]", " ", a)
    if not a.strip() or a.strip() in ("timeout", "stille"):
        return "timeout" if a.strip() in ("timeout", "stille") else "unclear"
    if _NO_RE.search(a):
        return "no"
    if _YES_RE.search(a):
        return "yes"
    return "unclear"


def _confirm_prompt(name: str, msg: str) -> str:
    return f"Nachricht an {name}: {msg}. Soll ich senden? Sag ja oder nein."


def _whatsapp_flow(parsed: dict[str, Any], source: str) -> dict[str, Any]:
    dev = pa.select_device()
    if dev["status"] != "OK":
        return {"success": False, "intent": "whatsapp_send", "status": "UNAVAILABLE",
                "error": dev.get("reason"), "reply": dev["reply"]}
    res = pa.resolve_contact(parsed["contact"], dev["serial"])
    matches = res["matches"]
    if not matches:
        return {"success": False, "intent": "whatsapp_send", "error": "contact_not_found",
                "reply": f"Ich habe keinen Kontakt {parsed['contact']} gefunden. Trag ihn bitte in die Kontaktliste ein."}
    data = {"message": parsed.get("message") or "", "source": source}
    if not res["unique"]:
        cands = matches[:3]
        names = [c["name"] for c in cands]
        data["candidates"] = cands
        prompt = "Ich habe mehrere Kontakte gefunden: " + ", ".join(names[:-1]) + " oder " + names[-1] + ". Wen meinst du?"
        return {"success": True, "intent": "whatsapp_send", "reply": prompt,
                "confirm": _new_pending("choose_contact", data, prompt), "candidates": names}
    c = matches[0]
    data.update({"name": c["name"], "number": c["number"]})
    if not data["message"]:
        prompt = f"Was soll ich {c['name']} schreiben?"
        return {"success": True, "intent": "whatsapp_send", "reply": prompt,
                "confirm": _new_pending("dictate", data, prompt), "recipient": c["name"]}
    prompt = _confirm_prompt(c["name"], data["message"])
    return {"success": True, "intent": "whatsapp_send", "reply": prompt, "recipient": c["name"],
            "message_preview": data["message"], "confirm": _new_pending("confirm_send", data, prompt)}


def _pick(parsed_list: list[dict[str, Any]]) -> dict[str, Any]:
    """Waehlt aus Haupt- und Alternativ-Transkript den besten Befehl."""
    cmds = [p for p in parsed_list if p.get("intent") not in ("chat", "empty")]
    if not cmds:
        return parsed_list[0]
    wa = [p for p in cmds if p["intent"] == "whatsapp_send"]
    if wa:
        primary = wa[0]
        if len(wa) > 1:
            try:
                local = pa._load_local_contacts()
                cached = pa._contacts_cache.get("items") or []
                pool = [_clean(x["name"]) for x in local + cached] + [_clean(a) for x in local for a in x["aliases"]]
            except Exception:
                pool = []
            def known(p):
                return bool(difflib.get_close_matches(_clean(p["contact"]), pool, n=1, cutoff=0.75)) if pool else False
            if not known(primary):
                alt = next((p for p in wa[1:] if known(p)), None)
                if alt:
                    primary = dict(primary, contact=alt["contact"], message=primary.get("message") or alt.get("message"))
        return primary
    return cmds[0]


def handle_command(body: dict[str, Any], engine) -> dict[str, Any]:
    t0 = time.time()
    source = str(body.get("source") or "api")[:20]
    heard = str(body.get("text") or "").strip()
    stt_info = None
    b64 = body.get("audio_b64") or ""
    if b64 and not heard:
        if isinstance(b64, str) and "," in b64:
            b64 = b64.split(",", 1)[1]
        try:
            raw = base64.b64decode(b64)
        except Exception:
            return {"success": False, "error": "bad_audio_b64", "reply": "Audio ungültig."}
        stt_info = transcribe_audio(raw, str(body.get("filename") or "audio.wav"))
        heard = (stt_info.get("text") or "").strip() if stt_info.get("success") else ""
    alts = [str(a) for a in (body.get("alt_texts") or []) if isinstance(a, str) and a.strip()][:3]
    texts = [x for x in [heard] + alts if x]
    if not texts:
        return {"success": False, "handled": True, "intent": "empty", "reply": "Ich habe nichts verstanden.",
                "stt": stt_info}
    for _x in texts:
        _blk = voice_priv_block(_x)
        if _blk:
            _blk["stt"] = stt_info
            return _blk
    parsed = _pick([parse(x) for x in texts])
    if parsed["intent"] == "chat" and body.get("llm_fallback", True):
        lp = _llm_parse(parsed["text"], engine)
        if lp:
            parsed = lp
    intent = parsed["intent"]
    out: dict[str, Any]
    if intent == "empty":
        out = {"success": True, "intent": "empty", "reply": "Ja?", "listen_again": True}
    elif intent == "open_pc_app":
        r = open_pc_app(parsed["app"], engine)
        out = {"success": r.get("success"), "intent": intent, "app": parsed["app"], "reply": r["reply"]}
        pa.log_action({"action": "open_pc_app", "app": parsed["app"], "result": "ok" if r.get("success") else "fail",
                       "source": source})
    elif intent == "open_phone_app":
        r = pa.open_phone_app(parsed["app"], source=source)
        out = {"success": r.get("success"), "intent": intent, "app": parsed["app"], "reply": r["reply"],
               "status": r.get("status"), "error": r.get("error")}
    elif intent == "whatsapp_send":
        out = _whatsapp_flow(parsed, source)
    else:
        if body.get("chat_fallback"):
            try:
                r = engine.chat(parsed["text"], body.get("session_id"))
                out = {"success": True, "intent": "chat", "reply": (r or {}).get("reply") or "Keine Antwort.",
                       "chat": True}
            except Exception as e:
                out = {"success": False, "intent": "chat", "reply": "Der Chat ist gerade nicht erreichbar.",
                       "error": type(e).__name__}
        else:
            out = {"success": True, "intent": "chat", "handled": False, "text": parsed["text"]}
    out.setdefault("handled", intent != "chat" or bool(body.get("chat_fallback")))
    out["heard"] = heard
    out["command_text"] = parsed.get("text")
    out["via"] = parsed.get("via", "rules")
    out["ms"] = int((time.time() - t0) * 1000)
    if stt_info:
        out["stt"] = {k: v for k, v in stt_info.items() if k != "text"}
    with _lock:
        _state["last_command"] = {"ts": time.time(), "intent": intent, "source": source, "ok": out.get("success")}
    return out


def handle_confirm(body: dict[str, Any], engine) -> dict[str, Any]:
    _gc()
    pid = str(body.get("id") or "")
    answer = str(body.get("answer") or "")
    with _lock:
        p = _pending.get(pid)
    if not p:
        return {"success": False, "error": "expired_or_unknown", "done": True,
                "reply": "Die Anfrage ist abgelaufen. Es wurde nichts gesendet."}
    data = p["data"]
    kind = p["kind"]
    if kind == "dictate":
        msg = _tidy_message(strip_wake(answer))
        if classify_answer(answer) == "no" or not msg or answer.strip().lower() in ("timeout", "stille"):
            with _lock:
                _pending.pop(pid, None)
            return {"success": True, "done": True, "result": "discarded", "reply": "Okay, abgebrochen. Nichts gesendet."}
        with _lock:
            _pending.pop(pid, None)
        data["message"] = msg
        prompt = _confirm_prompt(data["name"], msg)
        return {"success": True, "done": False, "reply": prompt, "confirm": _new_pending("confirm_send", data, prompt)}
    if kind == "choose_contact":
        if classify_answer(answer) in ("no", "timeout") or answer.strip().lower() in ("timeout", "stille"):
            with _lock:
                _pending.pop(pid, None)
            return {"success": True, "done": True, "result": "discarded", "reply": "Okay, abgebrochen. Nichts gesendet."}
        cands = data.get("candidates") or []
        a = _clean(strip_wake(answer))
        ordinals = {"erste": 0, "ersten": 0, "eins": 0, "zweite": 1, "zweiten": 1, "zwei": 1, "dritte": 2, "dritten": 2, "drei": 2}
        pick = None
        for w, i in ordinals.items():
            if re.search(rf"\b{w}\b", a) and i < len(cands):
                pick = cands[i]
                break
        if pick is None:
            best, score = None, 0.0
            for c in cands:
                s = difflib.SequenceMatcher(None, a, _clean(c["name"])).ratio()
                if any(tok in _clean(c["name"]).split() for tok in a.split()):
                    s = max(s, 0.8)
                if s > score:
                    best, score = c, s
            pick = best if score >= 0.6 else None
        if pick is None:
            return {"success": True, "done": False, "reply": "Das habe ich nicht verstanden. Wen meinst du?",
                    "confirm": {"id": pid, "kind": kind, "expires_in": int(p["expires"] - time.time()),
                                "prompt": "Wen meinst du?"}}
        with _lock:
            _pending.pop(pid, None)
        data.update({"name": pick["name"], "number": pick["number"]})
        data.pop("candidates", None)
        if not data.get("message"):
            prompt = f"Was soll ich {pick['name']} schreiben?"
            return {"success": True, "done": False, "reply": prompt, "confirm": _new_pending("dictate", data, prompt)}
        prompt = _confirm_prompt(pick["name"], data["message"])
        return {"success": True, "done": False, "reply": prompt, "confirm": _new_pending("confirm_send", data, prompt)}
    # confirm_send
    verdict = classify_answer(answer)
    if verdict == "unclear":
        return {"success": True, "done": False, "reply": "Bitte sag ja oder nein.",
                "confirm": {"id": pid, "kind": kind, "expires_in": int(p["expires"] - time.time()),
                            "prompt": "Bitte sag ja oder nein."}}
    with _lock:
        _pending.pop(pid, None)
    if verdict != "yes":
        pa.log_action({"action": "whatsapp_send", "to": pa.mask_recipient(data.get("name"), data.get("number")),
                       "text_len": len(data.get("message") or ""), "result": "discarded_" + verdict,
                       "source": data.get("source")})
        return {"success": True, "done": True, "result": "discarded",
                "reply": "Okay, verworfen. Nichts gesendet." if verdict == "no" else "Keine Antwort, ich habe die Nachricht verworfen."}
    r = pa.whatsapp_send(data["number"], data["message"], source=data.get("source") or "voice",
                         recipient=data.get("name") or "", confirmed=True)
    return {"success": bool(r.get("success")), "done": True, "result": "sent" if r.get("success") else "failed",
            "error": r.get("error"), "reply": r.get("reply") or ("Gesendet." if r.get("success") else "Fehler.")}


# ----------------------------------------------------------------- wakeword state
def _load_wake_cfg() -> dict[str, Any]:
    try:
        return json.loads(WAKE_CFG.read_text(encoding="utf-8"))
    except Exception:
        return {"enabled": True}


def _save_wake_cfg(cfg: dict[str, Any]) -> None:
    WAKE_CFG.parent.mkdir(parents=True, exist_ok=True)
    WAKE_CFG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def note_tts(text: str, source: Any = None) -> None:
    """Grobe Sprechdauer der Browser-TTS merken -> Weckwort-Dienst pausiert solange (kein Selbst-Trigger)."""
    if source == "wakeword":
        return
    est = 1.5 + len(text or "") * 0.075
    with _lock:
        _state["tts_until"] = max(_state["tts_until"], time.time() + min(est, 120))


def wake_state() -> dict[str, Any]:
    now = time.time()
    cfg = _load_wake_cfg()
    with _lock:
        live = _state["live_until"] > now
        tts = _state["tts_until"] > now
        svc = dict(_state.get("service") or {})
    alive = bool(svc) and now - float(svc.get("ts") or 0) < 30
    reason = None
    if not cfg.get("enabled", True):
        reason = "disabled"
    elif live:
        reason = "browser_live"
    elif tts:
        reason = "nexus_speaking"
    return {"success": True, "enabled": bool(cfg.get("enabled", True)), "paused": reason is not None,
            "reason": reason, "release_mic": reason in ("disabled", "browser_live"),
            "live_active": live, "tts_active": tts,
            "service": {**svc, "alive": alive, "age_s": round(now - float(svc.get("ts") or 0), 1) if svc else None}}


def handle_get(path: str, qs: dict, engine) -> tuple[int, dict[str, Any]]:
    if path == "/api/voice/wakeword/state":
        return 200, wake_state()
    if path == "/api/voice/commands/status":
        _gc()
        with _lock:
            pend = [{"id": k, "kind": v["kind"], "expires_in": int(v["expires"] - time.time())} for k, v in _pending.items()]
            last = _state.get("last_command")
        return 200, {"success": True, "pending": pend, "last_command": last, "phone": pa.status(),
                     "phone_apps": pa.phone_app_names(), "pc_apps": sorted(set(PC_ALIASES.values()))}
    return 404, {"success": False, "error": "not_found"}


def handle_post(path: str, body: dict[str, Any], engine) -> tuple[int, dict[str, Any]]:
    if path == "/api/voice/command":
        return 200, handle_command(body, engine)
    if path == "/api/voice/confirm":
        return 200, handle_confirm(body, engine)
    if path == "/api/voice/wakeword/config":
        cfg = _load_wake_cfg()
        if "enabled" in body:
            cfg["enabled"] = bool(body.get("enabled"))
        _save_wake_cfg(cfg)
        return 200, wake_state()
    if path == "/api/voice/wakeword/heartbeat":
        svc = {k: body.get(k) for k in ("pid", "rss_mb", "cpu_pct", "state", "engine", "mic", "last_wake", "version")}
        svc["ts"] = time.time()
        with _lock:
            _state["service"] = svc
        return 200, wake_state()
    if path == "/api/voice/live_state":
        with _lock:
            _state["live_until"] = time.time() + LIVE_TTL if body.get("active") else 0.0
        return 200, wake_state()
    return 404, {"success": False, "error": "not_found"}


# nexus_voice_priv_guard (2026-10-16, Nexus-Media): Sprache ist nie ein Freigabeweg.
# Mikro hoert Lautsprecher/Videos/TTS mit -> privilegierte Befehle per Sprache ablehnen.
# Erlaubt bleibt nur das Abschalten (/hermes normal, Not-Aus).
_PRIV_VOICE = re.compile(
    r"(al+[\s\-]*mod(us|os|e)?\b|al+e[sn]?[\s\-]*modus|freigeb|freigabe|genehmig|autorisier|"
    r"admin[\s\-]*(recht|modus|dienst)|administrator|rechte\s+(erweiter|vergeb|geben)|run[\s_]*as)",
    re.I)

def voice_priv_block(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if raw.startswith("/") or re.search(r"\bslash\b|schraegstrich", _fold(raw)):
        return {"success": False, "handled": True, "intent": "voice_priv_blocked", "error": "voice_slash_blocked",
                "reply": "Befehle mit Schraegstrich gehen nicht per Sprache."}
    t = _fold(text or "")
    if not t or not _PRIV_VOICE.search(t):
        return None
    if re.search(r"(hermes\s+normal|not[\s\-]*aus|beende|stopp?\b|aus\s*schalten|ausschalten)", t) and not re.search(r"(aktivier|einschalt|an\s*schalten|starte)", t):
        return None
    return {"success": False, "handled": True, "intent": "voice_priv_blocked", "error": "voice_priv_blocked",
            "reply": "Das geht nicht per Sprache. Freigaben und der ALL-Modus nur am PC mit Bestaetigung."}
