"""Fruehe Routen ohne LLM + Intent-Klassifikation (auch Sprachbefehle)."""
from __future__ import annotations
import re
from urllib.parse import urlparse
from pathlib import Path

CAPABILITIES_SHORT = (
    "NEXUS — lokal auf deinem PC.\n"
    "• Chat, Tools, Shell, Projekte bauen (echt, kein Simulieren)\n"
    "• PC steuern: Apps/Dateien/Websites, Media, Recherche, YouTube\n"
    "• Stimme: Edge-TTS Amala + Anruf /m + Sprachnachricht\n"
    "Mehr Details: «was kannst du alles»"
)

CAPABILITIES_DE = (
    "Ich bin NEXUS — dein lokaler KI-Partner.\n"
    "• Chat & Planung (Ollama, Streaming)\n"
    "• Echte PowerShell-/Shell-Befehle + run_script (.ps1/.bat/.sh) im Projektroot (kein Simulieren)\n"
    "• Projekt-Scaffolding (Vite/npm) via workspace/scripts/ — echte Builds, keine Simulation\n"
    "• Projekt-Dateien lesen/schreiben/listen + Build-Tools\n"
    "• PC steuern: Website oeffnen, Datei/Ordner oeffnen, Apps starten, Musik abspielen\n"
    "  Beispiele: oeffne youtube.com | oeffne explorer | starte chrome | spiel musik | oeffne workspace\n"
    "• Sprachbefehle: sag „Nexus, …“ oder aktiviere Live/Mic\n"
    "  Beispiele: neuer Chat, Pause, weiter, naechster, Bildschirm an,\n"
    "  nach oben scrollen, analysiere Bildschirm, merk dir …, suche …\n"
    "• Projekte, Tasks, Memory\n"
    "• Live-Web-Recherche (DuckDuckGo) via «suche …» / aktuelle News-Fragen — nie Fake-News\n"
    "• YouTube: Suche + ECHTE Wiedergabe — «spiel … auf youtube» / «schau … auf youtube» oeffnet watch?v=… im Browser; «suche auf youtube …» listet Treffer und spielt den besten\n• Apps: WhatsApp (auch Tippfehler üffne/whats app), Maps, Gmail, Chrome, …\n• Lautstaerke: lauter / leiser / stumm (Windows Media-Keys)\n• Taschenlampe/Torch: nur Mobil — auf dem PC BLOCKED\n"
    "• Medien, Downloads, Streams, Screen+Vision\n"
    "• Apps-Hub: siehe NEXUS_APP_CONNECTIONS.md (sofort / Login / spaeter)\n"
    "• Crew-Agenten: @Coder @Scout @Fixer @Media @Ops @Lens @Rap — siehe NEXUS_CREW.md\n"
    "• Self-Improve: verbessere dich -> SELF_IMPROVE_QUEUE.md (Paritaet Grok Bot)\n"
    "• Stimme: Edge-TTS de-DE-AmalaNeural; Call/Mobile immer sprechen; nie text-only behaupten; nur schreiben=Text"
)

NO_LIVE_WORLD_DE = (
    "Kein Live-Nachrichten-Feed — ich erfinde keine Welt-News oder Trends.\n"
    "Für echte Web-Treffer: schreib «suche <Thema>» (DuckDuckGo-Recherche).\n"
    "Lokal kann ich u. a.: PC steuern (Apps/Dateien/Websites öffnen), Shell/Skripte, "
    "Memory, Media/Streams/Downloads, Monitor/Leistung, Voice (AmalaNeural), "
    "Projekte/Tasks, Crew (@Coder …), Screen/Vision.\n"
    "Beispiele: öffne youtube.com | zeig leistung | spiel musik | was kannst du"
)


def _url_in(text: str) -> str | None:
    m = re.search(r"https?://[^\s<>\"']+", text)
    return m.group(0) if m else None


def strip_wake(message: str) -> str:
    """Entfernt Wake-Woerter am Anfang."""
    raw = (message or "").strip()
    pat = re.compile(
        r"^(hey\s+)?(nexus|nexxus|nexa)([,\s]+|[:\-]\s*)",
        re.I,
    )
    return pat.sub("", raw, count=1).strip() or raw



def _shader_script_hint(low: str) -> tuple[str | None, str | None]:
    """Returns (script_hint, note) for shader/vfx builds."""
    if not re.search(r"\b(shader|vfx|aurora)\b", low):
        return None, None
    note = None
    try:
        from nexus.common.paths import SCRIPTS_DIR
        shader_ok = (SCRIPTS_DIR / "New-ShaderProject.ps1").is_file()
    except Exception:
        shader_ok = Path(__file__).resolve().parents[2].joinpath(
            "workspace", "scripts", "New-ShaderProject.ps1"
        ).is_file()
    if shader_ok:
        return "workspace/scripts/New-ShaderProject.ps1", None
    return "workspace/scripts/Setup-AI-Agent.ps1", "New-ShaderProject.ps1 fehlt — Fallback Setup-AI-Agent"



def _normalize_cmd(message: str) -> str:
    """DE-Tippfehler und Fuzzy-Open-Verben normalisieren (üffne→öffne, whats app→whatsapp)."""
    raw = (message or "").strip()
    # Open-Verb-Tippfehler am Anfang
    raw = re.sub(
        r"^(üffne|ueffne|uffne|ofne|öfne|oefne|oeffene|öffene|mach\s+auf)\b",
        "öffne",
        raw,
        count=1,
        flags=re.I,
    )
    # WhatsApp-Schreibweisen (Leerzeichen/Bindestrich/Case)
    raw = re.sub(r"\bwhats\s*[-_]?\s*app\b", "whatsapp", raw, flags=re.I)
    raw = re.sub(r"\bwatsapp\b", "whatsapp", raw, flags=re.I)
    raw = re.sub(r"\bwhatapp\b", "whatsapp", raw, flags=re.I)
    return raw


def _mouse_coords(text: str) -> tuple[int, int] | None:
    """Liest (x, y) aus einem Maus-Befehl; zuerst per x=/y=-Label, sonst erste zwei Zahlen."""
    xm = re.search(r"(?:^|[^a-z0-9])x\s*[:=]?\s*(-?\d+)", text)
    ym = re.search(r"(?:^|[^a-z0-9])y\s*[:=]?\s*(-?\d+)", text)
    if xm and ym:
        return int(xm.group(1)), int(ym.group(1))
    nums = re.findall(r"-?\d+", text)
    if len(nums) >= 2:
        return int(nums[0]), int(nums[1])
    return None


def _mouse_has_labels(text: str) -> bool:
    """True, wenn x und y explizit als Koordinaten-Labels stehen (x=…, y=…)."""
    return bool(
        re.search(r"(?:^|[^a-z0-9])x\s*[:=]?\s*-?\d+", text)
        and re.search(r"(?:^|[^a-z0-9])y\s*[:=]?\s*-?\d+", text)
    )


def _norm_app_name(name: str) -> str:
    n = (name or "").strip().lower()
    n = re.sub(r"\s+", " ", n)
    n = re.sub(r"\bwhats\s*[-_]?\s*app\b", "whatsapp", n)
    n = n.replace("whats-app", "whatsapp").replace("whats app", "whatsapp")
    n = n.strip(" .!?,;:\"'")
    return n.strip()

