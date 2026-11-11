from __future__ import annotations
import re
import json
import os
import sys
import threading
from datetime import datetime
from typing import Any, Iterator
from nexus.common.config import Config
from nexus.common import ai_layer
from nexus.common.atomic import atomic_write_json, file_lock
from nexus.common.paths import CHAT_DIR, ensure_dirs
from nexus.master.controller import MasterController

HISTORY_FILE = CHAT_DIR / "conversations.json"
ACTIVE_FILE = CHAT_DIR / "active_session.json"
# 2026-10-29: System-/Health-Tests (nexus_os.run_chat_e2e) laufen isoliert, nie im aktiven Eddie-Chat.
TEST_SESSION_PREFIX = "NEXUS-OS-E2E"
IDENTITY_FIELDS = ("source", "agent", "partner", "mode", "provider", "response_id")


def response_identity(result: dict, default_partner: str = "nexus") -> dict:
    """Use server response provenance, rather than the selected UI card, as speaker."""
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    identity = {key: result.get(key) or data.get(key) for key in IDENTITY_FIELDS
                if result.get(key) or data.get(key)}
    source = str(identity.get("source") or "").lower()
    agent = str(identity.get("agent") or "").lower()
    partner = str(identity.get("partner") or "").lower()
    provider = str(identity.get("provider") or "").lower()
    if source == "hermes" or source.startswith("hermes_brain") or agent == "hermes":
        partner = "hermes"
    elif source == "aether" or agent == "aether":
        partner = "aether"
    elif source == "openai_orchestrator" or identity.get("mode") == "cloud/openai" or provider == "openai":
        partner = "gpt"
    elif partner not in ("nexus", "hermes", "gpt", "aether"):
        partner = default_partner
    identity["partner"] = partner
    return identity


_CLAIM_RE = re.compile(r"task|NX-|\d{5,}|gestartet|erledigt|ausgef(?:ü|ue)hrt|erstellt|gespeichert|gel(?:ö|oe)scht|installiert|gesendet|geschickt|ge(?:ö|oe)ffnet", re.I)

