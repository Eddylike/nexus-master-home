"""LT-09: Agenten im Live Talk ueber die BESTEHENDE Struktur (keine zweite Agentenverwaltung).

NEXUS fuehrt das Gespraech (gestreamt ueber den vorhandenen OllamaClient des Cores).
Wird ein anderer Agent direkt angesprochen, antwortet er ueber seinen vorhandenen Zugang:
  Hermes    -> nexus.voice.call._hermes_reply (HermesBrain, wie im NEXUSPhone-Anruf)
  NEXUS GPT -> nexus.integrations.gpt_local_agent.ask
  Aether    -> nexus.integrations.aether_agent.ask (nie /auftrag per Sprache)
Sonderfaelle wie im Anruf: "Auftrag: ..." und erkannte Geraete-/Tool-Befehle (decision.decide, gleicher
Orchestrator wie der Chat, keine neuen Rechte) -> volle NEXUS-Engine (Werkzeuge, Nachweis). Wetter -> echte
Open-Meteo-Daten, explizite "am PC"-Befehle -> Positivliste nexus/voice/pc.py. Wahrheitsschutz call.guard je Satz.
Alle Agenten sprechen mit derselben Piper-Stimme (voice.py).
"""
from __future__ import annotations

import re
import threading
from typing import Iterator

AGENTS = ("NEXUS", "Hermes", "NEXUS GPT", "Aether")
_LEAD = r"^\W*(?:(?:hey|hallo|hi|ok|okay|also)\W+)?"
_RX = {
    "NEXUS GPT": re.compile(_LEAD + r"(?:(?:nexus|nexis|nexes)\W*)?(?:gpt|g\.?\s?p\.?\s?t\.?|dschi\W?pi\W?ti|chat\s?gpt)\b", re.I),
    "Hermes": re.compile(_LEAD + r"(?:herme[sz]|herm[eä]s)\b", re.I),
    "Aether": re.compile(_LEAD + r"(?:aether|äther|ether|ather|etter|ätter|eter)\b", re.I),
}
LIVE_SYSTEM = (
    "Du bist NEXUS, die herzliche, aufmerksame Koordinatorin von Eddies lokalem KI-System, gerade in einem "
    "Live-Sprachgespraech mit Eddie (wie ein Telefonat). Antworte auf Deutsch, duze ihn, natuerlich und gesprochen: "
    "meist ein bis drei kurze Saetze, der erste Satz kurz. Keine Aufzaehlungen, kein Markdown, keine Emojis, "
    "keine Abkuerzungen. Geraetebefehle (Apps oeffnen, Musik, Lautstaerke, Systemstatus, Uhrzeit), Wetter, aktuelle "
    "Suche sowie Nachrichten und Dateien vom Handy wertet NEXUS getrennt echt aus; dazu muss Eddie nichts voranstellen. "
    "Behaupte nie, du koenntest Geraete nicht steuern, und behaupte nie eine ausgefuehrte Aktion oder Live-Zahl, "
    "die nicht aus dem echten Pfad kommt. Erfinde keine Nachrichten, Preise, Sportergebnisse oder Wetterwerte. Alles, was Eddie dir in diesem Gespraech erzaehlt hat, weisst du und nutzt es ganz selbstverstaendlich, "
    "zum Beispiel wenn er fragt, was er vorhin gesagt hat. Behaupte aber nie, etwas dauerhaft gespeichert, geteilt, weitergegeben oder geprueft zu haben. "
    "Zu deinem Team (Hermes fuer Werkzeuge und Technik, NEXUS GPT als zusaetzliche KI, Aether als Doktor und "
    "Begleiter) sagst du nur etwas, wenn Eddie danach fragt; er kann sie direkt mit Namen ansprechen. "
    "Grundwissen (nicht erfinden, nichts dazudichten): Eddie Schulz, lebt in Gelsenkirchen, Content Creator und "
    "Industriefotograf; drei Hunde (Nelly, ein Rottweiler, ein Great-Dane-Boxer-Mix). NEXUS laeuft lokal auf seinem "
    "Windows-PC (WoZiPC), das Handy ist ein Xiaomi Redmi Note 13. Ton: direkt, klar, auf Augenhoehe, kein "
    "Kundenservice-Slang. Wenn du etwas nicht weisst, sag das in einem kurzen Satz. "
    "Sag nie, eine App sei geoeffnet oder eine Nachricht geschrieben oder gesendet, wenn das nicht aus dem "
    "echten Pfad kommt; sag stattdessen, wie Eddie es formulieren kann (zum Beispiel: Oeffne WhatsApp, "
    "Schreib Anna Hallo)."
)
LIVE_MODEL = "qwen3.5:9b"  # fest, unabhaengig vom UI-Fallback auf 4b
_PHONE_CTX = re.compile(
    r"\b(?:am|auf (?:dem|meinem)|im)\s+(?:handy|telefon|redmi|smartphone)\b|\b(?:handy|redmi)\b",
    re.I,
)
# Alle vorhandenen Chat-Intents, die der Orchestrator schon ausfuehrt (keine neuen Rechte).
# crew nur bei explizitem @, nie der Auto-Router ("heute" -> Scout).
ACTION_INTENTS = frozenset({
    "os_open_url", "os_open_path", "os_launch_app", "os_play_media", "os_volume",
    "os_mouse", "os_list_dir", "os_close_opened", "os_close_url",
    "youtube_play", "youtube_search", "media",
    "shell", "run_script",
    "status", "diagnose", "monitor", "time",
    "notifications_read", "torch", "download",
    "research", "file", "project_open", "project_list_dir",
    "memory_store", "memory_recall", "correction_store",
    "capabilities", "build", "code", "task", "self_improve",
})
VOICE_NOTE = (" Antworte jetzt muendlich im Live-Sprachgespraech: hoechstens drei kurze, gut sprechbare Saetze, "
              "keine Aufzaehlungen, kein Markdown.")
WORK_ORDER = re.compile(r"^\W*(?:(?:hey|hallo|ok|okay)\W+)?(?:nexus\W+)?auftrag\b", re.I)


def _voice_norm(text: str) -> str:
    """Haeufige STT-/ASCII-Formen (spaet, oeffne, ...) in die Umlaut-Form, die der Chat-Router kennt."""
    t = str(text or "")
    reps = (
        (r"\bspaet\b", "sp\u00e4t"),
        (r"\boeffne", "\u00f6ffne"),
        (r"\bueffne", "\u00f6ffne"),
        (r"\blautstaerke\b", "lautst\u00e4rke"),
        (r"\bnaechste", "n\u00e4chste"),
        (r"\bschliess", "schlie\u00df"),
        (r"\bzurueck\b", "zur\u00fcck"),
    )
    for pat, rep in reps:
        t = re.sub(pat, rep, t, flags=re.I)
    t = re.sub(r"\bspielmusik\b", "spiel Musik", t, flags=re.I)
    t = t.strip().rstrip(".!?,;:")
    return t