def _directory_list_request(raw: str) -> str | None:
    """Narrow read-only directory command; no inferred path or shell fallback."""
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        return None
    allowed_tails = {
        "verwende dafür ausschließlich das werkzeug list_dir.",
        "verwende dafuer ausschliesslich das werkzeug list_dir.",
        "zeige dessen tatsächliches ergebnis. keine dateien verändern.",
        "zeige dessen tatsaechliches ergebnis. keine dateien veraendern.",
        "keine dateien ändern, keine shell, keine installation.",
        "keine dateien aendern, keine shell, keine installation.",
        "zeige das tatsächliche werkzeugergebnis.",
        "wenn kein werkzeug ausgeführt wurde, melde not_executed.",
    }
    if any(line.lower() not in allowed_tails for line in lines[1:]):
        return None
    match = re.fullmatch(
        r"(?:list_dir\s+|(?:liste|zeige|zeig)\s+(?:die\s+)?"
        r"(?:dateien|inhalt)\s+(?:im\s+(?:ordner|verzeichnis)|(?:des|vom)\s+(?:ordners|verzeichnisses))\s+)"
        r"(.+?)(?:\s+auf\.?)?",
        lines[0], re.I,
    )
    if not match:
        return None
    path = match.group(1).strip()
    if path.startswith(('"', "'")):
        if len(path) < 2 or path[-1] != path[0]:
            return None
        path = path[1:-1]
    if not path or any(c in path for c in ('"', "'", '\n', '\r', ';', '|', '<', '>')):
        return None
    if not re.match(r"^(?:[A-Za-z]:[\\/]|[\\/]|workspace[\\/]|nexus[\\/]|\.$)", path):
        return None
    return path


def strip_answer_policy(text):
    """2026-10-26 BLOCKED-Fix: UI-Suffix [Antwortvorgabe: ...] gehoert nicht zum Auftrag."""
    return re.sub(r"\s*\[Antwortvorgabe:[^\]]*\]\s*$", "", str(text or "")).strip()


