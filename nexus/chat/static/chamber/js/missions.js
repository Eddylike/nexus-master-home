/* =========================================================
   missions.js — Animiertes Missionsdeck
   ========================================================= */
(function () {
  "use strict";
  const NEXUS = window.NEXUS;
  const U = NEXUS.util;

  const LABEL = {
    queued: "In Warteschlange",
    running: "In Ausführung",
    blocked: "Blockiert",
    done: "Abgeschlossen",
    verified: "Verifiziert"
  };

  const Missions = {
    root: null, countEl: null,

    init() {
      this.root = document.getElementById("mission-list");
      this.countEl = document.getElementById("mission-count");
      this._renderAll();
      NEXUS.bus.on("mission", () => this._renderAll());
      NEXUS.bus.on("reset", () => this._renderAll());
      return this;
    },

    _renderAll() {
      if (!this.root) return;
      const list = NEXUS.store.get().missions;
      this.root.innerHTML = "";
      list.forEach((m) => this.root.appendChild(this._card(m)));
      if (this.countEl) this.countEl.textContent = String(list.length);
    },

    _card(m) {
      const card = U.el("div", "mission-card");
      card.dataset.status = m.status || "queued";
      card.dataset.id = m.id;

      const top = U.el("div", "mission-top");
      top.appendChild(U.el("span", "mission-title", U.escape(m.title || "Mission")));
      top.appendChild(U.el("span", "mission-id", U.escape(m.id || "")));
      card.appendChild(top);

      card.appendChild(U.el("div", "mission-owner", "Träger: " + U.escape(m.owner || "—")));

      const progress = U.clamp(m.progress == null ? (m.status === "done" || m.status === "verified" ? 1 : m.status === "running" ? 0.5 : 0) : m.progress, 0, 1);
      const bar = U.el("div", "mission-progress");
      const fill = U.el("i");
      fill.style.width = Math.round(progress * 100) + "%";
      bar.appendChild(fill);
      card.appendChild(bar);

      const status = U.el("div", "mission-status", (LABEL[m.status] || m.status || "") + (m.substatus ? " · " + U.escape(m.substatus) : ""));
      card.appendChild(status);

      card.addEventListener("click", () => {
        if (m.owner && NEXUS.agents.cores[m.owner]) {
          NEXUS.camera.mode = "ride";
          NEXUS.camera.rideIdx = NEXUS.CAMERA_RIDES.indexOf(m.owner === "hermes" ? "hermes" : "nexus");
          NEXUS.camera.rideTimer = 0;
        }
      });
      return card;
    }
  };

  NEXUS.missions = Missions;
})();