class NexusEngine:
    def __init__(self) -> None:
        ensure_dirs()
        self._chat_lock = threading.RLock()
        self._save_lock = threading.RLock()
        self.version = Config.VERSION
        self.master = MasterController()
        self.sessions: dict[str, dict] = self._load_sessions()
        self.session_id = self._load_active() or self._new_session_id()
        if self.session_id not in self.sessions:
            self.sessions[self.session_id] = self._empty_session(self.session_id)
        self._save_active(self.session_id)
        self._save_sessions()

    def _now(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _new_session_id(self) -> str:
        return "CHAT-" + datetime.now().strftime("%Y%m%d%H%M%S%f")

    def _empty_session(self, sid: str) -> dict:
        return {"id": sid, "title": "Neuer Chat", "created": self._now(), "updated": self._now(), "messages": []}

    def _load_sessions(self) -> dict:
        if not HISTORY_FILE.exists():
            return {}
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8")).get("sessions", {})
        except Exception as exc:
            raise RuntimeError("CHAT_HISTORY_UNREADABLE") from exc

    def _save_sessions(self) -> None:
        # Auch aus Threads ohne _chat_lock aufgerufen (Umbenennen, Löschen, Auftragsfortschritt):
        # eigene Sperre für Serialisierung + Schreiben, eindeutige tmp-Datei, atomarer Austausch.
        with self._save_lock, file_lock(HISTORY_FILE):
            atomic_write_json(HISTORY_FILE, {"version": self.version, "updated": self._now(),
                                             "sessions": self.sessions})

    def _load_active(self) -> str | None:
        if not ACTIVE_FILE.exists():
            return None
        try:
            return json.loads(ACTIVE_FILE.read_text(encoding="utf-8")).get("session_id")
        except Exception:
            return None

    def _save_active(self, sid: str) -> None:
        atomic_write_json(ACTIVE_FILE, {"session_id": sid, "updated": self._now()})


    @staticmethod
    def _selfbuild_state() -> str:
        """Echter Selbstbau-Zustand aus der Autobuild-Engine; UNKNOWN nur bei Fehler."""
        try:
            from nexus.autobuild.loop import get_autobuild
            st = get_autobuild().status()
            if st.get("cycle_running"):
                return "RUNNING"
            return "ACTIVE" if st.get("loop") and not st.get("stop_file") else "STOPPED"
        except Exception:
            return "UNKNOWN"

    def health(self) -> dict:
        ollama = self.master.llm.ping()
        model = self.master.llm.pick_model() if ollama.get("reachable") else None
        modules = self.master.modules_map()
        score = 40
        if ollama.get("reachable"):
            score += 40
        if model:
            score += 15
        score += min(5, sum(1 for v in modules.values() if v))
        score = min(100, score)
        ai = ai_layer.status(self.master.llm)
        return {
            "success": True,
            "ok": True,
            "score": score,
            "version": self.version,
            "port": Config.PORT,
            "host": Config.HOST,
            "ollama": {
                "reachable": ollama.get("reachable"),
                "models": ollama.get("models", []),
                "model": model,
                "error": ollama.get("error"),
                "keep_alive": ai.get("keep_alive"),
                "loaded_in_vram": ai.get("loaded_in_vram"),
            },
            "ai": ai,
            "modules": modules,
            "python": {"executable": sys.executable, "version": sys.version.split()[0]},
            "selfbuild": self._selfbuild_state(),
            "score_kind": "service_availability_only_not_execution_proof",
        }

    def status(self) -> dict:
        h = self.health()
        return {
            "success": True,
            "session_id": self.session_id,
            "project_id": self.master.projects.active_id,
            "media": self.master.media.state(),
            "streams": self.master.streams.list(),
            "system": self.master.system.status(),
            "health": h,
            "voice": self.master.voice.status(),
            "apps": self.master.apps.list(),
        }

    def list_sessions(self) -> list:
        return [
            {"id": s["id"], "title": s.get("title"), "updated": s.get("updated"), "count": len(s.get("messages", []))}
            for s in sorted(self.sessions.values(), key=lambda x: x.get("updated", ""), reverse=True)
        ]

    def get_session(self, sid: str | None = None) -> dict:
        sid = sid or self.session_id
        return self.sessions.get(sid) or self._empty_session(sid)

    def new_session(self, title: str = "Neuer Chat") -> dict:
        sid = self._new_session_id()
        with self._save_lock:
            self.sessions[sid] = self._empty_session(sid)
            self.sessions[sid]["title"] = title or "Neuer Chat"
        self.session_id = sid
        self._save_active(sid)
        self._save_sessions()
        return {"success": True, "session": self.sessions[sid]}

    def rename_session(self, sid: str, title: str) -> dict:
        if sid not in self.sessions:
            return {"success": False, "error": "not_found"}
        self.sessions[sid]["title"] = (title or "Chat").strip()
        self.sessions[sid]["updated"] = self._now()
        self._save_sessions()
        return {"success": True, "session": self.sessions[sid]}

    def delete_session(self, sid: str) -> dict:
        if sid not in self.sessions:
            return {"success": False, "error": "not_found"}
        with self._save_lock:
            del self.sessions[sid]
        if self.session_id == sid:
            if self.sessions:
                self.session_id = next(iter(sorted(self.sessions.keys(), reverse=True)))
            else:
                return self.new_session()
            self._save_active(self.session_id)
        self._save_sessions()
        return {"success": True, "active": self.session_id}

    def switch_session(self, sid: str) -> dict:
        if sid not in self.sessions:
            return {"success": False, "error": "not_found"}
        self.session_id = sid
        self._save_active(sid)
        return {"success": True, "session": self.sessions[sid]}

    def export_session(self, sid: str | None = None) -> dict:
        s = self.get_session(sid)
        return {"success": True, "session": s}

    def summarize_session(self, sid: str | None = None) -> dict:
        s = self.get_session(sid)
        msgs = s.get("messages", [])
        text = "\n".join(f"{m.get('role')}: {m.get('content','')[:200]}" for m in msgs[-20:])
        if not text:
            return {"success": True, "summary": "Leerer Chat."}
        llm = self.master.llm.chat([
            {"role": "system", "content": "Fasse den Chat kurz auf Deutsch zusammen."},
            {"role": "user", "content": text},
        ])
        return {"success": bool(llm.get("success")), "summary": llm.get("reply"), "error": llm.get("error")}

    def _drop_last_turn(self, session: dict) -> str:
        """2026-10-28 CHAT04: letzte Nutzerfrage + folgende Antworten entfernen (Neu generieren / Bearbeiten)."""
        msgs = session.get("messages") or []
        idx = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=None)
        if idx is None:
            return ""
        text = str(msgs[idx].get("content") or "")
        del msgs[idx:]
        return text

    def _prepare_session(self, message: str, session_id: str | None = None, project_id: str | None = None, rewrite: str | None = None) -> dict:
        is_test = bool(session_id) and str(session_id).startswith("NEXUS-OS-E2E")
        if session_id and session_id in self.sessions and not is_test:
            self.session_id = session_id
            self._save_active(session_id)
        if project_id:
            self.master.projects.set_active(project_id)
        if is_test:
            with self._save_lock:
                if session_id not in self.sessions:
                    _s = self._empty_session(session_id)
                    _s["title"] = "NEXUS-OS-E2E (Systemtest)"
                    self.sessions[session_id] = _s
                session = self.sessions[session_id]
                if len(session.get("messages") or []) > 40:
                    session["messages"] = session["messages"][-40:]
        else:
            session = self.sessions[self.session_id]
        if rewrite in ("regenerate", "edit"):
            prev = self._drop_last_turn(session)
            if rewrite == "regenerate" and not (message or "").strip():
                message = prev
        entry = {"role": "user", "content": message, "ts": self._now()}
        if rewrite in ("regenerate", "edit"):
            entry["rewrite"] = rewrite
        session["messages"].append(entry)
        if session.get("title") in (None, "", "Neuer Chat") and message.strip():
            session["title"] = message.strip()[:48]
        self._save_sessions()
        return session

    def _finalize(self, session: dict, reply: str, result: dict) -> dict:
        from nexus.master.evidence import guard_output
        identity = response_identity(result)
        _thinking = str(result.get("thinking") or "")
        result = guard_output(result)
        if _thinking and not result.get("thinking"):
            result = {**result, "thinking": _thinking}
        reply = result.get("reply") or ""
        self._append_response(session, result, identity)
        return {
            "success": result.get("success", True),
            "reply": reply,
            "session_id": session["id"],
            "intent": result.get("intent"),
            "plan": result.get("plan"),
            "model": result.get("model"),
            "error": result.get("error"),
            "data": result.get("data"),
            "thinking": result.get("thinking") or "",
            **identity,
        }

    def _append_response(self, session: dict, result: dict, identity: dict) -> None:
        session["messages"].append({
            "role": "assistant",
            "content": result.get("reply") or "",
            "ts": self._now(),
            "intent": result.get("intent"),
            "model": result.get("model"),
            "data": result.get("data"),
            "status": result.get("status") or (result.get("data") or {}).get("execution_status"),
            "success": result.get("success", True),
            "error": result.get("error"),
            **identity,
        })
        if result.get("thinking"):  # THINK-04: Denkprozess im Verlauf speichern (nach Reload aufklappbar)
            session["messages"][-1]["thinking"] = str(result.get("thinking"))
        session["updated"] = self._now()
        self._save_sessions()

    def record_partner_turn(self, message: str, result: dict, chat_session_id: str,
                            partner: str) -> dict:
        """Persist an explicitly selected NEXUS chat; provider session IDs stay separate."""
        with self._chat_lock, self._save_lock:
            if chat_session_id not in self.sessions:
                raise ValueError("CHAT_SESSION_NOT_FOUND")
            session = self.sessions[chat_session_id]
            session["messages"].append({"role": "user", "content": message, "ts": self._now()})
            if session.get("title") in (None, "", "Neuer Chat") and message.strip():
                session["title"] = message.strip()[:48]
            identity = response_identity(result, partner)
            self._append_response(session, result, identity)
            return {**result, **identity, "chat_session_id": chat_session_id, "chat_history_saved": True}

    def append_game_turn(self, chat_session_id: str, speaker: str, content: str,
                         partner: str = "nexus", role: str = "assistant", meta: dict | None = None) -> dict:
        """2026-11-04 HAUS5T: Spielbeitrag (echte Agentenantwort) sichtbar in eine Chat-Sitzung schreiben."""
        if role not in ("assistant", "system"):
            raise ValueError("ROLE_NOT_ALLOWED")
        with self._chat_lock, self._save_lock:
            if chat_session_id not in self.sessions:
                raise ValueError("CHAT_SESSION_NOT_FOUND")
            session = self.sessions[chat_session_id]
            p = partner if partner in ("nexus", "hermes", "gpt", "aether") else "nexus"
            msg = {"role": "assistant", "content": str(content or ""), "ts": self._now(), "intent": "game",
                   "source": "haus5t", "agent": str(speaker)[:40], "partner": p, "mode": "game",
                   "data": meta if isinstance(meta, dict) else {}, "success": True}
            session["messages"].append(msg)
            session["updated"] = self._now()
            self._save_sessions()
            return {"success": True, "chat_session_id": chat_session_id, "count": len(session["messages"])}

    def _chat_impl(self, message: str, session_id: str | None = None, project_id: str | None = None, model: str | None = None, source: str | None = None) -> dict:
        session = self._prepare_session(message, session_id, project_id)
        if source:
            session["request_source"] = str(source)
        ai_layer.apply_to_client(self.master.llm)
        use = ai_layer.resolve_chat_model(self.master.llm, override=model)
        if use:
            self.master.llm.model = use
        result = self._route_order(message, session, source=source)
        if result is None:
            result = self.master.handle(message, session["messages"][:-1])
        reply = result.get("reply") or ""
        return self._finalize(session, reply, result)

    def _chat_stream_impl(self, message: str, session_id: str | None = None, project_id: str | None = None, model: str | None = None, rewrite: str | None = None) -> Iterator[dict]:
        """Yield SSE-ready events: token then done (with session_id)."""
        session = self._prepare_session(message, session_id, project_id, rewrite)
        message = session["messages"][-1]["content"]
        ai_layer.apply_to_client(self.master.llm)
        use = ai_layer.resolve_chat_model(self.master.llm, override=model)
        if use:
            self.master.llm.model = use
        routed = self._route_order(message, session)
        if routed is not None:
            yield {"type": "done", **self._finalize(session, routed.get("reply", ""), routed)}
            return
        history = session["messages"][:-1]
        done_payload: dict[str, Any] | None = None
        _acc = ""
        _stream_hold = False
        _think_acc: list[str] = []
        try:
            for ev in self.master.handle_stream(message, history):
                if ev.get("type") == "thinking":  # THINK-02: Denkprozess separat, nie im Antworttext
                    _think_acc.append(ev.get("text") or "")
                    yield {"type": "thinking", "text": ev.get("text") or ""}
                    continue
                if ev.get("type") == "token":
                    # 2026-10-28 CHAT06: Tokens live weiterreichen; done.reply bleibt die validierte Endfassung
                    # Sobald der Text nach Erfolgs-/Task-Behauptung aussieht, nichts mehr live zeigen
                    # (unverifizierte Claims bleiben verborgen; done ersetzt ohnehin den Text).
                    txt = ev.get("text") or ""
                    _acc += txt
                    if txt and not _stream_hold and _CLAIM_RE.search(_acc):
                        _stream_hold = True
                    if txt and not _stream_hold:
                        yield {"type": "token", "text": txt}
                    continue
                elif ev.get("type") == "done":
                    done_payload = ev
        except Exception as e:
            done_payload = {
                "type": "done",
                "reply": f"Interner Fehler: {e}",
                "success": False,
                "error": str(e),
                "intent": "chat",
                "model": None,
            }
        if not done_payload:
            done_payload = {
                "type": "done",
                "reply": "Keine Antwort erzeugt.",
                "success": False,
                "error": "empty",
                "intent": "chat",
                "model": None,
            }
        if _think_acc and not done_payload.get("thinking"):
            done_payload["thinking"] = "".join(_think_acc).strip()
        reply = done_payload.get("reply") or ""
        finalized = self._finalize(session, reply, done_payload)
        yield {"type": "done", **finalized}

    def _route_order(self, message, session, source: str | None = None):
        from nexus.master.evidence import route_work
        self.master._persist_chat_order = self._save_sessions
        self.master._active_session_id = session.get("id") or self.session_id
        try:
            src = str(source or session.get("request_source") or "").lower()
            # 2026-11-09 FIXALL: Spiel/Probe nie in Hermes-Werkzeugvertraege ziehen.
            if src in ("haus5t", "konzil", "game", "probe") or (
                re.search(r"\b(rollenspiel|spielregeln|konzil der|haus der (?:fuenf|fünf) t(?:ue|ü)ren)\b", str(message or ""), re.I)
            ):
                return None
            from nexus.chat.collaboration import matches, collaborate
            if matches(message, session):
                return collaborate(self.master, message, session)
            return route_work(self.master, message, session["messages"][:-1], session)
        finally:
            del self.master._persist_chat_order

    def chat(self, message, session_id=None, project_id=None, model=None, source=None):
        with self._chat_lock:
            return self._chat_impl(message, session_id, project_id, model, source=source)

    def chat_stream(self, message, session_id=None, project_id=None, model=None, rewrite=None):
        with self._chat_lock:
            if rewrite:
                yield from self._chat_stream_impl(message, session_id, project_id, model, rewrite)
            else:
                yield from self._chat_stream_impl(message, session_id, project_id, model)