def _decide_base(message: str) -> dict:
    """Gibt intent + ggf. early_reply / params zurueck."""
    # 2026-11-09 FIXALL: Rollenspiel/Konzil/Haus nie als Werkzeug-/Maus-/Bau-Auftrag.
    raw = strip_wake(strip_answer_policy(message or ""))
    raw = _normalize_cmd(raw)
    low = raw.lower().strip()

    # 2026-11-09 FIXALL: Spielprompts (Konzil/Haus/Rollenspiel) immer Chat, nie Tools/OS.
    if re.search(
        r"\b(rollenspiel|spielregeln|konzil der|haus der (?:fuenf|fünf) t(?:ue|ü)ren|ethik-planspiel)\b",
        low,
        re.I,
    ) or ("simulation" in low and "spiel" in low):
        return {"intent": "chat", "early": False, "game": True}

    if not raw:
        return {"intent": "chat", "early": True, "reply": "Sag mir was — ich hoere zu."}

    # NOTIF_READ_V1: WhatsApp/Windows-Benachrichtigungen vorlesen -> echtes Tool, nie Websuche
    try:
        from nexus.computer.notifications import match_request as _nr_match
        _nr = _nr_match(raw)
    except Exception:
        _nr = None
    if _nr is not None:
        return {"intent": "notifications_read", "early": True, "app": _nr.get("app")}

    directory_path = _directory_list_request(raw)
    if directory_path is not None:
        return {"intent": "project_list_dir", "early": True, "path": directory_path}

    # Uhrzeit / Datum — echte lokale Zeit, kein Platzhalter
    if re.search(r"\b(wie\s+sp(?:ae|[aä])t|uhrzeit|wie\s+viel\s+uhr|welche[sr]?\s+tag|welches\s+datum|heutiges\s+datum)\b", low) or low in ("zeit", "datum", "uhr"):
        from datetime import datetime
        now = datetime.now()
        reply = now.strftime("Es ist %H:%M Uhr am %d.%m.%Y.")
        return {"intent": "time", "early": True, "reply": reply}

    # Taschenlampe / Torch — nur Mobil, auf Windows-Desktop BLOCKED
    if re.search(r"\b(torch|taschenlampe|flashlight)\b", low) and re.search(
        r"\b(an|aus|on|off|ein|aktivieren|deaktivieren)\b", low
    ) or re.match(r"^(torch|taschenlampe|flashlight)\b", low):
        return {
            "intent": "torch",
            "early": True,
            "reply": "BLOCKED: Taschenlampe/Torch gibt es auf dem Windows-PC nicht (nur Mobil/Android).",
            "blocked": True,
            "error": "torch_pc_na",
        }

    # Lautstaerke (Windows Media-Keys)
    if re.search(r"\b(lauter|volume\s*up|vol\+|lautst(?:ä|ae)rke\s*(hoch|rauf|up)|mach\s+lauter)\b", low) or low in ("lauter", "volume up", "+"):
        return {"intent": "os_volume", "early": True, "action": "up", "steps": 4}
    if re.search(r"\b(leiser|volume\s*down|vol\-|lautst(?:ä|ae)rke\s*(runter|runter|down)|mach\s+leiser)\b", low) or low in ("leiser", "volume down", "-"):
        return {"intent": "os_volume", "early": True, "action": "down", "steps": 4}
    if re.search(r"\b(stumm|mute|unmute|ton\s*aus|ton\s*an|unmute)\b", low) and len(low) < 40:
        act = "unmute" if re.search(r"\b(unmute|ton\s*an)\b", low) else "mute"
        return {"intent": "os_volume", "early": True, "action": act}

    # Mauszeiger exakt auf Koordinaten setzen (Bridge-Task mouse_move, PRIVILEGED)
    if (
        re.search(r"\b(maus(?:zeiger|pfeil)?|zeiger|cursor|mauscursor)\b", low)
        and (
            re.search(r"\b(beweg|setz|stell|fahr|positionier|leg|schieb|move)\w*\b", low)
            or _mouse_has_labels(low)
        )
    ):
        coords = _mouse_coords(low)
        if coords is not None:
            return {"intent": "os_mouse", "early": True, "x": coords[0], "y": coords[1]}


    # Crew / Spezialagenten (@Coder, @Scout, …) — VOR Build/Shell
    crew_match = re.match(
        r"^@(Coder|Scout|Fixer|Media|Ops|Lens|Rap|CODE|FILES|TERMINAL|SYSTEM|WEB|VOICE|UI)\b\s*(.*)$",
        raw,
        re.I | re.S,
    )
    if not crew_match:
        crew_match = re.match(
            r"^(?:agent|crew)\s*[:\s]+(coder|scout|fixer|media|ops|lens|rap|code|files|terminal|system|web|voice|ui)\b\s*(.*)$",
            raw,
            re.I | re.S,
        )
    if not crew_match:
        crew_match = re.match(
            r"^(?:frag|frage|ruf|rufe)\s+(coder|scout|fixer|media|ops|lens|rap|code|files|terminal|system|web|voice|ui)\b\s*(.*)$",
            raw,
            re.I | re.S,
        )
    if not crew_match:
        crew_match = re.match(
            r"^crew\s+(coder|scout|fixer|media|ops|lens|rap|code|files|terminal|system|web|voice|ui)\b\s*(.*)$",
            raw,
            re.I | re.S,
        )
    if crew_match:
        agent_id = crew_match.group(1).strip().lower().lstrip("@")
        query = (crew_match.group(2) or "").strip() or raw
        return {
            "intent": "crew",
            "early": False,
            "force_tools": True,
            "agent_id": agent_id,
            "query": query,
        }


    # Capabilities / Hilfe — NUR kurze reine Hilfe-Fragen (nicht bei "funktionen kaputt" / Aufträgen)
    help_exact = re.match(
        r"^(was kannst du(?:\s+alles)?|hilfe|capabilities|hilfe bitte|was kann ich sagen|"
        r"welche befehle|sprachbefehle\??|zeig(?:e)?\s+was\s+du\s+kannst|"
        r"welche\s+funktionen|f(?:ä|ae)higkeiten)\s*[?.!]?$",
        low,
    )
    help_phrase = (
        len(low) < 48
        and any(
            low.startswith(x) or low == x
            for x in (
                "was kannst du",
                "was kannst du alles",
                "hilfe",
                "capabilities",
                "was kann ich sagen",
                "welche befehle",
                "sprachbefehl",
                "sprachbefehle",
                "zeig was du kannst",
                "zeige was du kannst",
                "welche funktionen",
                "fähigkeiten",
                "faehigkeiten",
            )
        )
        and not any(
            w in low
            for w in (
                "nicht",
                "kaputt",
                "fehler",
                "beheb",
                "fix",
                "bau",
                "ausführ",
                "funktionieren",
                "broken",
            )
        )
    )
    if help_exact or help_phrase:
        want_full = bool(re.search(r"\b(alles|detail|ausfuehrlich|ausführlich|liste|vollstaendig|vollständig)\b", low))
        return {
            "intent": "capabilities",
            "early": True,
            "reply": CAPABILITIES_DE if want_full else CAPABILITIES_SHORT,
        }

    # Skript ausführen / Vite-Scaffold / npm create — VOR Monitor (lange Timeouts)
    if re.search(
        r"\b(f(?:ü|ue)hre\s+skript|run\s+script|skript\s+ausf(?:ü|ue)hren|"
        r"lege\s+vite|vite\s+projekt|npm\s+create|baue\s+shader|shader\s+projekt|"
        r"new-shaderproject|setup-ai-agent|ai[- ]?agent|mein[- ]?agent|"
        r"create[- ]vite|scaffold)\b",
        low,
    ):
        # Expliziter Skriptpfad?
        m_path = re.search(
            r"(?:skript|script)\s+[\"']?([\w./\\-]+\.(?:ps1|bat|cmd|sh))[\"']?",
            raw,
            re.I,
        )
        if not m_path:
            m_path = re.search(
                r"\b(workspace[/\\]scripts[/\\][\w.-]+\.(?:ps1|bat|cmd|sh)|"
                r"New-ShaderProject\.ps1|[\w.-]+\.ps1)\b",
                raw,
                re.I,
            )
        if m_path:
            spath = m_path.group(1).replace("\\", "/")
            after = raw[m_path.end():].strip()
            after = re.sub(r"^(aus\s+)", "", after, flags=re.I).strip()
            script_args = after if after else None
            return {
                "intent": "run_script",
                "early": True,
                "action": "run",
                "path": spath,
                "args": script_args,
                "timeout": None,  # ShellExecutor nutzt SHELL_TIMEOUT_BUILD
                "force_tools": False,
            }
        # Ohne Pfad: Agent mit Build-Timeout-Hinweis
        # Default scaffold = Setup-AI-Agent; Shader nur bei explizitem Keyword + Skript vorhanden
        hint_path = "workspace/scripts/Setup-AI-Agent.ps1"
        note = None
        if re.search(r"\b(setup-ai-agent|ai[- ]?agent|mein[- ]?agent)\b", low):
            hint_path = "workspace/scripts/Setup-AI-Agent.ps1"
        elif re.search(r"\b(shader|vfx|aurora)\b", low):
            try:
                from nexus.common.paths import SCRIPTS_DIR
                shader_ok = (SCRIPTS_DIR / "New-ShaderProject.ps1").is_file()
            except Exception:
                shader_ok = Path(__file__).resolve().parents[2].joinpath(
                    "workspace", "scripts", "New-ShaderProject.ps1"
                ).is_file()
            if shader_ok:
                hint_path = "workspace/scripts/New-ShaderProject.ps1"
            else:
                hint_path = "workspace/scripts/Setup-AI-Agent.ps1"
                note = "New-ShaderProject.ps1 fehlt — Fallback Setup-AI-Agent"
        elif re.search(r"\b(vite|npm\s+create|scaffold)\b", low):
            hint_path = "workspace/scripts/Setup-AI-Agent.ps1"
        out = {
            "intent": "build",
            "early": False,
            "force_tools": True,
            "timeout_hint": 600,
            "script_hint": hint_path,
        }
        if note:
            out["script_note"] = note
        return out


    # Selbstbau- / Ausführungsbefehl (vor Diagnose!)
    if re.search(
        r"selbstbau|ausführungsbefehl|ausfuehrungsbefehl|phase\s*a\s*[-–—]\s*f|"
        r"keine\s+simulation|echt\s+bauen|arbeitsschritt|"
        r"\b(baue[n]?|fix(e|en)?|verdraht(e|en)?|nachbesser)\b",
        low,
    ) and (
        "selbstbau" in low
        or "ausführungsbefehl" in low
        or "ausfuehrungsbefehl" in low
        or "keine simulation" in low
        or "phase a" in low
        or re.search(r"\b(baue|bauen|fix|fixen|verdrahte|nachbesser)\b", low)
    ):
        out = {"intent": "build", "early": False, "force_tools": True, "timeout_hint": 600, "self_build": True}
        sh, note = _shader_script_hint(low)
        if sh:
            out["script_hint"] = sh
        if note:
            out["script_note"] = note
        return out

    # --- UI / lokale Aktionen (Client fuehrt aus, Server bestaetigt) ---
    if re.search(r"\b(neuer chat|neuen chat|chat neu|new chat)\b", low):
        return {"intent": "ui", "action": "new_chat", "early": True, "reply": "Neuer Chat ist bereit."}
    if re.search(r"\b(chat (loeschen|löschen)|lösche chat|loesche chat)\b", low):
        return {"intent": "ui", "action": "delete_chat", "early": True, "reply": "Chat geloescht."}
    if re.search(r"\b(nach oben|scroll(e)? hoch|seite hoch|page up)\b", low):
        return {"intent": "ui", "action": "scroll_up", "early": True, "reply": "Scrolle nach oben."}
    if re.search(r"\b(nach unten|scroll(e)? runter|seite runter|page down)\b", low):
        return {"intent": "ui", "action": "scroll_down", "early": True, "reply": "Scrolle nach unten."}
    if re.search(r"\b(ganz nach oben|zum anfang)\b", low):
        return {"intent": "ui", "action": "scroll_top", "early": True, "reply": "Ganz nach oben."}
    if re.search(r"\b(ganz nach unten|zum ende)\b", low):
        return {"intent": "ui", "action": "scroll_bottom", "early": True, "reply": "Ganz nach unten."}

    # Screen
    if re.search(r"\b(bildschirm(freigabe)? (an|starten|teilen)|screen (an|on|start)|teile (den )?bildschirm)\b", low):
        return {"intent": "ui", "action": "screen_on", "early": True, "reply": "Bildschirmfreigabe starten."}
    if re.search(r"\b(bildschirm(freigabe)? (aus|stopp|stop)|screen (aus|off|stop)|beende screen)\b", low):
        return {"intent": "ui", "action": "screen_off", "early": True, "reply": "Bildschirmfreigabe beendet."}
    if re.search(r"\b(analysiere? (den )?bildschirm|was siehst du|screen analysieren|schau (auf|dir) (den )?bildschirm)\b", low):
        return {"intent": "ui", "action": "screen_analyze", "early": True, "reply": "Analysiere den Bildschirm."}

    # Voice control
    if re.search(r"\b(sprachchat aus|live aus|voice aus|hoer auf zu hoeren|hör auf zu hören|stop voice)\b", low):
        return {"intent": "ui", "action": "voice_off", "early": True, "reply": "Sprachmodus aus."}
    # Nur Text / nicht vorlesen (Auto-Speak aus)
    if re.search(
        r"\b(nur schreiben|nur text|nicht sprechen|ohne stimme|ohne vorlesen|textmodus|stumm ?modus|hör auf zu sprechen|hoer auf zu sprechen)\b",
        low,
    ):
        return {
            "intent": "ui",
            "action": "speak_off",
            "early": True,
            "reply": "Alles klar — ab jetzt nur Text, ohne Vorlesen.",
        }
    # Wieder sprechen (Auto-Speak an)
    if re.search(
        r"\b(sprich wieder|stimme an|sprechmodus|antworte laut|wieder sprechen|vorlesen an|auto speak)\b",
        low,
    ):
        return {
            "intent": "ui",
            "action": "speak_on",
            "early": True,
            "reply": "Alles klar — ab jetzt lese ich Antworten vor.",
        }
    if re.search(r"\b(spraeche? dich|lies vor|vorlesen|laut vorlesen)\b", low):
        return {"intent": "ui", "action": "speak_last", "early": True, "reply": "Ich lese die letzte Antwort vor."}

    # Bau / Implementierung VOR Monitor-Keywords (sonst frisst "system monitor" den Bauauftrag)
    if re.search(
        r"\b(baue|bauen|build|implementiere|implementier|patch(e)?|lege an|erstelle modul|entwickelt|"
        r"schreib(e)? (den |eine |das )?code|neu bau|neubau|fix(e|en)?|verdraht(e|en)?|selbstbau)\b",
        low,
    ):
        out = {"intent": "build", "early": False, "force_tools": True, "timeout_hint": 600}
        sh, note = _shader_script_hint(low)
        if sh:
            out["script_hint"] = sh
        if note:
            out["script_note"] = note
        return out

    # System-Monitor: echte Zahlen antworten
    if re.search(
        r"\b(auslastung|ram frei|arbeitsspeicher|festplatte|speicherplatz|"
        r"was belastet|top prozesse|top[- ]prozesse|ist ollama|ollama (an|aus|status|online)|"
        r"netzwerk(status)?|bandbreite|system[- ]?monitor|systemmonitor|alerts?|warnungen|"
        r"welche prozesse|prozessliste|cpu[- ]?auslastung|gpu[- ]?auslastung|grafikkarte)\b",
        low,
    ) or re.search(r"\b(cpu|ram|gpu|vram|disk)\b.*\b(hoch|frei|voll|status|auslast)\b", low) or re.search(
        r"\b(wie hoch|wie viel|wieviel)\b.*\b(cpu|ram|gpu|vram|speicher|arbeitsspeicher|festplatte|disk|auslastung|prozessor|netzwerk|bandbreite)\b", low
    ) or re.search(r"\bsystem[- ]?status\b", low):  # 2026-11-02 chatfix: "wie viel" allein ist keine Statusfrage
        return {"intent": "monitor", "early": True, "query": raw, "reply": None}

    # Leistungspanel öffnen (UI)
    if re.search(
        r"\b(leistung|leistungsanzeige|systemleistung|performance|system[- ]?metrics|"
        r"zeige\s+(cpu|leistung|leistungsanzeige)|öffne\s+leistung|oeffne\s+leistung|"
        r"perf\s*panel|öffne systemmonitor|oeffne systemmonitor)\b",
        low,
    ):
        return {"intent": "ui", "action": "open_perf", "early": True, "reply": "Leistungsanzeige geöffnet."}