def _spoken_result(result: dict, full: bool = False) -> str:
    """Kurze, sprechbare Bestaetigung aus der echten Orchestrator-Antwort. Kein Fake-Erfolg."""
    try:
        from nexus.livetalk.engines import speakable
        text = speakable(str((result or {}).get("reply") or "")).strip()
    except Exception:
        text = re.sub(r"\s+", " ", str((result or {}).get("reply") or "")).strip()
    if not text:
        return "Der Befehl kam ohne Antwort zurueck."
    if full:  # z. B. Nachrichten vorlesen: komplette Liste, nur hart begrenzt
        return text if len(text) <= 1200 else text[:1200].rsplit(" ", 1)[0] + " ..."
    parts, out = re.split(r"(?<=[.!?])\s+", text), []
    for part in parts:
        if not part:
            continue
        if out and len(" ".join(out)) + len(part) + 1 > 400:
            break
        out.append(part)
        if len(out) >= 3:
            break
    return " ".join(out)


def _is_action(text: str) -> bool:
    """True, wenn der Chat-Router denselben Text als Geraete-/Tool-Befehl behandelt."""
    try:
        from nexus.master.decision import decide
        d = decide(_voice_norm(text))
    except Exception:
        return False
    intent = str((d or {}).get("intent") or "")
    if intent in ACTION_INTENTS:
        return True
    if intent == "crew" and str(text or "").lstrip().startswith("@"):
        return True
    return intent == "ui" and (d or {}).get("action") == "close_pages"


def _phone_command(text: str, from_phone: bool = True, link=None) -> dict | None:
    """Bestehende Handy-Intents plus uebliche Assistenten-Befehle; ohne 'am PC' am Handy."""
    t = _voice_norm(text)
    msg = _phone_msg_command(t, link)
    if msg:
        return msg
    if from_phone and not _wants_pc(t):
        q = _open_question_app(t)
        if q:  # "Kannst du WhatsApp oeffnen?" -> echt oeffnen statt nur behaupten
            try:
                from nexus.phone_actions import find_phone_app
                found = find_phone_app(q)
            except Exception:
                found = None
            return {"intent": "open_phone_app", "app": found[0] if found else q, "text": t}
        try:
            from nexus.phone_voice import parse as _pv_parse
            cmd = _pv_parse(t)
            if cmd:
                return cmd
        except Exception:
            pass
        app = _phone_app_request(t)
        if app:
            return {"intent": "open_phone_app", "app": app, "text": t}
    # Offene WhatsApp-Bestaetigung ("ja"/"nein") aus dem bestehenden Confirm-Flow
    try:
        from nexus.voice import commands as vc
        if getattr(vc, "_pending", None):
            ans = vc.classify_answer(t)
            if ans in ("yes", "no"):
                return {"intent": "phone_confirm", "answer": ans, "text": t}
        p = vc.parse(t)
        if p.get("intent") in ("open_phone_app", "whatsapp_send"):
            return p
    except Exception:
        pass
    if not _PHONE_CTX.search(t):
        return None
    low = t.lower()
    if re.search(r"\b(screenshot|bildschirmfoto|foto vom bildschirm)\b", low):
        return {"intent": "phone_screenshot", "text": t}
    if re.search(r"\b(akku|batterie)\b", low):
        return {"intent": "phone_battery", "text": t}
    if re.search(r"\b(lauter|leiser|stumm|ton aus)\b", low):
        act = "down" if "leiser" in low else "mute" if ("stumm" in low or "ton aus" in low) else "up"
        return {"intent": "phone_volume", "action": act, "text": t}
    return None


