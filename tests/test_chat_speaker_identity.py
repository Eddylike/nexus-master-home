"""Speaker provenance and persistence, without starting NEXUS or calling providers."""
from __future__ import annotations

import ast
import __future__
import base64
import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nexus.common.atomic import atomic_write_json, file_lock


def _definitions(path, names):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node for node in tree.body if (
        isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names
    ) or (isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id in names for target in node.targets))]


class ChatSpeakerIdentityTests(unittest.TestCase):
    def setUp(self):
        temp_root = ROOT / "workspace" / "hermes_arbeitsbereich" / "nexus-hermes-home" / "tmp"
        self.temp = tempfile.TemporaryDirectory(prefix="chat_identity_", dir=temp_root)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        source = ROOT / "nexus" / "chat" / "engine.py"
        self.scope = {"json": json, "threading": threading, "datetime": datetime,
                      "HISTORY_FILE": self.path / "conversations.json",
                      "ACTIVE_FILE": self.path / "active.json",
                      "atomic_write_json": atomic_write_json, "file_lock": file_lock,
                      "ai_layer": SimpleNamespace(apply_to_client=Mock(),
                                                  resolve_chat_model=Mock(return_value=None))}
        nodes = _definitions(source, {"IDENTITY_FIELDS", "response_identity", "NexusEngine"})
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec",
                     flags=__future__.annotations.compiler_flag), self.scope)
        engine_type = self.scope["NexusEngine"]
        self.engine = engine_type.__new__(engine_type)
        self.engine._chat_lock = threading.RLock()
        self.engine._save_lock = threading.RLock()
        self.engine.version = "speaker-test"
        self.engine.session_id = "CHAT-identity-test"
        self.engine.sessions = {self.engine.session_id: self.engine._empty_session(self.engine.session_id)}
        self.engine.master = SimpleNamespace(llm=SimpleNamespace())
        self.engine._route_order = Mock(return_value=None)
        evidence = ModuleType("nexus.master.evidence")
        evidence.guard_output = lambda result: dict(result)
        self.modules = patch.dict(sys.modules, {"nexus.master.evidence": evidence})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        server_source = ROOT / "nexus" / "chat" / "server.py"
        self.handler_scope = {**self.scope, "BaseHTTPRequestHandler": BaseHTTPRequestHandler, "Path": Path,
                              "ENGINE": self.engine, "traceback": SimpleNamespace(print_exc=Mock()),
                              "bridge_api": None, "automation_api": None, "multimodal_api": None}
        exec(compile(ast.Module(body=_definitions(server_source, {"_ClientDisconnected", "Handler"}),
                                type_ignores=[]), str(server_source), "exec"), self.handler_scope)
        self.handler = self.handler_scope["Handler"].__new__(self.handler_scope["Handler"])
        self.handler._json = Mock()

    def _hermes(self):
        return {"success": True, "reply": "HERMES antwortet.", "source": "hermes", "agent": "hermes",
                "partner": "nexus", "provider": "ollama-qwen35", "model": "qwen35-4b-64k",
                "session_id": "hermes-cli-session"}

    def test_backend_provenance_wins_over_selected_partner(self):
        identity = self.scope["response_identity"]
        cases = [(self._hermes(), "hermes"),
                 ({"data": {"source": "hermes", "agent": "hermes"}}, "hermes"),
                 ({"source": "hermes_brain_api", "partner": "gpt"}, "hermes"),
                 ({"source": "aether", "partner": "nexus", "provider": "ollama"}, "aether"),
                 ({"mode": "cloud/openai", "partner": "aether"}, "gpt"),
                 ({"data": {"provider": "openai", "response_id": "resp_test"}}, "gpt"),
                 ({"partner": "aether", "provider": "ollama"}, "aether"),
                 ({"reply": "NEXUS antwortet."}, "nexus")]
        for result, expected in cases:
            with self.subTest(result=result):
                self.assertEqual(identity(result)["partner"], expected)

    def test_finalized_identity_survives_disk_reload(self):
        session = self.engine.sessions[self.engine.session_id]
        result = self._hermes()
        out = self.engine._finalize(session, result["reply"], result)
        saved = self.engine._load_sessions()[session["id"]]["messages"][-1]
        for value in (out, saved):
            self.assertEqual(value["partner"], "hermes")
            self.assertEqual(value["source"], "hermes")
            self.assertEqual(value["agent"], "hermes")
            self.assertEqual(value["provider"], "ollama-qwen35")

    def test_guard_failure_keeps_origin_without_success(self):
        evidence = sys.modules["nexus.master.evidence"]
        evidence.guard_output = lambda result: {"success": False, "error": "UNVERIFIED_MODEL_CLAIM",
                                               "reply": "Behauptung blockiert."}
        session = self.engine.sessions[self.engine.session_id]
        out = self.engine._finalize(session, "ignored", self._hermes())
        self.assertEqual(out["partner"], "hermes")
        self.assertFalse(out["success"])
        self.assertEqual(out["error"], "UNVERIFIED_MODEL_CLAIM")

    def test_engine_and_http_sse_preserve_cloud_provenance(self):
        result = {"type": "done", "success": True, "reply": "Analyse.", "mode": "cloud/openai",
                  "source": "openai_orchestrator", "provider": "openai", "response_id": "resp_test"}
        self.engine.master.handle_stream = Mock(return_value=iter([result]))
        self.handler._sse_start = Mock()
        self.handler._sse_event = Mock()
        self.handler._chat_stream({"message": "Analysiere.", "session_id": self.engine.session_id})
        events = [call.args[0] for call in self.handler._sse_event.call_args_list]
        self.assertEqual(events[-1], "[DONE]")
        done = events[0]
        self.assertEqual(done["partner"], "gpt")
        for key in ("mode", "source", "provider", "response_id"):
            self.assertEqual(done[key], result[key])
        saved = self.engine._load_sessions()[self.engine.session_id]["messages"][-1]
        self.assertEqual(saved["response_id"], "resp_test")

    def test_direct_turn_does_not_use_hermes_cli_session_as_chat_id(self):
        out = self.handler._partner_response({"message": "Hallo", "chat_session_id": self.engine.session_id},
                                             self._hermes(), "hermes")
        self.assertEqual(out["session_id"], "hermes-cli-session")
        self.assertEqual(out["chat_session_id"], self.engine.session_id)
        self.assertTrue(out["chat_history_saved"])
        self.assertEqual(self.engine.session_id, "CHAT-identity-test")
        saved = self.engine._load_sessions()[self.engine.session_id]["messages"]
        self.assertEqual([item["role"] for item in saved], ["user", "assistant"])
        self.assertEqual(saved[-1]["partner"], "hermes")

    def test_direct_routes_without_explicit_chat_id_do_not_change_history(self):
        out = self.handler._partner_response({"message": "Hallo", "session_id": "hermes-cli-session"},
                                             self._hermes(), "hermes")
        self.assertEqual(out["partner"], "hermes")
        self.assertEqual(self.engine.sessions[self.engine.session_id]["messages"], [])
        self.assertFalse(self.scope["HISTORY_FILE"].exists())

    def test_direct_http_routes_persist_each_actual_provider_once(self):
        integrations = ModuleType("nexus.integrations")
        allmode = SimpleNamespace(match_command=lambda message: None,
                                  status=lambda: {"state": "IDLE"})
        integrations.hermes_allmode = allmode
        modules = {"nexus.integrations": integrations}
        cases = [("/api/hermes/agent", "nexus.integrations.hermes_agent", "ask", {}, self._hermes(), "hermes"),
                 ("/api/aether/agent", "nexus.integrations.aether_agent", "ask", {},
                  {"success": True, "reply": "AETHER antwortet.", "partner": "aether",
                   "provider": "ollama", "model": "qwen35-4b-64k:latest"}, "aether"),
                 ("/api/chat", "nexus.master.openai_orchestrator", "orchestrate", {"mode": "cloud"},
                  {"success": True, "reply": "GPT analysiert.", "source": "openai_orchestrator",
                   "mode": "cloud/openai", "provider": "openai", "response_id": "resp_test"}, "gpt")]
        for path, name, function, extra, result, expected in cases:
            with self.subTest(partner=expected):
                provider = ModuleType(name)
                call = Mock(return_value=result)
                setattr(provider, function, call)
                self.handler._json.reset_mock()
                with patch.dict(sys.modules, {**modules, name: provider}):
                    self.handler._do_post_dispatch(path, {"message": "Hallo", "partner": "spoofed",
                                                        "chat_session_id": self.engine.session_id, **extra})
                call.assert_called_once_with("Hallo")
                status, out = self.handler._json.call_args.args
                self.assertEqual(status, 200)
                self.assertEqual(out["partner"], expected)
                self.assertTrue(out["chat_history_saved"])
                saved = self.engine._load_sessions()[self.engine.session_id]["messages"][-1]
                self.assertEqual(saved["partner"], expected)
        self.handler_scope["traceback"].print_exc.assert_not_called()

    def test_invalid_explicit_chat_is_rejected_before_provider_import(self):
        for path, body in [("/api/hermes/agent", {}), ("/api/aether/agent", {}),
                           ("/api/chat", {"mode": "cloud"}), ("/api/chat", {"mode": " cloud "})]:
            for invalid in ("hermes-cli-session", None, []):
                with self.subTest(path=path, invalid=invalid):
                    self.handler._json.reset_mock()
                    self.handler._do_post_dispatch(path, {**body, "chat_session_id": invalid})
                    self.handler._json.assert_called_once_with(
                        404, {"success": False, "error": "CHAT_SESSION_NOT_FOUND"})
        self.handler_scope["traceback"].print_exc.assert_not_called()

    def test_agent_routes_reject_non_loopback_before_provider(self):
        self.handler.client_address = ("192.168.1.50", 1234)
        self.handler.headers = {}
        self.handler.path = "/api/aether/agent"
        for path in ("/api/aether/agent", "/api/aether/supervisor", "/api/hermes/agent",
                     "/api/hermes/brain", "/api/gpt/agent", "/api/gpt/tool"):
            with self.subTest(path=path):
                self.handler._json.reset_mock()
                self.handler._do_post_dispatch(path, {"message": "Hallo"})
                self.handler._json.assert_called_once_with(
                    403, {"success": False, "error": "LOOPBACK_OR_TOKEN_REQUIRED"})

    def test_direct_aether_failure_remains_separate_and_failed(self):
        result = {"partner": "aether", "provider": "ollama", "model": "qwen35-4b-64k:latest",
                  "success": False, "reply": "", "error": "AETHER_UNAVAILABLE"}
        out = self.handler._partner_response({"message": "Hallo", "chat_session_id": self.engine.session_id},
                                             result, "aether")
        self.assertEqual(out["partner"], "aether")
        self.assertFalse(out["success"])
        saved = self.engine._load_sessions()[self.engine.session_id]["messages"][-1]
        self.assertFalse(saved["success"])
        self.assertEqual(saved["error"], "AETHER_UNAVAILABLE")

    def _voice_modules(self):
        voice = ModuleType("nexus.voice")
        voice.stt = SimpleNamespace(transcribe_bytes=Mock(side_effect=AssertionError("unexpected STT")))
        commands = ModuleType("nexus.voice.commands")
        commands.voice_priv_block = lambda text: None
        return {"nexus.voice": voice, "nexus.voice.commands": commands}

    def test_master_voice_turn_keeps_identity_and_actual_default_voice_metadata(self):
        result = {**self._hermes(), "data": {"execution_status": "HERMES_ANSWERED", "source": "hermes"}}
        self.engine.chat = Mock(return_value=result)
        audio_path = self.path / "mock_tts.mp3"
        audio_path.write_bytes(b"mock-audio-bytes")
        tts = Mock(return_value={"success": True, "path": str(audio_path),
                                 "voice": "de-DE-AmalaNeural", "mode": "plain", "rate": "-4%",
                                 "pitch": "-2Hz", "volume": "+0%"})
        self.engine.master.voice = SimpleNamespace(tts=tts)
        with patch.dict(sys.modules, self._voice_modules()):
            self.handler._do_post_dispatch("/api/voice/turn", {"message": "Hallo", "speak": True})
        status, out = self.handler._json.call_args.args
        self.assertEqual(status, 200)
        self.assertEqual(out["partner"], "hermes")
        for key in ("source", "agent", "provider", "data"):
            self.assertEqual(out[key], result[key])
        self.assertEqual(base64.b64decode(out["tts_b64"]), audio_path.read_bytes())
        self.assertEqual(out["tts_voice"], "de-DE-AmalaNeural")
        self.assertEqual(out["tts_profile"], "configured_default")
        self.assertEqual(out["tts_mode"], "plain")
        self.assertEqual(out["tts_rate"], "-4%")
        self.assertEqual(out["tts_pitch"], "-2Hz")
        self.assertEqual(out["tts_volume"], "+0%")
        tts.assert_called_once_with(result["reply"][:1800])
        self.handler_scope["traceback"].print_exc.assert_not_called()

    def test_master_voice_turn_without_speech_keeps_cloud_provenance(self):
        result = {"success": True, "reply": "Analyse.", "mode": "cloud/openai",
                  "source": "openai_orchestrator", "provider": "openai", "response_id": "resp_voice",
                  "data": {"provenance": "model_response_unverified"}}
        self.engine.chat = Mock(return_value=result)
        self.engine.master.voice = SimpleNamespace(tts=Mock())
        with patch.dict(sys.modules, self._voice_modules()):
            self.handler._do_post_dispatch("/api/voice/turn", {"message": "Hallo", "speak": False})
        status, out = self.handler._json.call_args.args
        self.assertEqual(status, 200)
        self.assertEqual(out["partner"], "gpt")
        for key in ("source", "mode", "provider", "response_id", "data"):
            self.assertEqual(out[key], result[key])
        self.engine.master.voice.tts.assert_not_called()
        self.assertNotIn("tts_b64", out)
        self.handler_scope["traceback"].print_exc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