# Streams panel
    if re.search(r"\b(oeffne streams|öffne streams|streams (oeffnen|öffnen)|zeige streams)\b", low):
        return {"intent": "ui", "action": "streams_open", "early": True, "reply": "Streams-Panel offen."}

    # Apps / Videos / Downloads öffnen (UI)
    if re.search(r"\b(oeffne apps|öffne apps|apps (oeffnen|öffnen)|zeige apps|apps[- ]?hub|app hub)\b", low):
        return {"intent": "ui", "action": "apps_open", "early": True, "reply": "Apps-Hub geöffnet."}
    if re.search(r"\b(oeffne videos|öffne videos|videos (oeffnen|öffnen)|zeige videos|videoplayer)\b", low):
        return {"intent": "ui", "action": "videos_open", "early": True, "reply": "Videos-Panel geöffnet."}
    if re.search(r"\b(oeffne downloads|öffne downloads|downloads (oeffnen|öffnen)|zeige downloads)\b", low):
        return {"intent": "ui", "action": "downloads_open", "early": True, "reply": "Downloads: schreib „download https://…“ in den Chat."}


    # YouTube PLAYBACK — echte watch?v=… Wiedergabe (VOR generischem "spiel musik" / media)
    m = re.match(
        r"^(?:spiel(?:e)?|play|schau(?:e)?|watch|guck(?:e)?|zeig(?:e)?)\s+(.+?)\s+(?:auf|bei|on)\s+(?:youtube|yt)\s*$",
        raw,
        re.I | re.S,
    )
    if m and m.group(1).strip():
        q = m.group(1).strip()
        q = re.sub(r"^(?:das\s+)?(?:video|clip|lied|song|musik)\s+", "", q, flags=re.I).strip() or q
        return {"intent": "youtube_play", "early": False, "query": q, "play_first": True}
    m = re.match(
        r"^(?:youtube|yt)\s+(?:spiel(?:e)?|play|watch|schau(?:e)?)\s*[:\-]?\s*(.+)$",
        raw,
        re.I | re.S,
    )
    if m and m.group(1).strip():
        return {"intent": "youtube_play", "early": False, "query": m.group(1).strip(), "play_first": True}
    m = re.match(
        r"^(?:spiel(?:e)?|play|watch)\s+(?:auf\s+|bei\s+)?(?:youtube|yt)\s*[:\-]?\s*(.+)$",
        raw,
        re.I | re.S,
    )
    if m and m.group(1).strip():
        return {"intent": "youtube_play", "early": False, "query": m.group(1).strip(), "play_first": True}
    # "… youtube abspielen/oeffnen" mit Query davor
    m = re.match(
        r"^(.+?)\s+(?:auf\s+|bei\s+)?(?:youtube|yt)\s+(?:abspielen|spielen|oeffnen|öffnen|anschauen|watch)\s*$",
        raw,
        re.I | re.S,
    )
    if m and m.group(1).strip() and not re.match(r"^(suche|search|finde)", m.group(1), re.I):
        return {"intent": "youtube_play", "early": False, "query": m.group(1).strip(), "play_first": True}

    # Musik-App / Datei bevor Browser-Playlist-Toggle
    if re.search(r"\b(spiel(e)?\s+musik|musik\s+abspielen|play\s+music|starte\s+(musik|spotify)|oeffne\s+spotify|öffne\s+spotify)\b", low):
        return {"intent": "os_play_media", "early": True, "path": None}

    # Media controls - nur eindeutige Medienbefehle.
    # Allgemeine Woerter wie "weiter", "naechster" oder "zurueck"
    # duerfen technische/konversationelle Nachrichten nicht abfangen.
    media_text = re.sub(r"\s+", " ", low).strip().rstrip(" .!?")

    # PAUSE
    if (
        media_text in {
            "pause",
            "pausieren",
            "pausiere",
            "musik pause",
            "musik pausieren",
            "wiedergabe pausieren",
            "player pausieren",
        }
        or re.fullmatch(
            r"(?:bitte\s+)?(?:pausiere|pausieren|pause|stoppe|stopp)\s+"
            r"(?:die\s+)?(?:musik|wiedergabe|player|song|track|lied)",
            media_text,
            re.I,
        )
    ):
        return {
            "intent": "media",
            "action": "pause",
            "early": True,
            "reply": "Pause.",
        }

    # PLAY / RESUME
    # "weiter" allein oder "weiter mit dem Bau" sind KEIN Media.
    if (
        media_text in {
            "musik weiter",
            "musik fortsetzen",
            "wiedergabe weiter",
            "wiedergabe fortsetzen",
            "player fortsetzen",
            "song weiter",
            "track weiter",
        }
        or re.fullmatch(
            r"(?:bitte\s+)?(?:spiele|spiel|starte|setze)\s+"
            r"(?:die\s+)?(?:musik|wiedergabe|player|song|track|lied)"
            r"(?:\s+fort|\s+weiter)?",
            media_text,
            re.I,
        )
    ):
        return {
            "intent": "media",
            "action": "play",
            "early": True,
            "reply": "Wiedergabe.",
        }

    # NEXT
    # "naechster" oder "weiter" allein reichen ausdruecklich NICHT.
    if (
        media_text in {
            "skip",
            "skip song",
            "skip track",
            "nächster song",
            "naechster song",
            "nächstes lied",
            "naechstes lied",
            "nächster track",
            "naechster track",
            "nächster titel",
            "naechster titel",
            "musik nächster",
            "musik naechster",
            "player nächster",
            "player naechster",
        }
        or re.fullmatch(
            r"(?:bitte\s+)?(?:spiel(?:e)?|wechsel|spring|skip)\s+"
            r"(?:zum\s+|auf\s+|den\s+)?"
            r"(?:nächsten|naechsten|nächstes|naechstes|next)\s+"
            r"(?:song|track|lied|musiktitel|titel)",
            media_text,
            re.I,
        )
    ):
        return {
            "intent": "media",
            "action": "next",
            "early": True,
            "reply": "Nächster Titel.",
        }

    # PREVIOUS
    # "zurueck" allein ist KEIN Media.
    if (
        media_text in {
            "vorheriger song",
            "vorheriger track",
            "vorheriges lied",
            "vorheriger titel",
            "letzter song",
            "letzter track",
            "musik zurück",
            "musik zurueck",
            "player zurück",
            "player zurueck",
        }
        or re.fullmatch(
            r"(?:bitte\s+)?(?:spiel(?:e)?|spring|wechsel)\s+"
            r"(?:zum\s+|auf\s+|den\s+)?"
            r"(?:vorherigen|vorheriger|vorheriges|letzten|letzter)\s+"
            r"(?:song|track|lied|musiktitel|titel)",
            media_text,
            re.I,
        )
    ):
        return {
            "intent": "media",
            "action": "prev",
            "early": True,
            "reply": "Vorheriger Titel.",
        }

    # STOP
    # "stop"/"stopp" allein bleibt fuer technische Arbeit frei.
    if (
        media_text in {
            "musik stoppen",
            "musik stopp",
            "musik aus",
            "wiedergabe stoppen",
            "player stoppen",
            "song stoppen",
            "track stoppen",
        }
        or re.fullmatch(
            r"(?:bitte\s+)?(?:stoppe|stopp|beende|beenden)\s+"
            r"(?:die\s+)?(?:musik|wiedergabe|player|song|track|lied)",
            media_text,
            re.I,
        )
    ):
        return {
            "intent": "media",
            "action": "stop",
            "early": True,
            "reply": "Musik gestoppt.",
        }

    # Eddie-Korrekturen / Praeferenzen (besser so / nicht so / immer / nie / ab jetzt)
    if re.match(
        r"^(besser\s+so|nicht\s+so|lieber\s+so|immer(?:\s+so)?|nie(?:\s+wieder)?(?:\s+so)?|"
        r"ab\s+jetzt|korrektur|korrigiere|praeferenz|präferenz|preference|"
        r"mach\s+das\s+(?:immer|nie)|bitte\s+immer|bitte\s+nie)\b",
        raw,
        re.I,
    ) or (
        re.search(r"\b(besser\s+so|nicht\s+so|nie\s+wieder|ab\s+jetzt)\b", low)
        and re.search(r"[:\-]|so\s*:|bitte", raw)
    ):
        return {"intent": "correction_store", "early": True, "text": raw.strip()}

    # Self-Improve / Daily Loop (Chat-Trigger)
    if re.search(
        r"\b("
        r"verbessere\s+dich|selbst\s*verbesser|selbstverbesser|"
        r"improvement\s*loop|daily\s*improve|tägliche?\s+verbesser|"
        r"taegliche?\s+verbesser|lern\s+aus\s+korrekturen|"
        r"schliesse\s+lücken|schließe\s+lücken|capability\s*gap|"
        r"werde\s+besser|nightly\s*check"
        r")\b",
        low,
    ):
        apply = bool(
            re.search(
                r"\b(anwenden|apply|umsetzen|fix\s+jetzt|sofort\s+fix|sichere?\s+fixes?|safe\s+fix)\b",
                low,
            )
        )
        return {
            "intent": "self_improve",
            "early": True,
            "apply_safe": apply,
            "trigger": "chat",
        }

    # Memory
    m = re.match(r"^(merk\s*dir|merke\s*dir|remember)\s*[:\-]?\s*(.+)$", raw, re.I | re.S)
    if m:
        return {"intent": "memory_store", "early": True, "text": m.group(2).strip()}
    m = re.match(r"^(erinner(?:e)?(?:\s+dich)?|recall)\s*[:\-]?\s*(.*)$", raw, re.I | re.S)
    if m:
        return {"intent": "memory_recall", "early": True, "query": (m.group(2) or "").strip()}
    m = re.match(r"^was wei(?:ß|ss)t du (?:über|ueber)\s*[:\-]?\s*(.*)$", raw, re.I | re.S)
    if m:
        return {"intent": "memory_recall", "early": True, "query": (m.group(1) or "").strip()}

    # Download
    if low.startswith("download ") or low.startswith("lade herunter ") or low.startswith("lad herunter "):
        url = _url_in(raw)
        if url:
            return {"intent": "download", "early": True, "url": url}
    url = _url_in(raw)
    if url and any(k in low for k in ("download", "herunterladen", "lade ", "speicher")):
        return {"intent": "download", "early": True, "url": url}


    # YouTube-Suche (echte Treffer — VOR generischer Web-Suche; nie erfundene Titel)
    m = re.match(
        r"^(?:suche|search|recherchiere|finde|zeig(?:e)?)\s+(?:auf\s+|bei\s+)?(?:youtube|yt)\s*[:\-]?\s*(.+)$",
        raw,
        re.I | re.S,
    )
    if m:
        return {
            "intent": "youtube_search",
            "early": False,
            "play_first": True,
            "query": m.group(1).strip(),
            "open_browser": bool(re.search(r"\b(oeffne|öffne|open)\b", low)),
        }
    m = re.match(
        r"^(?:youtube|yt)\s+(?:suche|search|finde|videos?)?\s*[:\-]?\s*(.+)$",
        raw,
        re.I | re.S,
    )
    if m and m.group(1).strip():
        return {
            "intent": "youtube_search",
            "early": False,
            "play_first": True,
            "query": m.group(1).strip(),
            "open_browser": bool(re.search(r"\b(oeffne|öffne|open)\b", low)),
        }
    m = re.match(
        r"^(?:finde|zeig(?:e)?|suche)\s+(?:mir\s+)?(?:videos?|clips?)\s+(?:zu|über|ueber|von|für|fuer)\s+(.+)$",
        raw,
        re.I | re.S,
    )
    if m:
        return {"intent": "youtube_search", "early": False, "play_first": True, "query": m.group(1).strip()}
    m = re.match(
        r"^(?:videos?|clips?)\s+(?:zu|über|ueber|von|für|fuer)\s+(.+)$",
        raw,
        re.I | re.S,
    )
    if m:
        return {"intent": "youtube_search", "early": False, "play_first": True, "query": m.group(1).strip()}
    m = re.match(
        r"^(?:oeffne|öffne|üffne|ueffne|uffne|ofne|öfne|open|mach\s+auf)\s+(?:youtube|yt)\s+(?:suche|search)\s*[:\-]?\s*(.+)$",
        raw,
        re.I | re.S,
    )
    if m:
        return {
            "intent": "youtube_search",
            "early": False,
            "play_first": True,
            "query": m.group(1).strip(),
            "open_browser": True,
        }

    # 2026-10-26 Websuche-Fix: freie Formulierungen ("schau im Netz nach ...", "such im Internet ...",
    # "recherchiere online ...") -> echte Live-Websuche statt Chat/Arbeitsauftrag-Validierung.
    _wq = web_lookup_query(raw)
    if _wq:
        return {"intent": "research", "early": False, "query": _wq}
    # Search (echte DuckDuckGo-Recherche — kein erfundener News-Essay)
    m = re.match(r"^(suche(?:\s+im\s+web)?|search|recherchiere|google|websearch)\s*[:\-]?\s*(.+)$", raw, re.I | re.S)
    if m:
        q = m.group(2).strip()
        # "suche youtube …" / "suche yt …" → YouTube-Tool, nicht generische News-Recherche
        mq = re.match(r"^(?:auf\s+|bei\s+)?(?:youtube|yt)\s*[:\-]?\s*(.+)$", q, re.I | re.S)
        if mq:
            return {"intent": "youtube_search", "early": False, "play_first": True, "query": mq.group(1).strip()}
        if re.search(r"\b(youtube\.com/watch|youtu\.be/)\b", q, re.I):
            return {"intent": "youtube_search", "early": False, "play_first": True, "query": q}
        return {"intent": "research", "early": False, "query": q}
    m = re.match(
        r"^(?:nachrichten|news)\s+(?:zu|über|ueber|about)\s+(.+)$",
        raw,
        re.I | re.S,
    )
    if m:
        return {"intent": "research", "early": False, "query": m.group(1).strip()}


    # Seiten / Browser-Fenster schliessen (UI Embed + OS close_opened)
    if re.search(
        r"\b("
        r"schlie(?:ss|ß)(?:e)?\s+(?:die\s+)?(?:seiten|tabs|fenster|website|webseite|browser)|"
        r"(?:die\s+)?seiten\s+(?:auch\s+)?(?:wieder\s+)?schlie(?:ss|ß)en|"
        r"tabs?\s+(?:auch\s+)?(?:wieder\s+)?schlie(?:ss|ß)en|"
        r"browser\s+(?:zu|schlie(?:ss|ß)en)|mach\s+(?:die\s+)?(?:seiten|tabs|fenster)\s+zu|"
        r"close\s+(?:the\s+)?(?:pages|tabs|browser)|close\s+pages"
        r")\b",
        low,
    ):
        url = _url_in(raw)
        out = {
            "intent": "ui",
            "action": "close_pages",
            "early": True,
            "reply": "Ich schliesse die geoeffneten Seiten.",
        }
        if url:
            out["url"] = url
        return out
    m = re.match(
        r"^(?:schlie(?:ss|ß)(?:e)?|close)\s+(?:die\s+)?(?:website|webseite|seite|url)\s+(.+)$",
        raw,
        re.I,
    )
    if m:
        target = (m.group(1) or "").strip().strip("\"'")
        url = _url_in(target) or target
        if url and not str(url).lower().startswith("http") and re.search(r"[a-z0-9-]+\.[a-z]{2,}", str(url), re.I):
            url = "https://" + url
        return {
            "intent": "ui",
            "action": "close_pages",
            "early": True,
            "url": url,
            "reply": f"Ich schliesse die Seite: {url}",
        }

    # --- OS: Website / Datei / App / Musik (echte Windows-Aktionen) ---
    # Explizite URL oeffnen
    url = _url_in(raw)
    if url and re.search(r"\b(oeffne|öffne|üffne|ueffne|uffne|ofne|öfne|open|starte|besuche|geh(e)? (zu|auf)|mach auf)\b", low):
        return {"intent": "os_open_url", "early": True, "url": url}
    m = re.match(
        r"^(?:oeffne|öffne|üffne|ueffne|uffne|ofne|öfne|open|mach\s+auf)\s+(?:(die\s+)?(website|seite|url)\s+)?(.+)$",
        raw,
        re.I,
    )
    if m and not re.search(r"\b(projekt|apps|videos|downloads|streams|leistung)\b", low):
        target = (m.group(3) or "").strip().strip('"\'')
        # Domain-ähnlich?
        if re.search(r"[a-z0-9-]+\.[a-z]{2,}(/\S*)?$", target, re.I) or target.lower().startswith(("www.", "http")):
            if not target.lower().startswith("http"):
                target = "https://" + target
            return {"intent": "os_open_url", "early": True, "url": target}

    # Datei / Ordner oeffnen
    m = re.match(
        r"^(?:oeffne|öffne|üffne|ueffne|uffne|ofne|öfne|open|mach\s+auf)\s+(?:(die\s+|den\s+|das\s+)?(datei|ordner|verzeichnis|folder|file)\s+)?(.+)$",
        raw,
        re.I,
    )
    if m:
        rest = (m.group(3) or "").strip().strip('"\'')
        kind = (m.group(2) or "").lower()
        # Windows-Pfad oder klarer Dateiname
        if re.search(r"^[A-Za-z]:\\|^\\\\|[/\\]", rest) or kind in ("datei", "ordner", "verzeichnis", "folder", "file"):
            return {
                "intent": "os_open_path",
                "early": True,
                "path": rest,
                "reveal": kind in ("ordner", "verzeichnis", "folder"),
            }

    # App starten / oeffnen
    m = re.match(
        r"^(?:starte|start|launch|oeffne|öffne|üffne|ueffne|uffne|ofne|öfne|open|mach\s+auf)\s+(?:(die\s+|den\s+|das\s+)?(app|anwendung|programm)\s+)?(.+)$",
        raw,
        re.I,
    )
    if m:
        app = _norm_app_name((m.group(3) or "").strip().strip("\"'"))
        app_l = app.lower()
        # Keine UI-Panels / generischen Phrasen
        skip = ("apps", "videos", "downloads", "streams", "leistung", "projekt", "chat", "website", "seite")
        if app_l and app_l not in skip and not re.search(r"[a-z0-9-]+\.[a-z]{2,}", app_l):
            # Pfad? → open_path
            if re.search(r"^[A-Za-z]:\\|\.exe$|\.lnk$", app, re.I):
                return {"intent": "os_open_path", "early": True, "path": app, "reveal": False}
            known_apps = (
                "chrome", "edge", "firefox", "brave", "notepad", "rechner", "calc", "explorer",
                "spotify", "vlc", "discord", "steam", "whatsapp", "telegram", "code", "vscode",
                "cursor", "fotos", "photos", "wmplayer", "musik", "youtube", "gmail", "kalender",
                "drive", "maps", "facebook", "tiktok", "cmd", "powershell", "einstellungen",
            )
            # Kurzer App-Name oder bekannt
            if app_l in known_apps or len(app.split()) <= 3:
                return {"intent": "os_launch_app", "early": True, "name": app}

    # Musik / Media abspielen
    if re.search(r"\b(spiel(e)?\s+musik|musik\s+abspielen|play\s+music|starte\s+musik)\b", low):
        return {"intent": "os_play_media", "early": True, "path": None}
    m = re.match(
        r"^(?:spiel(e)?|play|oeffne|öffne)\s+(?:(die\s+)?(musik|song|lied|audio|mp3|track)\s+)?(.+\.(?:mp3|wav|flac|m4a|aac|ogg|wma|opus|mp4|mkv|webm))\s*$",
        raw,
        re.I,
    )
    if m:
        return {"intent": "os_play_media", "early": True, "path": m.group(4).strip().strip('"\'')}

    # Explorer zu bekanntem Ort
    if re.search(r"\b(oeffne|öffne|open)\s+(explorer|dateien|ordner)\b", low) and len(low) < 40:
        return {"intent": "os_launch_app", "early": True, "name": "explorer"}

    # Open project
    m = re.match(r"^(öffne\s*projekt|oeffne\s*projekt|open\s*project)\s*[:\-]?\s*(.+)$", raw, re.I)
    if m:
        return {"intent": "project_open", "early": True, "name": m.group(2).strip()}

    # Direct PowerShell / Shell (echte Ausführung)
    m = re.match(r"^(?:powershell\s*:|ps>|shell\s*:)\s*(.+)$", raw, re.I | re.S)
    if m:
        prefix = raw.lstrip()[:12].lower()
        sh = "powershell" if prefix.startswith(("powershell", "ps>")) else "auto"
        return {
            "intent": "shell",
            "early": True,
            "action": "run",
            "command": m.group(1).strip(),
            "shell": sh,
        }
    if re.search(r"\b(f(?:ü|ue)hre\s+powershell|powershell\s+(ausf(?:ü|ue)hren|run))\b", low):
        # Rest nach Schlüsselwort als Befehl versuchen
        m2 = re.search(
            r"(?:f(?:ü|ue)hre\s+powershell|powershell)\s*(?:aus[:\s]+|:\s*|\s+)(.+)$",
            raw,
            re.I | re.S,
        )
        cmd = (m2.group(1).strip() if m2 else "")
        if cmd:
            return {"intent": "shell", "early": True, "action": "run", "command": cmd, "shell": "powershell"}
        return {"intent": "shell", "early": False, "force_tools": True}

    # Build / implement — Agent-Loop mit echten Tools
    if any(
        k in low
        for k in (
            "baue ",
            "build ",
            "implementiere",
            "patch",
            "lege an",
            "erstelle modul",
            "baue modul",
            "lege vite",
            "npm create",
            "baue shader",
        )
    ) or low.startswith(("baue", "bauen", "build", "implementiere", "patch", "fix", "selbstbau", "ausführungsbefehl", "ausfuehrungsbefehl")):
        out = {"intent": "build", "early": False, "force_tools": True, "timeout_hint": 600}
        sh, note = _shader_script_hint(low)
        if sh:
            out["script_hint"] = sh
        if note:
            out["script_note"] = note
        return out

    # Shell / Skript intent (ohne Prefix)
    if any(
        k in low
        for k in (
            "führe powershell",
            "fuehre powershell",
            "powershell ausführen",
            "powershell ausfuehren",
            "shell befehl",
            "führe shell",
            "fuehre shell",
            "führe skript",
            "fuehre skript",
            "run script",
            "skript ausführen",
            "skript ausfuehren",
        )
    ):
        return {"intent": "shell", "early": False, "force_tools": True, "timeout_hint": 600}

    # Code hint
    if low.startswith("```") or low.startswith("führe aus") or low.startswith("fuehre aus") or low.startswith("run python"):
        return {"intent": "code", "early": False}

    # Task / plan
    if any(k in low for k in ("plane ", "erstelle task", "todo:", "aufgabe:")):
        return {"intent": "task", "early": False}

    # File ops (Sandbox workspace/files + OS-Liste)
    if any(k in low for k in ("lies datei", "schreib datei", "liste dateien", "zeige dateien", "liste ordner", "zeig ordner")):
        mpath = re.search(r"([A-Za-z]:\\.+)", raw)
        if not mpath:
            mpath = re.search(r"(/(?:tmp|home|var|mnt|Users)/\S*)", raw)
        if mpath and ("liste" in low or "zeig" in low):
            return {"intent": "os_list_dir", "early": True, "path": mpath.group(1).strip()}
        return {"intent": "file", "early": False}


    # Status / Monitor summary
    if (
        re.search(r"\b(status|systemstatus|bist du online|health|systemübersicht|systemuebersicht)\b", low)
        and (len(low) < 50
             # t329u: laengere, explizite Pruefauftraege ("Pruefe Core und Status ...") echt ausfuehren
             or (len(low) < 200
                 and re.search(r"\b(pr\u00fcf\w*|pruef\w*|check\w*|zeig\w*|nenn\w*|melde\w*)\b", low)
                 and not re.search(r"\b(bau\w*|\u00e4nder\w*|aender\w*|implementier\w*|patch\w*|erstell\w*|schreib\w*)\b", low)))
        and not re.search(r"\b(diagnostiziere|diagnostizieren|diagnose|diagnostik|kaputt|beheb|reparier)\b", low)
    ):
        return {"intent": "status", "early": True, "reply": None}  # engine fills

    # Explizite Diagnose-Anfrage
    if re.search(
        r"\b(diagnostizier(?:e|en)?|diagnose|diagnostik|systemdiagnose|"
        r"prüfe nexus|pruefe nexus|check nexus)\b",
        low,
    ):
        return {"intent": "diagnose", "early": True, "query": raw, "reply": None}

    # Vage Reparatur-Klagen → schnelle Diagnose (kein 90s-Agent-Hang)
    if any(
        w in low
        for w in (
            "funktioniert nicht",
            "funktionieren nicht",
            "geht nicht",
            "warum antwortet",
            "warum führt",
            "warum fuehrt",
            "kaputt",
            "broken",
            "nichts geht",
            "meisten funktionen",
            "diagnostiziere",
            "diagnostizieren",
            "diagnose",
            "diagnostik",
        )
    ) and not re.search(r"\b(baue|bauen|build|implementiere|fix|fixen|selbstbau|ausführungsbefehl|ausfuehrungsbefehl|powershell:|ps>|shell:)\b", low):
        return {"intent": "diagnose", "early": True, "query": raw, "reply": None}

    # Konkrete Reparatur/Ausführung → Agent mit Tools
    if any(
        w in low
        for w in (
            "behebe",
            "reparier",
            "fix das",
            "fix nexus",
            "führ den befehl",
            "fuehr den befehl",
            "führe aus",
            "fuehre aus",
        )
    ) or re.search(r"\b(beheb|reparier|debug|fehler (beheben|fixen))\b", low):
        # Wenn nur „behebe das" ohne Ziel → diagnose statt Hang
        if re.search(r"\b(behebe|reparier)\b", low) and len(low) < 60 and not re.search(
            r"\b(datei|modul|monitor|shell|powershell|server|ui|stimme|tts|api)\b", low
        ):
            return {"intent": "diagnose", "early": True, "query": raw, "reply": None}
        return {"intent": "build", "early": False, "force_tools": True}


    # Offene Welt-/News-Fragen ohne Tool: ehrlich, kein LLM-Fluff / keine erfundenen News
    if re.search(
        r"\b("
        r"was\s+ist\s+(heute\s+)?neu|"
        r"was\s+gibt\s+es\s+(heute\s+)?neues|"
        r"was\s+passier(?:t|te)\s+(gerade\s+)?(?:heute\s+)?(?:auf|in)\s+der\s+welt|"
        r"was\s+passier(?:t|te)\s+(gerade\s+)?heute|"
        r"was\s+geht\s+ab\s+(in\s+der\s+welt)|"
        r"nachrichten|"
        r"weltnachrichten|"
        r"weltgeschehen|"
        r"aktuell(?:e|es)?\s+(news|nachrichten|welt)|"
        r"(?:news|trends?)\s+(heute|aktuell)|"
        r"trending|"
        r"breaking\s+news|"
        r"erste\s+anzeichen\s+(?:von\s+)?nexus|"
        r"nexus\s+in\s+(?:der\s+)?realit(?:ä|ae)t|"
        r"exoplanet(?:en)?\s+202\d|"
        r"knowledge\s+cutoff|"
        r"mein\s+wissen\s+(endet|reicht)|"
        r"neu(?:es)?\s+in\s+der\s+welt"
        r")\b",
        low,
    ):
        # Explizite Suche mit Thema → echte Research; sonst ehrliche Kurzantwort
        m_topic = re.match(
            r"^(?:suche|search|recherchiere|google)\s*[:\-]?\s*(.+)$",
            raw,
            re.I | re.S,
        )
        if m_topic:
            return {"intent": "research", "early": False, "query": m_topic.group(1).strip()}
        return {"intent": "research", "early": False, "query": (raw.strip() or "Weltnachrichten heute"), "live_web": True}

    return {"intent": "chat", "early": False}