def _run_phone(cmd: dict, meta: dict | None = None) -> dict:
    """Fuehrt eine erkannte Handy-Aktion ueber phone_actions bzw. die App-Bruecke aus (kein Root)."""
    from nexus import phone_actions as pa
    intent = cmd.get("intent")
    link = (meta or {}).get("phone")
    if intent == "phone_read_notifications":
        items = []
        if link is not None:
            try:
                items = link.notifications_live()
            except Exception:
                items = link.fresh_notifications()
        from nexus.livetalk.phone_link import speak_notifications
        _remember_last_sender(link, items)
        return {"success": True, "reply": speak_notifications(items), "count": len(items or [])}
    if intent in ("phone_write", "phone_write_followup"):
        r = _run_write(cmd, link)
        if not r.get("success") and r.get("fallback_ok") and cmd.get("explicit_wa"):
            # keine passende Benachrichtigung: bestehender WhatsApp-Weg (nexus.voice.commands), unveraendert
            try:
                from nexus.voice import commands as vc
                p = vc.parse(str(cmd.get("text") or ""))
                if p.get("intent") == "whatsapp_send":
                    return vc._whatsapp_flow(p, "livetalk")
            except Exception:
                pass
        return r
    if intent == "phone_prepare_reply":
        return _run_write({"intent": "phone_write", "who": cmd.get("who") or "", "body": cmd.get("body") or "",
                           "split": True}, link)
    if intent == "phone_prepare_reply_legacy":
        if link is None:
            return {"success": False, "reply": "Die Handy-Bruecke ist gerade nicht verbunden."}
        who, body = str(cmd.get("who") or "").strip(), str(cmd.get("body") or "").strip()
        notif = link.find_notification(who)
        if not notif:
            try:
                link.notifications_live()
            except Exception:
                pass
            notif = link.find_notification(who)
        if not notif or not notif.get("id"):
            return {"success": False, "reply": f"Ich habe keine Nachricht von {who or 'dem Kontakt'} gefunden."}
        if not notif.get("can_reply") and not any((a or {}).get("remote") for a in (notif.get("actions") or []) if isinstance(a, dict)):
            return {"success": False, "reply": f"Auf die Nachricht von {who} kann ich nicht direkt antworten."}
        res = link.request("prepare_reply", id=notif.get("id"), text=body) or {}
        spoken = (res.get("spoken") or res.get("result") or {}).get("spoken") if isinstance(res.get("result"), dict) else res.get("spoken")
        if not spoken:
            spoken = f"Antwort an {who}: {body}. Sag senden, wenn ich sie abschicken soll."
        link.pending_reply = {"id": notif.get("id"), "who": who, "body": body}
        return {"success": True, "reply": spoken, "pending": True}
    if intent == "phone_confirm_send":
        if link is not None and getattr(link, "pending_reply", None):
            who = str((link.pending_reply or {}).get("who") or "").strip()
            res = link.request("confirm_send")
            if res is None:
                link.pending_reply = None
                return {"success": False, "error": "no_result",
                        "reply": "Vom Handy kam keine Rueckmeldung. Ich weiss nicht, ob die Antwort raus ist."}
            inner = _result_obj(res)
            if res.get("ok") is False or inner.get("ok") is False:
                err = str(inner.get("error") or res.get("error") or "")
                if err == "too_fast_confirm":
                    return {"success": False, "error": err, "reply": "Sag bitte nochmal senden."}
                link.pending_reply = None
                if err == "notification_gone":
                    return {"success": False, "error": err, "reply": f"Die Nachricht von {who or 'dem Kontakt'} "
                            "ist nicht mehr in der Statusleiste, deshalb konnte ich nicht antworten."}
                return {"success": False, "error": err or "send_failed", "reply": "Das Senden hat nicht geklappt."}
            link.pending_reply = None
            return {"success": True, "reply": f"Die Antwort an {who} ist unterwegs." if who else "Die Antwort ist unterwegs."}
        try:  # offener Entwurf aus dem bestehenden WhatsApp-Weg (nexus.voice.commands)
            from nexus.voice import commands as vc
            pid = next(iter(vc._pending), None) if getattr(vc, "_pending", None) else None
            if pid:
                return vc.handle_confirm({"id": pid, "answer": "ja"}, None)
        except Exception:
            pass
        return {"success": False, "reply": "Es liegt keine Antwort zum Senden vor."}
    if intent == "phone_cancel_send":
        try:
            from nexus.voice import commands as vc
            pid = next(iter(vc._pending), None) if getattr(vc, "_pending", None) else None
            if pid:
                vc.handle_confirm({"id": pid, "answer": "nein"}, None)
        except Exception:
            pass
        if link is not None:
            link.pending_write = None
            try:
                if getattr(link, "pending_reply", None):
                    link.request("cancel_reply", timeout=2.0)
            except Exception:
                pass
            link.pending_reply = None
        return {"success": True, "reply": "Alles klar, ich habe den Entwurf verworfen."}
    if intent == "phone_send_file":
        from nexus.livetalk.phone_link import find_and_stage_file
        r = find_and_stage_file(str(cmd.get("name") or ""))
        if r.get("success") and link is not None:
            try:
                link.request("send_file_to_phone", timeout=3.0, name=r.get("name"))
            except Exception:
                pass
        return r if r.get("reply") else {"success": bool(r.get("success")), "reply": r.get("reply") or "Die Datei ist in der Outbox."}
    if intent == "phone_describe_photo":
        rec = getattr(link, "last_image", None) if link is not None else None
        path = (rec or {}).get("path") or (rec or {}).get("rel")
        from nexus.livetalk.phone_link import describe_image
        d = describe_image(path)
        return {"success": bool(d.get("success")), "reply": d.get("spoken"), "model": d.get("model")}
    if intent == "open_phone_app":
        name = str(cmd.get("app") or "")
        r = pa.open_phone_app(name, source="livetalk")
        if not r.get("success") and name == "youtube music":
            r2 = pa.open_phone_app("spotify", source="livetalk")
            if r2.get("success"):
                return r2
        return r
    if intent == "whatsapp_send":
        from nexus.voice import commands as vc
        return vc._whatsapp_flow(cmd, "livetalk")
    if intent == "phone_confirm":
        from nexus.voice import commands as vc
        # bestehende Pending-ID (nur eine)
        pid = next(iter(vc._pending), None) if getattr(vc, "_pending", None) else None
        if not pid:
            return {"success": False, "reply": "Es liegt keine Nachricht zur Bestaetigung vor."}
        return vc.handle_confirm({"id": pid, "answer": "ja" if cmd.get("answer") == "yes" else "nein"}, None)
    if intent == "phone_volume":
        return pa.phone_volume(str(cmd.get("action") or "up"), source="livetalk")
    if intent == "phone_battery":
        return pa.phone_battery(source="livetalk")
    if intent == "phone_screenshot":
        return pa.phone_screenshot(source="livetalk")
    try:
        from nexus.phone_voice import run as _pv_run
        return _pv_run(cmd, source="livetalk")
    except Exception as exc:
        return {"success": False, "reply": "Diese Handy-Aktion kenne ich so nicht.", "error": type(exc).__name__}




def _now_berlin() -> str:
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Europe/Berlin"))
    except Exception:
        now = datetime.now()
    wd = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")[now.weekday()]
    return f"Jetzt ist {wd}, {now.strftime('%d.%m.%Y')}, {now.strftime('%H:%M')} Uhr (Europe/Berlin)."


def _wants_pc(text: str) -> bool:
    t = _voice_norm(text)
    return bool(re.search(r"\b(?:am|auf (?:dem|meinem))\s+(?:pc|rechner|computer|laptop)\b", t, re.I))


def _collapse_repeat(text: str) -> str:
    t = re.sub(r"[,;]+", " ", str(text or ""))
    t = re.sub(r"\s+", " ", t).strip().rstrip(".!?")
    parts = t.split()
    if len(parts) >= 2 and parts[0].lower().rstrip(".!,") == parts[1].lower().rstrip(".!,"):
        t = parts[0]
    return t.strip().rstrip(".!?")


