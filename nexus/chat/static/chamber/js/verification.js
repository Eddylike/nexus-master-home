/* =========================================================
   verification.js — Verification-Seal + Blocker + Convergence-Meter
   Regel: Ein Seal erscheint AUSSCHLIESSLICH bei confirmed===true
   UND vorhandenem Beleg. Demo-Belege sind klar als DEMO markiert.
   ========================================================= */
(function () {
  "use strict";
  const NEXUS = window.NEXUS;
  const U = NEXUS.util;

  const Verification = {
    body: null, blockerList: null, meter: null,

    init() {
      this.body = document.getElementById("verify-body");
      this.blockerList = document.getElementById("blocker-list");
      this.meter = document.getElementById("pulse-meter");

      NEXUS.bus.on("verification", (v) => this._renderSeal(v));
      NEXUS.bus.on("blocker", () => this._renderBlockers());
      NEXUS.bus.on("convergence", (v) => this._renderMeter(v));
      NEXUS.bus.on("reset", () => { this._renderSeal(null); this._renderBlockers(); });
      return this;
    },

    _renderSeal(v) {
      if (!this.body) return;
      this.body.innerHTML = "";
      if (!v) {
        this.body.appendChild(U.el("div", "verify-empty", "Kein bestätigter Beleg."));
        return;
      }
      const seal = U.el("div", "seal");
      const badge = U.el("div", "seal-badge");
      badge.appendChild(U.el("span", "seal-ring"));
      badge.appendChild(document.createTextNode("Verifiziert"));
      if (v.demo) badge.appendChild(U.el("span", "seal-demo", "DEMO"));
      seal.appendChild(badge);

      const ev = v.evidence || {};
      const rows = [
        ["Subjekt", v.subject || "—"],
        ["Beleg", typeof ev === "object" ? Object.keys(ev).filter((k) => ev[k]).map((k) => k).join(", ") || "belegt" : String(ev)],
        ["Hash", v.hash || "—"]
      ];
      rows.forEach(([k, val]) => {
        const f = U.el("div", "seal-field");
        f.appendChild(U.el("span", null, k));
        f.appendChild(U.el("span", null, U.escape(String(val))));
        seal.appendChild(f);
      });
      this.body.appendChild(seal);
      NEXUS.effects && NEXUS.effects.shockwave(v.demo ? 0x58f0a6 : 0x58f0a6, 1);
    },

    _renderBlockers() {
      if (!this.blockerList) return;
      const list = NEXUS.store.get().blockers;
      this.blockerList.innerHTML = "";
      if (!list.length) { this.blockerList.appendChild(U.el("div", "mini", "Keine Blocker.")); return; }
      list.forEach((b) => {
        const node = U.el("div", "blocker");
        node.appendChild(U.el("b", null, U.escape(b.code || "BLOCKED") + (b.demo ? " · DEMO" : "")));
        node.appendChild(document.createTextNode(U.escape(b.message || "")));
        this.blockerList.appendChild(node);
      });
    },

    _renderMeter(v) {
      if (!this.meter) return;
      this.meter.classList.toggle("hot", v > 0.4);
    }
  };

  NEXUS.verification = Verification;
})();
