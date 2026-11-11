/* =========================================================
   util.js — Namespace + Hilfsfunktionen
   Global: window.NEXUS
   ========================================================= */
(function () {
  "use strict";

  const NEXUS = (window.NEXUS = window.NEXUS || {});
  NEXUS.version = "0.1.0-prototype";

  const U = {
    clamp(v, a, b) { return Math.min(b, Math.max(a, v)); },
    lerp(a, b, t) { return a + (b - a) * t; },
    rand(a, b) { return a + Math.random() * (b - a); },
    randInt(a, b) { return Math.floor(U.rand(a, b + 1)); },
    pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; },

    uid(prefix = "id") {
      return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
    },

    nowISO() { return new Date().toISOString(); },

    timeShort(ts) {
      const d = ts ? new Date(ts) : new Date();
      return d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    },

    // Deterministischer Kurz-Hash (djb2) — nur für Demo-Belegkennungen,
    // ausdrücklich KEINE kryptografische Integrität.
    demoHash(str) {
      let h = 5381;
      for (let i = 0; i < str.length; i++) h = ((h << 5) + h + str.charCodeAt(i)) >>> 0;
      return h.toString(16).padStart(8, "0") + Math.abs(str.length * 2654435761 % 4294967296).toString(16).padStart(8, "0").slice(0, 8);
    },

    reducedMotion() {
      const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
      return mq.matches || NEXUS.forceReduced === true;
    },

    el(tag, cls, html) {
      const n = document.createElement(tag);
      if (cls) n.className = cls;
      if (html != null) n.innerHTML = html;
      return n;
    },

    $ (sel, root) { return (root || document).querySelector(sel); },
    $$(sel, root) { return Array.from((root || document).querySelectorAll(sel)); },

    toast(msg, kind = "info", ms = 3200) {
      const t = document.getElementById("toast");
      if (!t) return;
      t.textContent = msg;
      t.dataset.kind = kind;
      t.classList.add("show");
      clearTimeout(NEXUS._toastTimer);
      NEXUS._toastTimer = setTimeout(() => t.classList.remove("show"), ms);
    },

    escape(str) {
      return String(str == null ? "" : str)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    },

    // Geräte-Qualität grob schätzen
    guessQuality() {
      const mem = navigator.deviceMemory || 4;
      const cores = navigator.hardwareConcurrency || 4;
      const small = Math.min(window.innerWidth, window.innerHeight) < 700;
      if (small || mem <= 2 || cores <= 2) return "low";
      if (mem <= 4 || cores <= 4) return "medium";
      return "high";
    }
  };

  NEXUS.util = U;
})();