def _phone_app_request(text: str) -> str | None:
    """Kurzer App-Befehl: 'YouTube', 'oeffne YouTube', 'spiel Musik ab' -> App-Schluessel oder None."""
    raw = _collapse_repeat(_voice_norm(text))
    if not raw:
        return None
    # Nachrichten vorlesen ist kein SMS-App-Start
    low = raw.lower()
    if re.search(r"\b(lies|lese|vorlesen|antworte?|nachrichten|benachrichtigungen)\b", low) and "oeffne" not in low and "öffne" not in low and "starte" not in low:
        return None
    cleaned = re.sub(
        r"^\W*(?:oeffne|öffne|starte|start|spiel(?:e)?|mach\s+auf|zeig(?:e)?)\s+(?:mal\s+)?",
        "", raw, flags=re.I,
    )
    cleaned = re.sub(r"\s+ab\s*$", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+(?:am|auf (?:dem|meinem))\s+(?:handy|telefon|redmi|smartphone|pc|rechner|computer).*$", "", cleaned, flags=re.I)
    cleaned = cleaned.strip().rstrip(".!?")
    if re.fullmatch(r"(?:die\s+)?musik", cleaned, flags=re.I) or re.fullmatch(r"(?:spiel(?:e)?\s+)?musik(?:\s+ab)?", raw, flags=re.I):
        return "youtube music"
    if not cleaned:
        return None
    # Nur kurze, klare App-Namen (kein Plaudern)
    if len(cleaned.split()) > 4:
        return None
    try:
        from nexus.phone_actions import find_phone_app
        found = find_phone_app(cleaned)
        if found:
            return found[0]
    except Exception:
        return None
    return None


_WEB_RX = re.compile(
    r"\b(?:nachrichten|news|schlagzeilen|meldungen|"
    r"preise?|kurs(?:e)?|aktie[n]?|boerse|bitcoin|krypto|"
    r"bundesliga|spielstand|sportuntersuch|champions?\s*league|"
    r"aktuell(?:es|er|e|en)?|"
    r"was\s+(?:ist|gibt(?:'?s)?|laeuft|lauft)\s+heute(?:\s+los)?|"
    r"was\s+ist\s+heute\s+los|"
    r"was\s+gibt\s+es\s+(?:heute\s+)?neues|"
    r"wer\s+hat\s+gewonnen|"
    r"wahl(?:ergebnis)?)\b",
    re.I,
)


def _needs_web(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    t = _voice_norm(raw)
    if re.search(r"\bwas\s+machen\s+(wir|wir uns|uns)\s+heute\b", t, re.I):
        return False
    if _is_read_notifications(t):
        return False
    if _WEB_RX.search(t):
        return True
    try:
        from nexus.master.decision import decide
        d = decide(raw) or {}
        if d.get("intent") == "research":
            return True
    except Exception:
        pass
    return False


def _is_read_notifications(text: str) -> bool:
    """Erkennt Vorlese-/Schau-Befehle fuer Handy- und WhatsApp-Nachrichten, inkl. STT-Varianten.

    Unscharf genug fuer typische STT-Verhoerer (liegt/lieg/liest statt lies, '... vor'),
    aber nur wenn Nachricht/Benachrichtigung/WhatsApp im Satz vorkommt. Nachrichten-Lage
    ohne Vorlese-Hinweis bleibt Websuche.
    """
    low = _voice_norm(text).strip().lower()
    if not low:
        return False
    has_topic = bool(re.search(
        r"\b(b?e?nachrichtigung(?:en)?|belachrichtigung(?:en)?|nachricht(?:en)?|"
        r"notifications?|whats?app)\b",
        low,
    ))
    if not has_topic:
        return False
    has_read = bool(re.search(
        r"\b(lies[t]?|lieg(?:t)?|lese|vorlesen|vor\s*lesen|"
        r"zeig(?:e|en)?|guck(?:e|en)?|schau(?:e|en)?|check(?:en)?|"
        r"pruefe|pruefen|prüfe|ansehen)\b",
        low,
    ))
    has_vor = bool(re.search(r"\bvor\s*[.!?]*\s*$", low))
    mine = bool(re.search(r"\b(meine[rns]?|mir|handy)\b", low))
    if not (has_read or has_vor or mine):
        return False
    news = bool(re.search(
        r"\b(welt|deutschland|politik|sport|bundesliga|schlagzeilen)\b", low
    ))
    if news and not (has_read or has_vor or mine or re.search(r"\bwhats?app\b", low)
                     or re.search(r"\b(benachrichtigung|belachrichtigung|handy)\b", low)):
        return False
    return True


def _phone_msg_command(text: str, link=None) -> dict | None:
    t = _voice_norm(text).strip()
    low = t.lower()
    if re.fullmatch(r"\W*(senden|absenden|send(?:en)?|abschicken|los(?:schicken)?)\W*", low):
        return {"intent": "phone_confirm_send", "text": t}
    if re.fullmatch(r"\W*(abbrechen|nicht senden|abbrechen\s+bitte|cancel)\W*", low):
        return {"intent": "phone_cancel_send", "text": t}
    if _is_read_notifications(t):
        return {"intent": "phone_read_notifications", "text": t}
    m = re.search(
        r"\b(?:schick|sende|schicke)\s+(?:mir\s+)?(?:die\s+|den\s+|das\s+)?(?:datei\s+)?(.+?)\s+"
        r"(?:aufs|auf\s+das|auf\s+dem|ans)\s+handy\b",
        t, re.I,
    )
    if m:
        return {"intent": "phone_send_file", "name": m.group(1).strip(), "text": t}
    if re.search(
        r"\b(?:beschreib(?:e)?(?:\s+das)?\s+(?:foto|bild)|was\s+ist\s+auf\s+dem\s+(?:foto|bild)|"
        r"was\s+siehst\s+du\s+auf\s+dem\s+(?:foto|bild)|analysiere\s+das\s+(?:foto|bild))\b",
        low,
    ):
        return {"intent": "phone_describe_photo", "text": t}
    w = _parse_write(t, link)
    if w:
        return w
    return None


# --- Schreiben per Sprache: Antwort ueber die Benachrichtigung (RemoteInput der App) -------------
# Reihenfolge: Statusleisten-Benachrichtigung mit Antworten-Aktion gewinnt. Nur wenn keine passt und
# der Satz ausdruecklich WhatsApp nennt, bleibt der bestehende WhatsApp-Weg aus nexus.voice.commands
# (whatsapp_send / _whatsapp_flow) zustaendig, unveraendert. Hier werden keine Kontakte/Nummern gelesen.
WRITE_TTL_S = 90
_WRITE_VERB = (r"(?:schreib(?:e|t|en|st)?|antworte(?:t|n)?|antwort(?:en)?|schick(?:e|t|en)?|"
               r"sende(?:t)?|send)")
_SAG_VERB = r"(?:sag(?:e|t)?)"
_WA_WORD = r"(?:whats\s?app|watsapp|whatsup|what'?s\s?app)"
_NOT_NAME = {
    "mir", "mich", "uns", "das", "es", "was", "dir", "dich", "auf", "bitte", "mal", "doch", "noch",
    "ein", "eine", "einen", "einem", "die", "der", "den", "dem", "nachricht", "nachrichten", "nichts",
    "alles", "zurueck", "zur\u00fcck", "jetzt", "gerade", "dann", "an", "weiter", "ab", "dazu", "darauf",
    "datei", "foto", "bild", "bescheid", "senden", "ja", "nein", "hallo", "wie", "wo", "wann", "warum",
    "wer", "welche", "welcher", "welches", "kurz", "gedicht", "text", "email", "e-mail", "mail", "notiz",
    "liste", "code", "brief", "auch", "so", "ihr", "ihm", "ihnen", "ihn", "sie", "er",
}
_PRON_LAST = r"(?:ihr|ihm|ihnen|ihn)"


def _write_clean_body(s: str) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip(" ,;:")
    s = re.sub(r"^(?:dass|das)\s+", "", s, flags=re.I)
    return s.strip().rstrip(".").strip()


def _parse_write(text: str, link=None) -> dict | None:
    """'Schreib(t)/Antworte/Schick <Name> <Text>', 'Antworte ihr ...', 'Schreib eine Nachricht' -> phone_write.

    'Sag <Name> ...' nur mit Trenner/'dass' oder wenn <Name> zu einem Absender in der Statusleiste passt.
    """
    t = _voice_norm(text)
    t = re.sub(r"^\W*(?:(?:hey|hallo|ok|okay|also|und)\W+)?(?:nexus\W+)?", "", t, flags=re.I)
    m = re.match(rf"^(?:bitte\s+)?(?P<verb>{_WRITE_VERB}|{_SAG_VERB})(?![\w\u00e4\u00f6\u00fc\u00df])\s*(?P<rest>.*)$",
                 t, re.I | re.S)
    if not m:
        return None
    verb, rest = m.group("verb").lower(), m.group("rest").strip()
    is_sag = verb.startswith("sag")
    for _ in range(3):
        rest = re.sub(r"^(?:mal|bitte|doch|noch|kurz)\s+", "", rest, flags=re.I)
    if re.search(r"\b(?:aufs|ans)\s+handy\b|\bauf\s+(?:das|dem)\s+handy\b", rest, re.I):
        return None  # Datei aufs Handy schicken
    generic = False
    mm = re.match(rf"^(?:(?:eine?|ne)\s+)?(?:nachricht|{_WA_WORD})(?:\s+|$)", rest, re.I)
    if mm:
        rest, generic = rest[mm.end():], True
    rest = re.sub(r"^(?:an|zu|f\u00fcr|fuer)\s+", "", rest, flags=re.I)
    rest = re.sub(rf"\s*\b(?:auf|per|\u00fcber|ueber|via|bei|in|mit)\s+{_WA_WORD}\b", "", rest, flags=re.I)
    rest = rest.strip(" ,.;!?")
    explicit_wa = bool(re.search(_WA_WORD, t, re.I))
    base = {"intent": "phone_write", "text": t, "explicit_wa": explicit_wa}
    if not rest:
        if is_sag or not generic and verb.startswith(("send", "schick")):
            return None
        return dict(base, who="", body="", split=True)
    pm = re.match(rf"^{_PRON_LAST}\b[\s,:]*(?:zur\u00fcck\s+|zurueck\s+)?(.*)$", rest, re.I)
    if pm:
        return dict(base, who="", who_ref="last", body=_write_clean_body(pm.group(1)), split=True)
    first = rest.split()[0].strip(",.:;!?").lower()
    if first in _NOT_NAME:
        return None
    sm = re.match(r"^(?P<who>[^,:;]+?)\s*(?:[,:;]\s*|\s+(?:dass|das)\s+)(?P<body>.+)$", rest)
    if sm and len(sm.group("who").split()) <= 3:
        return dict(base, who=sm.group("who").strip(), body=_write_clean_body(sm.group("body")), split=True)
    if is_sag:
        # nur wenn der Name zu einem aktuellen Absender passt (kein Kapern von "sag Hallo")
        if link is None or not _match_senders(_write_candidates(link, live=False), rest)[0]:
            return None
    return dict(base, who=rest, body="", split=False)


def _fold_name(s: str) -> str:
    t = str(s or "").lower()
    for a, b in (("\u00e4", "ae"), ("\u00f6", "oe"), ("\u00fc", "ue"), ("\u00df", "ss")):
        t = t.replace(a, b)
    t = re.sub(r"[^a-z ]", "", t)
    for a, b in (("th", "t"), ("ph", "f"), ("ck", "k"), ("c", "k"), ("y", "i"), ("ie", "i"), ("ee", "e"),
                 ("dt", "t"), ("tz", "z"), ("w", "v")):
        t = t.replace(a, b)
    t = re.sub(r"(.)\1+", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


def _name_score(said: str, sender: str) -> float:
    from difflib import SequenceMatcher
    a, b = _fold_name(said), _fold_name(sender)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    best = SequenceMatcher(None, a, b).ratio()
    btoks = b.split()
    for tok in btoks:
        best = max(best, SequenceMatcher(None, a, tok).ratio())
    if len(a.split()) == 1 and btoks and btoks[0].startswith(a) and len(a) >= 3:
        best = max(best, 0.9)
    return best


def _sender_name(n: dict) -> str:
    who = str(n.get("sender") or n.get("title") or "").strip()
    return re.sub(r"\s*\(\d+\s+nachrichten?\)\s*$", "", who, flags=re.I).strip()


def _write_candidates(link, live: bool = True) -> list[dict]:
    """Nachrichten-Benachrichtigungen (neueste zuerst), nicht eigene. Keine Kontaktliste."""
    items: list[dict] = []
    if link is None:
        return items
    try:
        items = link.notifications_live() if live else link.fresh_notifications()
    except Exception:
        try:
            items = link.fresh_notifications()
        except Exception:
            items = []
    try:
        from nexus.livetalk.notif_util import is_message
    except Exception:
        is_message = lambda n: True  # noqa: E731
    out = []
    for n in items or []:
        if not isinstance(n, dict) or n.get("from_self") or n.get("own"):
            continue
        if not is_message(n) and not n.get("can_reply"):
            continue
        if _sender_name(n):
            out.append(n)
    return out


_MATCH_MIN = 0.74


def _match_senders(cands: list[dict], who: str) -> tuple[list[tuple[str, dict, float]], str]:
    """-> (Treffer je Absender sortiert, Rest-Text). who darf den Text noch enthalten (split=False)."""
    words = str(who or "").split()
    if not words or not cands:
        return [], ""
    best: dict[str, tuple[str, dict, float, int]] = {}
    for k in range(min(3, len(words)), 0, -1):
        said = " ".join(words[:k])
        for n in cands:
            name = _sender_name(n)
            sc = _name_score(said, name)
            # auch "Sender in Gruppe": nur den Absender vergleichen
            if " in " in name:
                sc = max(sc, _name_score(said, name.split(" in ", 1)[0]))
            if sc < _MATCH_MIN:
                continue
            key = _fold_name(name)
            cur = best.get(key)
            prefer = (n.get("can_reply") is True) and not (cur and cur[1].get("can_reply") is True)
            if cur is None or sc > cur[2] + 1e-9 or (abs(sc - cur[2]) < 1e-9 and (k > cur[3] or prefer)):
                best[key] = (name, n, sc, k)
    if not best:
        return [], ""
    ranked = sorted(best.values(), key=lambda x: (-x[2], -x[3]))
    rest = " ".join(words[ranked[0][3]:])
    return [(r[0], r[1], r[2]) for r in ranked], rest


def _pending_write(link) -> dict | None:
    pw = getattr(link, "pending_write", None) if link is not None else None
    if not pw:
        return None
    import time as _t
    if _t.time() - float(pw.get("ts") or 0) > WRITE_TTL_S:
        try:
            link.pending_write = None
        except Exception:
            pass
        return None
    return pw


def _set_pending_write(link, **kw) -> None:
    import time as _t
    if link is not None:
        try:
            link.pending_write = dict(kw, ts=_t.time())
        except Exception:
            pass


def _result_obj(res) -> dict:
    """phone_result der Seite: {'ok': true, 'result': <Bridge-JSON als String/Objekt>}."""
    import json as _json
    if not isinstance(res, dict):
        return {}
    inner = res.get("result")
    if isinstance(inner, str):
        try:
            inner = _json.loads(inner)
        except Exception:
            inner = None
    return inner if isinstance(inner, dict) else {}


def _prepare_reply(link, notif: dict, who: str, body: str) -> dict:
    if notif.get("can_reply") is False and not any(
            (a or {}).get("remote") for a in (notif.get("actions") or []) if isinstance(a, dict)):
        return {"success": False, "reply": f"Auf die Nachricht von {who} kann ich nicht direkt antworten. "
                                           f"Die Benachrichtigung hat keine Antworten-Funktion."}
    res = link.request("prepare_reply", id=notif.get("id") or notif.get("key"), text=body)
    if res is None:
        return {"success": False, "reply": "Die Live-Talk-App am Handy hat nicht geantwortet. Ich habe nichts vorbereitet."}
    inner = _result_obj(res)
    if res.get("ok") is False or inner.get("ok") is False:
        err = inner.get("error") or res.get("error") or ""
        if err == "not_found":
            msg = (f"Die Nachricht von {who} liegt der Live-Talk-App gerade nicht vor, deshalb kann ich ihr nicht "
                   f"direkt antworten.")
        elif err == "no_remote_input":
            msg = f"Auf die Nachricht von {who} kann ich nicht direkt antworten."
        else:
            msg = "Die Antwort konnte ich am Handy nicht vorbereiten."
        return {"success": False, "reply": msg, "error": err or "prepare_failed"}
    import time as _t
    link.pending_reply = {"id": notif.get("id") or notif.get("key"), "who": who, "body": body, "ts": _t.time()}
    try:
        link.pending_write = None
    except Exception:
        pass
    return {"success": True, "reply": f"Antwort an {who}: {body}. Sag senden, wenn ich sie abschicken soll.",
            "pending": True}


def _no_reply_target(who: str) -> dict:
    return {"success": False, "error": "no_notification",
            "reply": (f"Ich kann {who} gerade nur antworten, wenn eine Nachricht von {who} in der Statusleiste liegt. "
                      f"Gerade finde ich keine.")}


def _run_write(cmd: dict, link) -> dict:
    """Empfaenger aus der Statusleiste, Entwurf vorlesen, senden erst nach 'senden'."""
    intent = cmd.get("intent")
    pw = _pending_write(link) or {}
    if intent == "phone_write_followup":
        said = _write_clean_body(cmd.get("text") or "")
        if not said:
            return {"success": False, "reply": "Ich habe nichts verstanden. Sag es bitte nochmal oder sag abbrechen."}
        if pw.get("stage") == "body":
            cmd = {"intent": "phone_write", "who": pw.get("who") or "", "body": said, "split": True,
                   "notif_id": pw.get("notif_id")}
        else:
            said = re.sub(r"^(?:an|zu|f\u00fcr|fuer)\s+", "", said, flags=re.I)
            sm = re.match(r"^(?P<who>[^,:;]+?)\s*(?:[,:;]\s*|\s+(?:dass|das)\s+)(?P<body>.+)$", said)
            if sm and len(sm.group("who").split()) <= 3:
                cmd = {"intent": "phone_write", "who": sm.group("who"), "body": _write_clean_body(sm.group("body")),
                       "split": True}
            else:
                cmd = {"intent": "phone_write", "who": said, "body": pw.get("body") or "", "split": False,
                       "keep_body": True}
    if link is None:
        return {"success": False, "reply": "Die Handy-Bruecke ist gerade nicht verbunden."}
    who, body = str(cmd.get("who") or "").strip(), str(cmd.get("body") or "").strip()
    cands = _write_candidates(link, live=True)
    if cmd.get("who_ref") == "last" and not who:
        who = str(getattr(link, "last_sender", "") or "")
        if not who:
            return {"success": False, "reply": "Ich weiss nicht, wem ich antworten soll. Sag bitte den Namen dazu."}
        cmd = dict(cmd, split=True)
    if not who:
        _set_pending_write(link, stage="who", body=body)
        return {"success": True, "reply": "An wen soll die Nachricht gehen?", "pending_write": True}
    if cmd.get("notif_id"):
        hit = next((n for n in cands if str(n.get("id") or n.get("key")) == str(cmd["notif_id"])), None)
        matches = [(_sender_name(hit), hit, 1.0)] if hit else []
        rest = ""
    else:
        matches, rest = _match_senders(cands, who)
    if not cmd.get("split") and matches:
        extra = _write_clean_body(rest)
        if cmd.get("keep_body") and body:
            body = (body + " " + extra).strip() if extra else body
        else:
            body = extra or body
    if not matches:
        spoken_who = who if cmd.get("split") else " ".join(who.split()[:1])
        return {"success": False, "fallback_ok": True, **_no_reply_target(spoken_who)}
    top = matches[0]
    close = [m for m in matches if m[2] >= top[2] - 0.05]
    if len(close) > 1:
        names = [m[0] for m in close[:3]]
        _set_pending_write(link, stage="who", body=body)
        return {"success": True, "pending_write": True,
                "reply": "Meinst du " + ", ".join(names[:-1]) + " oder " + names[-1] + "?"}
    name, notif = top[0], top[1]
    if not body:
        _set_pending_write(link, stage="body", who=name, notif_id=notif.get("id") or notif.get("key"))
        return {"success": True, "pending_write": True, "reply": f"Was soll ich {name} schreiben?"}
    return _prepare_reply(link, notif, name, body)


def _remember_last_sender(link, items) -> None:
    if link is None:
        return
    try:
        from nexus.livetalk.notif_util import is_message, rank_notifications
        for n in rank_notifications(items or []):
            if is_message(n) and not n.get("from_self") and _sender_name(n):
                link.last_sender = _sender_name(n)
                return
    except Exception:
        pass


_OPEN_Q = re.compile(
    r"^\W*(?:(?:hey|hallo|ok|okay|also)\W+)?(?:nexus\W+)?"
    r"(?:kannst|k\u00f6nntest|koenntest|w\u00fcrdest|wuerdest|magst)\s+du\s+(?:mir\s+)?(?:(?:mal|bitte|kurz)\s+){0,3}"
    r"(?P<obj>.+?)\s+(?:\u00f6ffnen|oeffnen|offnen|aufmachen|starten|aufrufen)\W*$",
    re.I,
)


def _open_question_app(text: str) -> str | None:
    """'Kannst du WhatsApp oeffnen?' -> 'WhatsApp' (Frageform wird als echter Befehl behandelt)."""
    m = _OPEN_Q.match(_voice_norm(text) + "?")
    if not m:
        return None
    obj = re.sub(r"^(?:die|den|das|meine[nm]?|mein)\s+", "", m.group("obj").strip(), flags=re.I)
    obj = re.sub(r"\s+(?:app)$", "", obj, flags=re.I).strip()
    if not obj or len(obj.split()) > 4:
        return None
    return obj


_FAKE_DONE = re.compile(
    r"\b(?:ist|sind|wurde|wurden|habe|hab)\s+(?:\w+\s+){0,4}?(?:jetzt\s+|nun\s+|gerade\s+|schon\s+)?"
    r"(?:ge(?:\u00f6|oe|o)ffnet|gestartet|gesendet|verschickt|abgeschickt|aufgerufen)\b",
    re.I,
)


def _source_host(url: str) -> str:
    from urllib.parse import urlparse
    host = (urlparse(url or "").hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _web_answer(query: str) -> tuple[str, dict]:
    """Kostenlose Recherche (Research + optionaler Seitenabruf). Nichts erfinden."""
    info: dict = {"ok": False, "source": None}
    try:
        from nexus.knowledge.research import Research
        news = bool(re.search(r"\b(heute|aktuell|nachrichten|news|passiert)\b", query or "", re.I))
        r = Research().search(query, limit=4, news=news)
    except Exception:
        return "Ich komme gerade nicht an die Suche. Die Anfrage ist fehlgeschlagen.", info
    if not r.get("success") or not r.get("results"):
        return "Ich habe dazu gerade keine aktuellen Treffer gefunden.", info
    first = r["results"][0]
    title = (first.get("title") or "").strip()
    snippet = (first.get("snippet") or "").strip()
    url = first.get("url") or ""
    host = _source_host(url)
    if len(snippet) < 80 and url:
        try:
            from nexus.omega.tools.web import fetch_page
            page = fetch_page(url)
            if isinstance(page, dict):
                extra = (page.get("text") or page.get("content") or page.get("snippet") or "").strip()
            else:
                extra = str(page or "").strip()
            if extra:
                snippet = re.sub(r"\s+", " ", extra)[:280]
        except Exception:
            pass
    body = snippet or title
    if title and snippet and title.lower() not in snippet.lower():
        body = f"{title}. {snippet}"
    source = host or r.get("source") or "Web"
    spoken = f"Laut {source}: {body}"
    if host and host not in spoken.lower():
        spoken = spoken.rstrip(".") + f". Quelle {host}."
    spoken = _spoken_result({"reply": spoken, "success": True})
    info.update(ok=True, source=source, url=url, engine=r.get("source"))
    return spoken, info


def route(text: str, from_phone: bool = True, phone=None) -> tuple[str, str]:
    """-> (agent, mode). mode: talk | order | action | phone | weather | pc | web."""
    t = str(text or "")
    if WORK_ORDER.search(t):
        return "NEXUS", "order"
    if _pending_write(phone) or _phone_msg_command(t, phone):
        return "NEXUS", "phone"  # Rueckfrage "An wen?"/"Was soll ich schreiben?" oder Schreib-Befehl
    for agent in ("NEXUS GPT", "Hermes", "Aether"):
        if _RX[agent].search(t):
            return agent, "talk"
    try:
        from nexus.voice import call
        if call.weather_request(t):
            return "NEXUS", "weather"
    except Exception:
        pass
    if _phone_msg_command(t):
        return "NEXUS", "phone"
    if _phone_command(t, from_phone=from_phone):
        return "NEXUS", "phone"
    if _wants_pc(t) or not from_phone:
        try:
            from nexus.voice import pc as pc_mod
            if pc_mod.parse(t):
                return "NEXUS", "pc"
        except Exception:
            pass
    else:
        # "am PC" in parse() allein; ohne Geraetewort nicht den PC-Pfad erzwingen
        try:
            from nexus.voice import pc as pc_mod
            if pc_mod.parse(t) and _wants_pc(t):
                return "NEXUS", "pc"
        except Exception:
            pass
    if _is_action(t):
        return "NEXUS", "action"
    if _needs_web(t):
        return "NEXUS", "web"
    return "NEXUS", "talk"


def _strip_address(text: str, agent: str) -> str:
    rx = _RX.get(agent)
    return (rx.sub("", text, count=1).lstrip(" ,.:;!?-") if rx else text).strip() or text


def _transcript(history: list[dict], limit: int = 8) -> str:
    lines = []
    for m in history[-limit * 2:]:
        who = "Eddie" if m.get("role") == "user" else str(m.get("agent") or "NEXUS")
        lines.append(f"{who}: {m.get('content')}")
    return "\n".join(lines)


def _call_turns(history: list[dict], speaker: str) -> list[dict]:
    """Verlauf im Format von nexus.voice.call (turns mit segments) fuer _hermes_reply."""
    turns, pending = [], None
    for m in history:
        if m.get("role") == "user":
            pending = m.get("content")
        elif m.get("role") == "assistant" and pending:
            turns.append({"text": pending, "segments": [{"speaker": str(m.get("agent") or "NEXUS").lower(),
                                                         "reply": m.get("content")}]})
            pending = None
    return turns


def reply_stream(engine, agent: str, mode: str, text: str, history: list[dict], call_id: str,
                 meta: dict) -> Iterator[str]:
    """Liefert Text-Tokens (NEXUS gestreamt, andere Agenten als ganzer Text). meta wird ergaenzt."""
    meta.update(agent=agent, mode=mode)
    if mode == "weather":
        from nexus.voice import live
        r = live.weather(text, default_place="Gelsenkirchen")
        meta.update(model="open-meteo", source=r.get("source"), verified=True, day=r.get("day"))
        yield str(r.get("spoken") or "Dazu habe ich gerade keine Wetterdaten bekommen.")
        return
    if mode == "web":
        box: dict = {"spoken": None, "ok": False, "source": None, "err": None}

        def _run_web():
            try:
                spoken, info = _web_answer(text)
                box.update(spoken=spoken, **(info or {}))
            except Exception as exc:
                box["spoken"] = "Ich komme gerade nicht an die Suche. Die Anfrage ist fehlgeschlagen."
                box["err"] = type(exc).__name__

        th = threading.Thread(target=_run_web, name="livetalk-web", daemon=True)
        th.start()
        yield "Moment, ich schau nach."
        th.join(20)
        if th.is_alive():
            meta.update(model="research", verified=True, success=False, error="timeout")
            yield " Die Suche dauert zu lange, ich breche ab."
            return
        meta.update(model="research", source=box.get("source"), verified=True,
                    success=bool(box.get("ok")), error=box.get("err"))
        yield " " + (box.get("spoken") or "Ich habe dazu gerade keine aktuellen Treffer gefunden.")
        return
    if mode == "phone":
        link = meta.get("phone")
        cmd = (_phone_msg_command(text, link)
               or ({"intent": "phone_write_followup", "text": text} if _pending_write(link) else None)
               or _phone_command(text, from_phone=bool(meta.get("from_phone", True)), link=link)
               or {"intent": "unknown"})
        r = _run_phone(cmd, meta)
        spoken = _spoken_result(r, full=cmd.get("intent") == "phone_read_notifications")
        meta.update(model="phone_adb", intent=cmd.get("intent"), verified=True,
                    success=r.get("success"), error=r.get("error"))
        yield spoken
        return
    if mode == "pc":
        from nexus.voice import call, pc as pc_mod
        cmd = pc_mod.parse(text)
        ids = {"turn_id": None, "request_id": "lt", "agent": "nexus", "session_id": "CALL-" + call_id + "-nexus"}
        _st, out = call._pc_turn(engine, {"speak": False}, call_id, "nexus", text, cmd, 0, ids)
        meta.update(model="pc_action", pc_action=out.get("pc_action"), verified=True)
        yield str(out.get("spoken") or out.get("reply") or "")
        return
    if mode in ("order", "action"):
        from nexus.voice import call
        r = call._nexus_reply(engine, call_id, _voice_norm(text), None)
        spoken = _spoken_result(r)
        meta.update(model=r.get("model") or "nexus_engine", engine=True, verified=True,
                    intent=r.get("intent"), error=r.get("error"), success=r.get("success"))
        yield spoken
        return
    if agent == "Hermes":
        from nexus.voice import call
        r = call._hermes_reply(engine, _call_turns(history, "hermes"), _strip_address(text, agent))
        meta.update(model=r.get("model"), error=r.get("error"))
        yield str(r.get("reply") or "Hermes antwortet gerade nicht.")
        return
    if agent == "NEXUS GPT":
        from nexus.integrations import gpt_local_agent as gpt
        prompt = (f"Bisheriges Live-Gespraech:\n{_transcript(history)}\n\nEddie sagt jetzt zu dir: "
                  f"{_strip_address(text, agent)}")
        r = gpt.ask(prompt, system="Du bist NEXUS GPT, die zusaetzliche lokale KI in Eddies NEXUS-Team." + VOICE_NOTE)
        meta.update(model=r.get("model"), error=r.get("error"))
        yield str(r.get("reply") or "NEXUS GPT antwortet gerade nicht.")
        return
    if agent == "Aether":
        from nexus.integrations import aether_agent as ae
        msg = _strip_address(text, agent).lstrip("/")
        prompt = f"Bisheriges Live-Gespraech:\n{_transcript(history)}\n\nEddie sagt jetzt zu dir: {msg}{VOICE_NOTE}"
        r = ae.ask(prompt)
        meta.update(model=r.get("model"), error=r.get("error"))
        yield str(r.get("reply") or "Aether antwortet gerade nicht.")
        return
    # NEXUS: Gespraech, gestreamt
    llm = engine.master.llm
    messages = [{"role": "system", "content": LIVE_SYSTEM + "\n\n" + _now_berlin()}]
    for m in history[-16:]:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            c = m["content"] if m.get("role") == "user" or (m.get("agent") or "NEXUS") == "NEXUS" else \
                f"({m.get('agent')} hat gesagt:) {m['content']}"
            messages.append({"role": m["role"], "content": c})
    messages.append({"role": "user", "content": text})
    from nexus.common import think_panel
    tok = think_panel.set_override(False)  # Live Talk: nie "denken" (Latenz), unabhaengig vom Denk-Fenster
    got = False
    try:
        for ev in llm.chat_stream(messages, temperature=0.5, model=LIVE_MODEL):
            if ev.get("type") == "token" and ev.get("text"):
                got = True
                yield ev["text"]
            elif ev.get("type") == "done":
                meta.update(model=ev.get("model"), error=ev.get("error"))
                if not got:
                    err = ev.get("error")
                    meta["failed"] = True
                    yield ("Die lokale KI ist gerade belegt, sag es bitte gleich nochmal." if err == "ollama_busy" else
                           "Ich erreiche die lokale KI gerade nicht." if err in ("ollama_down", "no_model") else
                           "Da kam gerade keine Antwort von der lokalen KI.")
                break
    finally:
        try:
            think_panel.reset_override(tok)
        except Exception:
            pass


def guard_sentence(sentence: str, meta: dict) -> tuple[str, bool]:
    """Wahrheitsschutz aus dem Anruf (call.guard) je Satz; nur fuer reine Modellantworten."""
    if meta.get("verified"):
        return sentence, False
    if _FAKE_DONE.search(sentence or ""):
        meta["guarded"] = "unverified_action"
        return ("Ausgefuehrt habe ich dabei nichts. Sag zum Beispiel: Oeffne WhatsApp, "
                "oder: Schreib Anna Hallo, dann mache ich es wirklich."), True
    try:
        from nexus.voice import call
        out, why = call.guard(sentence, False)
        if why:
            meta["guarded"] = why
            return out, True
    except Exception:
        pass
    return sentence, False
