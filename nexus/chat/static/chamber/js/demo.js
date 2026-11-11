/* =========================================================
   demo.js — Interaktive Demo-Engine (gescriptetes Szenario)
   Alle Ereignisse laufen über den DemoAdapter und sind demo:true.
   Der produktive NEXUS wird NICHT verbunden.
   ========================================================= */
(function () {
  "use strict";
  const NEXUS = window.NEXUS;
  const U = NEXUS.util;

  const Demo = {
    adapter: null,
    steps: [],
    idx: 0,
    timer: null,
    playing: false,
    speed: 1,
    finished: false,

    init() {
      this.adapter = new NEXUS.adapter.DemoAdapter();
      this.steps = this._buildScenario();
      this._bindControls();
      this.adapter.connect();
      this._syncButtons();
      return this;
    },

    // Hilfs-Emit über den Adapter (einziger Ereignispfad)
    _emit(evt) { this.adapter.emit(evt); },

    _next() {
      if (!this.playing) return;
      if (this.idx >= this.steps.length) { this._finish(); return; }
      const step = this.steps[this.idx++];
      try { step.fn(this._emit.bind(this)); } catch (e) { console.error("[demo step]", e); }
      this._scheduleNext(step.delay || 1100);
      this._syncButtons();
    },

    _scheduleNext(delay) {
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this._next(), delay / this.speed);
    },

    play() {
      if (this.finished) this.reset();
      this.playing = true;
      NEXUS.store.patch({ phase: "running" });
      NEXUS.camera.setIdle();
      this._next();
      this._syncButtons();
    },

    pause() {
      this.playing = false;
      clearTimeout(this.timer);
      NEXUS.store.patch({ phase: "paused" });
      this._syncButtons();
    },

    stepOnce() {
      this.playing = false;
      clearTimeout(this.timer);
      if (this.idx >= this.steps.length) { this._finish(); return; }
      const step = this.steps[this.idx++];
      try { step.fn(this._emit.bind(this)); } catch (e) { console.error(e); }
      this._syncButtons();
    },

    reset() {
      clearTimeout(this.timer);
      this.playing = false;
      this.idx = 0;
      this.finished = false;
      NEXUS.store.reset();
      NEXUS.store.patch({ phase: "boot", connection: { source: "demo", status: "demo", label: "DEMO · lokal" } });
      this._syncButtons();
    },

    _finish() {
      this.playing = false;
      this.finished = true;
      NEXUS.store.patch({ phase: "done" });
      this._emit({ kind: "system", code: "DEMO_COMPLETE", message: "Demo-Zyklus abgeschlossen. Alle Ergebnisse sind DEMO." });
      this._syncButtons();
    },

    _syncButtons() {
      const play = document.getElementById("btn-play");
      const pause = document.getElementById("btn-pause");
      const step = document.getElementById("btn-step");
      if (!play) return;
      play.disabled = this.playing;
      play.textContent = this.finished ? "▶ Demo erneut" : (this.idx > 0 ? "▶ Fortsetzen" : "▶ Demo starten");
      if (pause) pause.disabled = !this.playing;
      if (step) step.disabled = this.playing;
    },

    _bindControls() {
      document.getElementById("btn-play").addEventListener("click", () => this.play());
      document.getElementById("btn-pause").addEventListener("click", () => this.pause());
      document.getElementById("btn-step").addEventListener("click", () => this.stepOnce());
      document.getElementById("btn-reset").addEventListener("click", () => { this.reset(); U.toast("Demo zurückgesetzt.", "info", 1600); });
    },

    // ---------------- Szenario ----------------
    _buildScenario() {
      const self = this;
      const msg = (from, to, content, role) => ({ kind: "message", from, to, content, role: role || "message" });
      const task = (id, title, owner, status, progress, detail) => ({ kind: "task", id, taskId: id, title, owner, taskStatus: status, status, progress, detail });
      const S = (delay, fn) => ({ delay, fn });

      const steps = [];
      const push = (delay, fn) => steps.push(S(delay, fn));

      // --- Phase 1: Systemstart ---
      push(600, (e) => {
        e({ kind: "system", code: "CHAMBER_INIT", text: "Convergence Chamber initialisiert · Adapter: DEMO (lokal)" });
        NEXUS.store.patch({ connection: { source: "demo", status: "demo", label: "DEMO · lokal" } });
      });
      push(700, (e) => { e({ kind: "participant", agent: "nexus", status: "active", load: 0.34 }); });
      push(500, (e) => { e({ kind: "participant", agent: "hermes", status: "active", load: 0.22 }); });
      push(500, (e) => { e({ kind: "participant", agent: "system", status: "active", load: 0.28 }); });
      push(700, (e) => e({ kind: "discussion", topic: "SharedRoom-Synchronisation · orbitaler Nachrichtenabgleich", round: 1, maxRounds: 6, discussionStatus: "active" }));

      // --- Phase 2: Konvergenter Lauf (READ_ONLY) ---
      push(900, (e) => e(msg("nexus", "hermes", "Hermes, Status der Executor-Bridge?", "question")));
      push(1100, (e) => e(msg("hermes", "nexus", "Bridge erreichbar. 12 registrierte Executoren aktiv. Bereit für Task-Vorschlag.", "answer")));
      push(1000, (e) => e(msg("nexus", "hermes", "Vorschlag: lesender Health-Abgleich des Workspace. Permission READ_ONLY.", "proposal")));
      push(900, (e) => e({ kind: "discussion", topic: "SharedRoom-Synchronisation · orbitaler Nachrichtenabgleich", round: 2, maxRounds: 6, discussionStatus: "active" }));
      push(900, (e) => e(msg("hermes", "nexus", "Zugestimmt. Führe Task NX-DEMO-0001 aus.", "accept")));
      push(700, (e) => { e(task("NX-DEMO-0001", "Workspace Health-Check", "hermes", "running", 0.15, "READ_ONLY")); e({ kind: "participant", agent: "hermes", status: "active", load: 0.55 }); });
      push(900, (e) => { e(task("NX-DEMO-0001", "Workspace Health-Check", "hermes", "running", 0.6, "Lese Workspace-Index")); });
      push(1000, (e) => {
        e({ kind: "executor_result", taskId: "NX-DEMO-0001", actor: "hermes", status: "done", exitCode: 0, verified: true, stdout: "12 Executoren · 0 Fehler", demo: true });
      });
      push(600, (e) => e({ kind: "verification", subject: "NX-DEMO-0001 · Workspace Health-Check", confirmed: true, evidence: { readback: true, instance: true, instanceId: "demo-nxos-01" }, hash: U.demoHash("NX-DEMO-0001-health"), demo: true }));
      push(700, (e) => e({ kind: "decision", outcome: "converge", topic: "Workspace Health-Check freigeben", votes: [{ agent: "nexus", choice: "yes" }, { agent: "hermes", choice: "yes" }], rationale: "Beide Kerne bestätigen verifizierten READ_ONLY-Abgleich." }));

      // --- Phase 3: Blocker (PRIVILEGED ohne Freigabe) ---
      push(1500, (e) => e(msg("nexus", "hermes", "Neuer Vorschlag: direkter Schreibzugriff auf einen Systempfad.", "proposal")));
      push(800, (e) => e({ kind: "discussion", topic: "Privilegierter Systemschreibzugriff", round: 3, maxRounds: 6, discussionStatus: "active" }));
      push(1000, (e) => e(msg("hermes", "nexus", "Ablehnung. PRIVILEGED erfordert lokale Freigabe. Vorgang wird blockiert.", "reject")));
      push(500, (e) => { e(task("NX-DEMO-0002", "Systempfad-Schreibzugriff", "hermes", "blocked", 0, "PRIVILEGED")); e({ kind: "participant", agent: "hermes", status: "blocked", load: 0.4 }); });
      push(600, (e) => e({ kind: "blocker", code: "LOCAL_APPROVAL_REQUIRED", message: "PRIVILEGED-Aktion ohne lokale Freigabe abgewiesen (DEMO).", demo: true }));
      push(500, (e) => e({ kind: "executor_result", taskId: "NX-DEMO-0002", actor: "hermes", status: "blocked", exitCode: null, verified: false, stderr: "local approval required", demo: true }));
      push(600, (e) => e({ kind: "decision", outcome: "diverge", topic: "Systemschreibzugriff", votes: [{ agent: "nexus", choice: "yes" }, { agent: "hermes", choice: "no" }], rationale: "Keine Einigung; sicherer Ersatzweg vorgeschlagen." }));

      // --- Phase 4: Auflösung (create-only, eingeschränkter Scope) ---
      push(1400, (e) => e(msg("nexus", "hermes", "Einverstanden. Ersatzweg: create-only unter workspace/projects/hermes, kein Überschreiben.", "proposal")));
      push(800, (e) => e({ kind: "discussion", topic: "Eingeschränkter create-only Ersatzweg", round: 4, maxRounds: 6, discussionStatus: "active" }));
      push(900, (e) => e(msg("hermes", "nexus", "Zugestimmt. Führe Task NX-DEMO-0003 mit Datei-Vertrag aus.", "accept")));
      push(600, (e) => { e(task("NX-DEMO-0003", "create-only Artefakt", "hermes", "running", 0.2, "workspace/projects/hermes/convergence.log")); e({ kind: "participant", agent: "hermes", status: "active", load: 0.6 }); });
      push(1000, (e) => e(task("NX-DEMO-0003", "create-only Artefakt", "hermes", "running", 0.7, "Schreibe Artefakt + Byte-Vergleich")));
      push(900, (e) => e({ kind: "executor_result", taskId: "NX-DEMO-0003", actor: "hermes", status: "done", exitCode: 0, verified: true, stdout: "read -> compare: identisch", demo: true }));
      push(600, (e) => e({ kind: "verification", subject: "NX-DEMO-0003 · create-only Artefakt", confirmed: true, evidence: { readback: true, instance: true, bytesEqual: true, instanceId: "demo-nxos-01" }, hash: U.demoHash("NX-DEMO-0003-artifact"), demo: true }));
      push(800, (e) => {
        e({ kind: "decision", outcome: "converge", topic: "Create-only Ersatzweg angenommen", votes: [{ agent: "nexus", choice: "yes" }, { agent: "hermes", choice: "yes" }], rationale: "Konvergenz über sicheren, verifizierten Pfad." });
        // großer Convergence-Puls + Kamerafahrt
        NEXUS.store.bumpConvergence(1);
        NEXUS.agents.setConvergenceEnergy(1);
        NEXUS.effects.shockwave(0x58f0a6, 1.4);
      });
      push(900, (e) => {
        e({ kind: "system", code: "CYCLE_COMPLETE", text: "Chamber-Zyklus abgeschlossen · Ergebnisse DEMO-verifiziert." });
        NEXUS.store.setDiscussion({ round: 6, discussionStatus: "converge", status: "converge" });
      });

      return steps;
    }
  };

  NEXUS.demo = Demo;
})();
