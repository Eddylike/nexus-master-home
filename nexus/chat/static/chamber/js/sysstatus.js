/* =========================================================
   sysstatus.js — Live-Systemstatus der Chamber.

   Pollt /api/system/monitor (bestehender, lesender Endpunkt):
   CPU, RAM, Systemlaufzeit, NEXUS- und Ollama-Status plus
   Alerts. Pure Anzeige — keine Ausführung, keine Schreibpfade.
   ========================================================= */
(function () {
  "use strict";
  const NEXUS = window.NEXUS;

  function el(id) { return document.getElementById(id); }

  function humanUptime(s) {
    if (s == null) return "n/a";
    s = Math.max(0, Math.floor(Number(s)));
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    return d > 0 ? d + "d " + h + "h " + m + "m" : (h > 0 ? h + "h " + m + "m" : m + "m");
  }

  function setBar(id, pct) {
    const b = el(id);
    if (!b) return;
    if (pct == null) { b.style.width = "0%"; b.style.background = "var(--edge)"; return; }
    const v = Math.min(100, Math.max(0, Number(pct)));
    b.style.width = v + "%";
    b.style.background = v > 80 ? "var(--red)" : "var(--green)";
  }

  function render(data) {
    const cpu = data.cpu || {};
    const ram = data.ram || {};
    const nx = data.nexus || {};
    const oll = data.ollama || {};
    const up = data.uptime || {};

    setBar("sys-cpu-bar", cpu.percent);
    if (el("sys-cpu")) el("sys-cpu").textContent = cpu.percent == null ? "n/a" : cpu.percent + "%";
    setBar("sys-ram-bar", ram.percent);
    if (el("sys-ram")) el("sys-ram").textContent =
      ram.percent == null ? "n/a" : (ram.used_gb || "?") + "/" + (ram.total_gb || "?") + " GB · " + ram.percent + "%";
    if (el("sys-up")) el("sys-up").textContent =
      up.human || (up.uptime_s != null ? humanUptime(up.uptime_s) : "n/a");
    if (el("sys-nexus")) el("sys-nexus").textContent =
      nx.listening === false ? "aus · Port " + (nx.port || "?") : "aktiv · PID " + (nx.pid || "?") + " · up " + humanUptime(nx.uptime_s);
    if (el("sys-ollama")) el("sys-ollama").textContent =
      oll.reachable ? "an · " + ((oll.preferred) || (oll.models || [])[0] || "?") : "aus";
    if (el("sys-ts")) el("sys-ts").textContent =
      (data.timestamp || "").replace("T", " ").slice(0, 19) || "—";

    const box = el("sys-alerts");
    if (box) {
      const alerts = (data.alerts || []) || [];
      box.innerHTML = alerts.map((a) =>
        '<div class="sys-alert ' + (a.level || "warn") + '">' + (a.message_de || a.code || "?") + "</div>"
      ).join("");
    }
  }

  function poll() {
    fetch("/api/system/monitor", { cache: "no-store" })
      .then((r) => r.json())
      .then(render)
      .catch(() => {
        const box = el("sys-alerts");
        if (box) box.innerHTML = '<div class="sys-alert bad">Systemstatus nicht erreichbar.</div>';
      });
  }

  let timer = null;
  NEXUS.sysstatus = {
    init() {
      if (!el("sys-cpu")) return this;
      poll();
      if (timer) clearInterval(timer);
      timer = setInterval(poll, 5000);
      return this;
    }
  };
})();