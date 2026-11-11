/* =========================================================
   timeline.js — Leuchtende Ereignis-Timeline
   ========================================================= */
(function () {
  "use strict";
  const NEXUS = window.NEXUS;
  const U = NEXUS.util;

  const Timeline = {
    root: null,

    init() {
      this.root = document.getElementById("timeline");
      NEXUS.store.get().events.forEach((e) => this._append(e));
      NEXUS.bus.on("event", (e) => this._append(e, true));
      NEXUS.bus.on("reset", () => { if (this.root) this.root.innerHTML = ""; });
      return this;
    },

    _append(e, scroll) {
      if (!this.root) return;
      const node = U.el("div", "tl-event");
      node.dataset.kind = e.kind || "system";
      const time = U.el("span", "tl-time", U.timeShort(e.ts));
      const dot = U.el("span", "tl-dot");
      const text = U.el("span", "tl-text", U.escape(e.text || e.message || e.subject || e.kind));
      node.appendChild(dot);
      node.appendChild(time);
      node.appendChild(text);
      if (e.demo) node.appendChild(U.el("span", "tl-demo", "DEMO"));
      this.root.appendChild(node);
      while (this.root.children.length > 120) this.root.removeChild(this.root.firstChild);
      if (scroll) this.root.scrollLeft = this.root.scrollWidth;
    }
  };

  NEXUS.timeline = Timeline;
})();