_WEB_CUE = re.compile(
    r"\b(?:im|ins|aus\s+dem|per|über|ueber)\s+(?:netzt?|internet|web)\b|\bonline\b|\bweb-?suche\b|\bgoogle\w*\b|\bergoogle\w*\b|\b(?:link|url|video)s?\b.{0,40}\byoutube\b|\byoutube\b.{0,40}\b(?:link|url|video)s?\b",
    re.I)
_WEB_VERB = re.compile(r"\b(?:schau\w*|guck\w*|such\w*|find\w*|recherch\w*|nachseh\w*|nachschau\w*|check\w*|prüf\w*|pruef\w*|look|search|google\w*|ergoogle\w*|informier\w*|was\s+gibt|zeig\w*|gib|such)\b", re.I)
_WEB_STRIP = re.compile(
    r"^\s*(?:hey\s+|hallo\s+)?(?:nexus\s*[,:]?\s*)?(?:bitte\s+|kannst\s+du\s+|könntest\s+du\s+|koenntest\s+du\s+|du\s+sollst\s+|du\s+solltest\s+)?",
    re.I)


def web_lookup_query(raw):
    """Liefert die Suchanfrage, wenn der Text eine Web-/Internet-Recherche verlangt, sonst None."""
    t = str(raw or "").strip()
    if not t or len(t) > 400 or not _WEB_CUE.search(t) or not _WEB_VERB.search(t):
        return None
    if re.search(r"\b(?:erstell\w*|schreib\w*|speicher\w*|bau\w*|programmier\w*|install\w*|lösch\w*|loesch\w*)\b", t, re.I):
        return None  # Bau-/Schreibauftraege bleiben bei ihren Vertraegen
    q = _WEB_STRIP.sub("", t)
    q = re.sub(r"\b(?:schau|guck|such|find|recherchier|check)\w*\b|\b(?:mal|bitte|doch|kurz|nach|nachsehen|nachschauen|was)\b", " ", q, flags=re.I)
    q = re.sub(r"\b(?:zeig\w*|gib)\s+(?:mir\s+)?(?:(?:ein|einen|eine)\s+)?(?:link|url)s?\s+(?:f(?:ü|ue)r|zu)\s+", " ", q, flags=re.I)
    q = re.sub(r"\b(?:im|ins|aus\s+dem|per|über|ueber)\s+(?:netzt?|internet|web)\b|\bonline\b|\bweb-?suche\b|\bsollte\b|\bsoll\b|\bnexus\b", " ", q, flags=re.I)
    q = re.sub(r"^\s*(?:dem|den|der|die|das|zu|zum|zur|über|ueber|nach)\s+", "", re.sub(r"\s+", " ", q).strip(" ,.:;!?"), flags=re.I)
    q = re.sub(r"\s+", " ", q).strip(" ,.:;!?")
    q = re.sub(r"^(?:es|gibt\s+es)\s+|\s+gibt$", "", q, flags=re.I).strip()
    return q if len(q) >= 3 else None

