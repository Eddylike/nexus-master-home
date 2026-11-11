/* =========================================================
   adapter.js — Integrations-Adapter (Schnittstelle + Demo + SharedRoom-Stub)

   Diese Datei definiert die EINZIGE Stelle, an der später echte
   NEXUS-Ereignisse in die Chamber gelangen. Aktuell aktiv ist
   ausschließlich der DemoAdapter. Der SharedRoomAdapter / BridgeAdapter
   sind vorbereitete, NICHT verbundene Mapper.
   ========================================================= */
(function () {
  "use strict";
  const NEXUS = window.NEXUS;
  const U = NEXUS.util;

  // Kanonische Ereignisarten (Dokumentation der Schnittstelle)
  const KINDS = Object.freeze({
    SYSTEM: "system",
    MESSAGE: "message",
    PARTICIPANT: "participant",
    DISCUSSION: "discussion",
    DECISION: "decision",
    TASK: "task",
    EXECUTOR_RESULT: "executor_result",
    VERIFICATION: "verification",
    BLOCKER: "blocker"
  });

  // ---------------- Basis-Adapter ----------------
  class ConvergenceAdapter {
    constructor(opts = {}) {
      this.id = opts.id || U.uid("adapter");
      this.label = opts.label || "adapter";
      this.status = "disconnected"; // disconnected | connecting | live | demo | error
      this._listeners = new Set();
    }
    onEvent(cb) { this._listeners.add(cb); return () => this._listeners.delete(cb); }
    _dispatch(evt) {
      const e = Object.assign(
        { id: U.uid("ev"), ts: U.nowISO(), demo: true },
        evt
      );
      this._listeners.forEach((cb) => { try { cb(e); } catch (err) { console.error("[adapter]", err); } });
      return e;
    }
    connect() { this.status = "connecting"; }
    disconnect() { this.status = "disconnected"; }
  }

  // ---------------- Demo-Adapter ----------------
  // Erzeugt KEINE echten Systemereignisse. Alle Events demo:true.
  class DemoAdapter extends ConvergenceAdapter {
    constructor() {
      super({ id: "demo", label: "DEMO · lokal" });
      this.status = "demo";
    }
    connect() { this.status = "demo"; this._dispatch({ kind: KINDS.SYSTEM, code: "DEMO_ADAPTER_READY", message: "Demo-Adapter aktiv." }); }
    emit(evt) { return this._dispatch(Object.assign({ demo: true }, evt)); }
  }

  // ---------------- SharedRoom-Adapter (STUB, nicht verbunden) ----------------
  // Später: liest GET /api/shared-room + /api/shared-room/status und
  // POST /api/shared-room/message. Hier nur Mapping-Funktionen, KEIN fetch.
  class SharedRoomAdapter extends ConvergenceAdapter {
    constructor(opts = {}) {
      super({ id: "sharedroom", label: "SharedRoom (nicht verbunden)" });
      this.baseUrl = opts.baseUrl || "http://127.0.0.1:8299";
      this.session = opts.session || "convergence";
      this.status = "disconnected";
    }

    // NEXUS-SharedRoom-Sender -> kanonische Chamber-Rolle
    static roleOf(senderType) {
      switch ((senderType || "").toLowerCase()) {
        case "nexus": return "nexus";
        case "agent": return "hermes";       // Hermes-Bridge/Externer Agent
        case "system": return "system";
        case "local_ai": return "local_ai";
        case "chatgpt": return "chatgpt";
        case "human": return "human";
        default: return "unknown";
      }
    }

    // Mappt eine SharedRoom-Nachricht auf ein kanonisches Chamber-Event.
    static mapMessage(raw) {
      const from = SharedRoomAdapter.roleOf(raw.sender_type || raw.sender);
      return {
        kind: KINDS.MESSAGE,
        demo: false,
        id: raw.message_id || raw.id || null,
        ts: raw.timestamp || raw.ts || U.nowISO(),
        from,
        to: raw.target ? SharedRoomAdapter.roleOf(raw.target) : null,
        role: raw.role || "message",
        content: raw.content || raw.text || "",
        raw
      };
    }

    // Mappt einen SharedRoom-Status-Payload auf Teilnehmer-Events.
    static mapStatus(payload) {
      const parts = (payload && payload.participants) || [];
      return parts.map((p) => ({
        kind: KINDS.PARTICIPANT,
        demo: false,
        agent: SharedRoomAdapter.roleOf(p.sender_type || p.id || p.name),
        name: p.name || p.sender_name,
        status: p.status || "unknown",
        load: typeof p.load === "number" ? p.load : undefined,
        online: p.online !== false,
        raw: p
      }));
    }
  }

  // ---------------- Bridge-Adapter (STUB, nicht verbunden) ----------------
  // Später: Task/Executor-Rückmeldungen + Verifikationsbelege aus
  // POST /api/task / GET /api/task/{id} (Bearer-Auth). Hier nur Mapping.
  class BridgeAdapter extends ConvergenceAdapter {
    constructor(opts = {}) {
      super({ id: "bridge", label: "NEXUS Bridge (nicht verbunden)" });
      this.baseUrl = opts.baseUrl || "http://127.0.0.1:8299";
      this.status = "disconnected";
    }

    // Mappt einen Task-Record der Bridge auf Task- bzw. Verifikations-Events.
    static mapTask(raw) {
      const events = [];
      events.push({
        kind: KINDS.TASK,
        demo: false,
        id: raw.task_id,
        taskId: raw.task_id,
        title: raw.task || raw.title || "Task",
        owner: raw.actor || "hermes",
        taskStatus: raw.status || "queued",
        progress: raw.progress,
        raw
      });
      // Beleg nur bei tatsächlich bestätigtem Readback.
      if (raw.task_readback_verified === true && raw.execution_instance_verified === true) {
        events.push({
          kind: KINDS.VERIFICATION,
          demo: false,
          id: raw.task_id,
          subject: raw.task || raw.task_id,
          confirmed: true,
          evidence: {
            readback: raw.task_readback_verified,
            instance: raw.execution_instance_verified,
            instanceId: raw.execution_instance_id || null
          },
          hash: raw.content_sha256 || null,
          raw
        });
      }
      return events;
    }
  }

  NEXUS.KINDS = KINDS;
  NEXUS.adapter = {
    ConvergenceAdapter,
    DemoAdapter,
    SharedRoomAdapter,
    BridgeAdapter
  };
})();
