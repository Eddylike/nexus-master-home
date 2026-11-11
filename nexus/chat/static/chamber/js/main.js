/* =========================================================
   main.js — Bootstrap: Verdrahtung Store <-> 3D <-> HUD <-> Adapter
   ========================================================= */
(function () {
  "use strict";
  const NEXUS = window.NEXUS;
  const U = NEXUS.util;

  const NAME = { nexus: "NEXUS", hermes: "HERMES", system: "NEXUS OS", human: "EDDY", local_ai: "LOCAL AI", chatgpt: "CHATGPT" };
  const nameOf = (id) => NAME[id] || id || "SYSTEM";
  const trunc = (s, n = 54) => { s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; };

  // ---------- Kanonische Ereignisse -> Store + Visuals ----------
  function applyEvent(e) {
    const store = NEXUS.store, agents = NEXUS.agents, beams = NEXUS.beams, effects = NEXUS.effects;

    switch (e.kind) {
      case "system":
        store.addEvent({ kind: "system", text: e.text || e.message, demo: e.demo });
        break;

      case "participant": {
        const st = e.status || "idle";
        const active = st === "active";
        store.setAgent(e.agent, { status: st, active, load: e.load != null ? e.load : undefined });
        agents.setStatus(e.agent, { status: st, load: e.load });
        store.addEvent({ kind: "system", text: `${nameOf(e.agent)} online · ${active ? "aktiv" : st} · Last ${Math.round((e.load || 0) * 100)}%`, demo: e.demo });
        break;
      }

      case "message": {
        store.addMessage({ id: e.id, ts: e.ts, session_id: e.raw && e.raw.session_id, parent_message_id: e.raw && e.raw.parent_message_id, from: e.from, to: e.to, role: e.role, content: e.content, demo: e.demo });
        store.addEvent({ kind: "message", text: `${nameOf(e.from)} → ${nameOf(e.to)} · ${trunc(e.content)}`, demo: e.demo });
        if (e.from === "nexus" || e.from === "hermes") beams.transmission(e.from, e.to);
        agents.setStatus(e.from, { status: "active" });
        break;
      }

      case "discussion": {
        store.setDiscussion({ topic: e.topic, round: e.round, maxRounds: e.maxRounds || 6, status: e.discussionStatus || e.status || "active" });
        store.addEvent({ kind: "system", text: `Diskussion · Runde ${e.round}: ${trunc(e.topic, 46)}`, demo: e.demo });
        ["nexus", "hermes"].forEach((id) => agents.setStatus(id, { status: "active" }));
        break;
      }

      case "task": {
        const status = e.status || e.taskStatus;
        store.upsertMission({ demo: e.demo, id: e.taskId || e.id, title: e.title, owner: e.owner, status, progress: e.progress, detail: e.detail });
        store.addEvent({ kind: "task", text: `Task ${e.taskId || e.id} · ${status}`, demo: e.demo });
        store.addMessage({ from: "system", role: "task", kind: "task", content: `TASK ${e.taskId || e.id} [${String(status).toUpperCase()}] ${e.title}${e.detail ? " · " + e.detail : ""}`, demo: e.demo });
        if (status === "running") agents.setStatus(e.owner, { status: "active" });
        break;
      }

      case "executor_result": {
        const verified = e.verified === true;
        store.upsertMission({
          id: e.taskId, demo: e.demo,
          status: verified ? "verified" : (e.status || "done"),
          progress: verified ? 1 : undefined,
          substatus: verified ? "Readback belegt" : "unbestätigt"
        });
        store.addMessage({
          from: "hermes", role: "result", kind: "executor_result",
          content: `Executor-Rückmeldung ${e.taskId}: ${e.status} · exit=${e.exitCode == null ? "—" : e.exitCode}${e.stdout ? " · " + e.stdout : ""}${e.stderr ? " · " + e.stderr : ""}`,
          demo: e.demo
        });
        store.addEvent({ kind: verified ? "verify" : "task", text: `Executor ${e.taskId} · ${e.status}${verified ? " · verifiziert" : ""}`, demo: e.demo });
        agents.pulse("hermes", 0.6);
        store.setAgent("hermes", { active: true, load: null });
        break;
      }

      case "verification": {
        store.setVerification({ id: e.id, subject: e.subject, confirmed: e.confirmed === true, evidence: e.evidence, hash: e.hash, demo: e.demo });
        if (e.confirmed === true && e.evidence) store.addEvent({ kind: "verify", text: `Beleg bestätigt: ${e.subject}`, demo: e.demo });
        break;
      }

      case "decision": {
        store.patch({ decision: { outcome: e.outcome, topic: e.topic, votes: e.votes } });
        const dstat = e.outcome === "converge" ? "converge" : e.outcome === "diverge" ? "diverge" : "blocked";
        store.setDiscussion({ status: dstat, topic: e.topic, votes: e.votes });
        store.addMessage({ from: "system", role: "decision", kind: "decision", content: `Entscheidung [${String(e.outcome).toUpperCase()}]: ${e.topic}${e.rationale ? " · " + e.rationale : ""}`, demo: e.demo });
        store.addEvent({ kind: "decision", text: `Entscheidung: ${trunc(e.topic, 40)} (${e.outcome})`, demo: e.demo });
        if (e.outcome === "converge") { store.bumpConvergence(1); agents.setConvergenceEnergy(1); effects.shockwave(0x58f0a6, 1); beams.beamBurst("main"); }
        else if (e.outcome === "diverge") effects.shockwave(0xffab4c, 0.7);
        else effects.shockwave(0xff5a6a, 0.8);
        break;
      }

      case "blocker": {
        store.addBlocker({ code: e.code, message: e.message, demo: e.demo });
        store.addMessage({ from: "system", role: "blocker", kind: "blocker", content: `BLOCKER [${e.code}]: ${e.message}`, demo: e.demo });
        store.addEvent({ kind: "blocker", text: `Blocker: ${e.code}`, demo: e.demo });
        effects.shockwave(0xff5a6a, 0.6);
        break;
      }

      default:
        if (e.text || e.message) store.addEvent({ kind: e.kind || "system", text: e.text || e.message, demo: e.demo });
    }
  }

  // ---------- Agent-Legende ----------
  function renderAgents() {
    const root = document.getElementById("agent-list");
    if (!root) return;
    const st = NEXUS.store.get().agents;
    root.innerHTML = "";
    ["nexus", "hermes", "system"].forEach((id) => {
      const a = st[id];
      const card = U.el("div", "agent-card");
      card.dataset.agent = id;
      if (a.active) card.classList.add("active");

      const row = U.el("div", "agent-row");
      const left = U.el("div");
      left.appendChild(U.el("div", "agent-name", a.name));
      left.appendChild(U.el("div", "agent-role", a.role));
      row.appendChild(left);
      row.appendChild(U.el("span", "agent-status", a.status));
      card.appendChild(row);

      const bar = U.el("div", "agent-bar");
      const fill = U.el("i");
      fill.style.width = Math.round(U.clamp(a.load, 0, 1) * 100) + "%";
      bar.appendChild(fill);
      card.appendChild(bar);

      const meta = U.el("div", "agent-meta");
      meta.appendChild(U.el("span", null, a.load == null ? "Last unbekannt" : "Last " + Math.round(a.load * 100) + "%"));
      meta.appendChild(U.el("span", null, "Belege " + (a.verified || 0)));
      card.appendChild(meta);

      root.appendChild(card);
    });
  }

  // ---------- Uhr ----------
  function startClock() {
    const el = document.getElementById("clock");
    const tick = () => { if (el) el.textContent = U.timeShort(); };
    tick();
    setInterval(tick, 1000);
  }

  // ---------- Boot-Log ----------
  const BOOT_LINES = [
    "Lade Three.js-Kern (lokal) …",
    "Initialisiere Sternenfeld mit Tiefenwirkung …",
    "Rekonstruiere holografische Erdgeometrie …",
    "Binde KI-Kerne NEXUS / HERMES / NEXUS OS …",
    "Kalibriere Energie-Lichtstrahlen …",
    "Öffne Adapter-Kanal (DEMO · SharedRoom-Stub bereit) …",
    "Convergence Chamber bereit."
  ];

  function runBootLog() {
    const log = document.getElementById("boot-log");
    const btn = document.getElementById("btn-boot");
    if (!log) return;
    log.innerHTML = "";
    btn.disabled = true;
    let i = 0;
    const reduce = U.reducedMotion();
    const step = () => {
      if (i >= BOOT_LINES.length) { btn.disabled = false; return; }
      const li = U.el("li", "ok", U.escape(BOOT_LINES[i++]));
      log.appendChild(li);
      setTimeout(step, reduce ? 60 : 260);
    };
    step();
  }

  // ---------- Boot ----------
  function startApp() {
    const canvas = document.getElementById("scene");
    const reduce = U.reducedMotion();
    document.getElementById("app").dataset.reduced = String(reduce);

    const quality = "low";
    document.getElementById("sel-quality").value = quality;

    NEXUS.scene.init(canvas, { quality });
    NEXUS.stars.init();
    NEXUS.earth.init();
    NEXUS.agents.init();
    NEXUS.beams.init();
    NEXUS.effects.init();
    NEXUS.camera.init();

    // HUD
    NEXUS.timeline.init();
    NEXUS.missions.init();
    NEXUS.chat.init();
    NEXUS.verification.init();
    renderAgents();
    NEXUS.bus.on("change", renderAgents);
    NEXUS.bus.on("agent", renderAgents);

    // Adapter -> Store (einziger Eingangspunkt)
    NEXUS.live.init(applyEvent);

    // Bedienkonsole (Auftrag / Frage / Kommentar)
    NEXUS.composer.init();

    // Live-Systemstatus (CPU/RAM/Uptime/NEXUS/Ollama)
    NEXUS.sysstatus.init();

    // Convergence-Zerfall (ohne Store-Change-Flut)
    NEXUS.scene.add((dt) => {
      const c = NEXUS.store.state.convergence;
      if (c > 0) {
        NEXUS.store.state.convergence = Math.max(0, c - dt * 0.18);
        NEXUS.bus.emit("convergence", NEXUS.store.state.convergence);
      }
    });

    // Steuerung
    document.getElementById("btn-intro").addEventListener("click", () => { introRunning = true; NEXUS.camera.playIntro(); });
    document.getElementById("btn-cam").addEventListener("click", () => NEXUS.camera.cycleRide());
    document.getElementById("sel-quality").addEventListener("change", (ev) => onQuality(ev.target.value));

    // Intro -> Demo auto
    NEXUS.bus.on("camera:mode", (m) => {
      if (introRunning && m === "idle") {
        introRunning = false;
        // Live mode never starts a demo.
      }
    });

    // Boot-Button
    document.getElementById("btn-boot").addEventListener("click", () => {
      const boot = document.getElementById("boot");
      boot.classList.add("gone");
      NEXUS.scene.setActive(true);
      introRunning = true;
      NEXUS.camera.playIntro();
    });

    runBootLog();
    startClock();
    NEXUS.bus.emit("ready");

    // Autostart (Deep-Link / automatisierte Prüfung): ?autostart=1 oder ?autostart=demo
    const auto = new URLSearchParams(location.search).get("autostart");
    if (auto) {
      document.getElementById("boot").classList.add("gone");
      NEXUS.scene.setActive(true);
      if (auto === "demo") {
        NEXUS.camera.setIdle();
        // Live mode never starts a demo.
      } else {
        introRunning = true;
        NEXUS.camera.playIntro();
      }
    }
  }

  function onQuality(q) {
    NEXUS.scene.setQuality(q);
    // Ringe an Qualität anpassen
    if (NEXUS.earth.rings) {
      NEXUS.earth.rings.forEach((r, i) => { r.rgroup.visible = q === "low" ? i < 1 : q === "medium" ? i < 2 : true; });
    }
    U.toast("Qualität: " + q, "info", 1400);
  }

  let introRunning = false;

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", startApp);
  else startApp();
})();
