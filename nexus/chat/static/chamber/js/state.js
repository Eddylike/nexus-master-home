/* =========================================================
   state.js — Zentraler Store + Event-Bus
   ========================================================= */
(function () {
  "use strict";
  const NEXUS = window.NEXUS;

  // ---- Event-Bus ----
  function Bus() {
    this._map = new Map();
  }
  Bus.prototype.on = function (evt, cb) {
    if (!this._map.has(evt)) this._map.set(evt, new Set());
    this._map.get(evt).add(cb);
    return () => this.off(evt, cb);
  };
  Bus.prototype.off = function (evt, cb) {
    const s = this._map.get(evt);
    if (s) s.delete(cb);
  };
  Bus.prototype.emit = function (evt, payload) {
    const s = this._map.get(evt);
    if (s) s.forEach((cb) => { try { cb(payload); } catch (e) { console.error("[bus:" + evt + "]", e); } });
    const a = this._map.get("*");
    if (a) a.forEach((cb) => { try { cb({ evt, payload }); } catch (e) { console.error("[bus:*]", e); } });
  };

  // ---- Store ----
  const initial = {
    phase: "boot",                 // boot | running | paused | done
    connection: { source: "demo", status: "demo", label: "DEMO · lokal" },
    view: "command",               // command | meeting

    agents: {
      nexus:  { id: "nexus",  name: "NEXUS",  role: "Master Home",        status: "idle", load: 0.18, active: false, tasks: 0, verified: 0 },
      hermes: { id: "hermes", name: "HERMES", role: "Executor-Bridge",    status: "idle", load: 0.10, active: false, tasks: 0, verified: 0 },
      system: { id: "system", name: "NEXUS OS", role: "Systemträger",     status: "idle", load: 0.22, active: false, tasks: 0, verified: 0 }
    },

    discussion: {
      id: null,
      topic: "—",
      round: 0,
      maxRounds: 6,
      status: "idle",              // idle | active | converge | diverge | blocked
      votes: []                    // [{agent, choice, note}]
    },

    messages: [],
    missions: [],
    events: [],                    // Timeline (neueste zuletzt)
    decision: null,
    verification: null,            // { id, subject, confirmed, evidence, hash, demo }
    blockers: [],
    convergence: 0                 // 0..1
  };

  const Store = {
    state: JSON.parse(JSON.stringify(initial)),
    bus: new Bus(),

    get() { return this.state; },

    patch(obj) {
      Object.assign(this.state, obj);
      this.bus.emit("change", this.state);
    },

    // tiefes, gezieltes Update über Pfad-Callback
    update(fn) {
      fn(this.state);
      this.bus.emit("change", this.state);
    },

    reset() {
      this.state = JSON.parse(JSON.stringify(initial));
      this.bus.emit("reset", this.state);
      this.bus.emit("change", this.state);
    },

    // ---- Fachliche Mutationen ----
    setAgent(id, patch) {
      const a = this.state.agents[id];
      if (!a) return;
      Object.assign(a, patch);
      this.bus.emit("agent", a);
      this.bus.emit("change", this.state);
    },

    addMessage(msg) {
      const m = Object.assign({ id: NEXUS.util.uid("msg"), ts: NEXUS.util.nowISO(), demo: true }, msg);
      this.state.messages.push(m);
      if (this.state.messages.length > 200) this.state.messages.shift();
      this.bus.emit("message", m);
      this.bus.emit("change", this.state);
      return m;
    },

    upsertMission(mission) {
      const list = this.state.missions;
      const i = list.findIndex((m) => m.id === mission.id);
      let target;
      if (i >= 0) { Object.assign(list[i], mission); target = list[i]; }
      else { target = Object.assign({ progress: 0, demo: true }, mission); list.unshift(target); }
      if (list.length > 40) list.pop();
      this.bus.emit("mission", target);
      this.bus.emit("change", this.state);
      return target;
    },

    addEvent(evt) {
      const e = Object.assign({ id: NEXUS.util.uid("ev"), ts: NEXUS.util.nowISO(), demo: true }, evt);
      this.state.events.push(e);
      if (this.state.events.length > 120) this.state.events.shift();
      this.bus.emit("event", e);
      this.bus.emit("change", this.state);
      return e;
    },

    addBlocker(blocker) {
      const b = Object.assign({ id: NEXUS.util.uid("blk"), ts: NEXUS.util.nowISO(), demo: true }, blocker);
      this.state.blockers.unshift(b);
      if (this.state.blockers.length > 30) this.state.blockers.pop();
      this.bus.emit("blocker", b);
      this.bus.emit("change", this.state);
      return b;
    },

    setDiscussion(patch) {
      Object.assign(this.state.discussion, patch);
      this.bus.emit("discussion", this.state.discussion);
      this.bus.emit("change", this.state);
    },

    setVerification(v) {
      // Regel: Ein Seal nur mit confirmed === true UND vorhandenem Beleg.
      if (v && v.confirmed === true && v.evidence) {
        this.state.verification = Object.assign({ demo: true }, v);
        this.bus.emit("verification", this.state.verification);
      } else {
        this.state.verification = null;
        this.bus.emit("verification", null);
      }
      this.bus.emit("change", this.state);
    },

    bumpConvergence(v) {
      this.state.convergence = NEXUS.util.clamp(v, 0, 1);
      this.bus.emit("convergence", this.state.convergence);
      this.bus.emit("change", this.state);
    },

    decayConvergence() {
      if (this.state.convergence > 0) {
        this.state.convergence = Math.max(0, this.state.convergence - 0.004);
      }
    }
  };

  NEXUS.bus = Store.bus;
  NEXUS.store = Store;
})();