_AUTO_LIVE = re.compile(r"\b(?:heute|neu(?:es|e|en|igkeiten)?|aktuell\w*|news|nachrichten|recherch\w*)\b", re.I)
_AUTO_CODE = re.compile(r"\b(?:schreib\w*|programmier\w*|erstell\w*|bau\w*|code|coden)\b.{0,60}\b(?:skript|script|code|programm|funktion|klasse|python|javascript|html|app)\b", re.I | re.S)
_AUTO_FIX = re.compile(r"\b(?:behebe?|beheb\w*|fix\w*|reparier\w*)\b.{0,40}\bfehler\b|\bfehler\b.{0,40}\b(?:beheb\w*|fix\w*)\b", re.I | re.S)


def decide(message: str) -> dict:
    """Routing: @-Erwaehnung hat Vorrang (Basis-Router); ohne @ automatisch an Scout/Coder/Fixer."""
    d = _decide_base(message)
    raw = strip_wake(strip_answer_policy(message or "")).strip()
    if raw.startswith("@") or d.get("intent") == "crew":
        return d
    low = raw.lower()
    if d.get("intent") == "diagnose" and _AUTO_FIX.search(low):
        return {"intent": "crew", "early": False, "force_tools": True, "agent_id": "fixer", "query": raw, "auto_routed": True}
    if d.get("intent") != "chat":
        return d
    if _AUTO_LIVE.search(low) and not re.search(r"\b(?:uhr|datum|wetter)\b", low):
        return {"intent": "crew", "early": False, "force_tools": True, "agent_id": "scout", "query": raw, "auto_routed": True}
    if _AUTO_CODE.search(low):
        return {"intent": "crew", "early": False, "force_tools": True, "agent_id": "coder", "query": raw, "auto_routed": True}
    return d
