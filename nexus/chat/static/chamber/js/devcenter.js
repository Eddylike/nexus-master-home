/* =========================================================
   devcenter.js — Entwicklungszentrale der Convergence Chamber

   Zeigt ausschließlich belegte Orchestrator-Daten aus /api/dev/orders:
   Auftrag, geplante Schritte, entstehende Dateien, tatsächlich gelaufene
   Werkzeuge (Task-IDs), Prüfergebnisse, Blocker, nötige Freigaben und den
   Startlink einer geprüften Vorschau. Jeder Auftrag hat seinen eigenen Zustand.
   Keine simulierten Daten: ohne Sitzung/Belege bleibt das Panel leer.
   ========================================================= */
(function () {
  "use strict";
  const POLL_MS = 1500;
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  };
  const session = () => new URLSearchParams(location.search).get("session_id") ||
    (window.NEXUS && window.NEXUS.live && window.NEXUS.live.session) || null;
  const CRIT = {
    no_syntax_errors: "Syntax", no_runtime_errors: "Laufzeitfehler", canvas_present: "Canvas",
    canvas_not_blank: "Bild", animates: "Animation", keyboard_controls: "Tastatur",
    alive_after_input: "Läuft nach Eingabe", network_blocked: "Netz gesperrt", visual_content: "Bildinhalt",
    input_changes_game: "Steuerung wirkt", game_state_readable: "Spielzustand", enemies_appear: "Gegner",
    shooting_works: "Schießen", player_stays_in_view: "Spieler im Bild", score_logic_present: "Punkte",
    laser_hits_enemy: "Laser trifft Gegner", movement_stops_on_release: "Stopp beim Loslassen",
    enemy_collision_costs_life: "Kollision kostet Leben", report_collected: "Prüfbericht"
  };
  // Aktuelle Kriterienstufe = höchste in diesem Auftrag gemeldete Version. Ältere Prüfungen
  // wurden nach schwächeren Kriterien bewertet und sind kein aktueller Beleg.
  const stageOf = (dev) => Math.max(0, ...dev.filter(e => e.step === "check" && e.criteria_version)
    .map(e => Number(e.criteria_version)));
  const STEP = { plan: "Plan", code: "Programmieren", check: "Browserprüfung", fix: "Korrektur",
    publish: "Vorschau veröffentlichen" };

  function chip(status) {
    const s = String(status || "—").toUpperCase();
    const c = el("span", "dev-chip", s);
    c.dataset.state = s.toLowerCase();
    return c;
  }

  function evidenceUrl(path) {
    // workspace/projects/hermes/preview_evidence/<projekt>/<datei>.png -> /preview-evidence/<projekt>/<datei>.png
    const m = /preview_evidence\/([A-Za-z0-9_-]+)\/((?:check|preview)-\d{8}-\d{6}\.png)$/.exec(String(path || ""));
    return m ? "/preview-evidence/" + m[1] + "/" + m[2] : null;
  }

  function link(href, text) {
    const a = el("a", "dev-link", text);
    a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer";
    return a;
  }

  function renderOrder(order) {
    const card = el("article", "dev-order");
    const head = el("div", "dev-head");
    head.append(chip(order.status), el("span", "dev-goal", order.goal));
    card.append(head);
    card.append(el("div", "mini", "Lauf " + (order.run_id || "—") + " · Phase " + (order.phase || "—") +
      (order.resumes ? " · setzt " + order.resumes + " fort" : "") +
      (order.superseded_by ? " · abgelöst durch " + order.superseded_by : "")));

    const dev = order.dev || [];
    const plan = [...dev].reverse().find(e => e.step === "plan");
    if (plan) {
      const sec = el("div", "dev-sec dev-plan");
      sec.append(el("div", "dev-label", "Plan (Hermes) · " + (plan.title || "") + " · " + (plan.plan_id || "")));
      const ul = el("ul", "dev-list");
      (plan.features || []).slice(0, 3).forEach(f => ul.append(el("li", null, f)));
      sec.append(ul);
      const files = el("div", "dev-files");
      (plan.files || []).forEach(f => files.append(el("code", null, f)));
      sec.append(files);
      card.append(sec);
    }

    const stage = stageOf(dev);
    const timeline = el("ol", "dev-steps");
    dev.forEach(e => {
      if (e.step === "step") {
        timeline.append(el("li", "run", (STEP[e.name] ? STEP[e.name] + ": " : "") +
          (e.detail || "") + (e.round !== undefined ? " (Runde " + e.round + ")" : "")));
      } else if (e.step === "files") {
        timeline.append(el("li", e.status === "PASS" ? "ok" : "bad",
          "Dateien geschrieben + zurückgelesen: " + e.status + " · " + (e.task_ids || []).join(", ")));
      } else if (e.step === "patch") {
        timeline.append(el("li", e.ok ? "ok" : "bad", "Prüfrunde " + e.round + " · Korrektur: patch_file " +
          (e.patch_task_id || "—") + " + read_file " + (e.read_task_id || "—")));
      } else if (e.step === "check") {
        const version = Number(e.criteria_version) || 0;
        const outdated = stage && version < stage;
        const verdict = e.passed ? (outdated ? "BESTANDEN nach älteren Kriterien" + (version ? " v" + version : "") +
          " – überholt durch strengere Kriterien v" + stage : "BESTANDEN") : "NICHT BESTANDEN";
        const counter = (e.fix_round !== undefined && e.max_fix_rounds)
          ? " · Korrekturrunde " + e.fix_round + "/" + e.max_fix_rounds : "";
        const li = el("li", outdated ? "old" : (e.passed ? "ok" : "bad"), "Prüfrunde " + e.round + ": " + (e.task_id || "—") +
          " · " + verdict + (version ? " · Kriterien v" + version : "") + counter);
        const crit = el("div", "dev-crit");
        Object.entries(e.criteria || {}).forEach(([k, v]) => crit.append(el("span", v ? "c-ok" : "c-bad",
          (v ? "✓ " : "✗ ") + (CRIT[k] || k))));
        li.append(crit);
        (e.errors || []).forEach(x => li.append(el("div", "dev-err", x)));
        const shot = evidenceUrl(e.screenshot);
        if (shot) li.append(link(shot, "Screenshot der Prüfung"));
        timeline.append(li);
      } else if (e.step === "preview") {
        timeline.append(el("li", e.published ? "ok" : "bad", "Vorschau: " +
          (e.published ? "veröffentlicht" : "nicht veröffentlicht – " + (e.error || "")) + " · " + (e.task_id || "—")));
      }
    });
    if (timeline.childElementCount) {
      const sec = el("div", "dev-sec dev-timeline");
      sec.append(el("div", "dev-label", "Ablauf (echte Werkzeugschritte)"), timeline);
      card.append(sec);
    }

    const ids = order.task_ids || [];
    if (ids.length) {
      const sec = el("div", "dev-sec dev-ids");
      sec.append(el("div", "dev-label", "Endbelege (Task-IDs)"));
      const box = el("div", "dev-files");
      const verified = new Map(((order.verification || {}).tasks || []).map(t => [t.task_id, t.verified]));
      ids.forEach(id => box.append(el("code", verified.get(id) === true ? "v-ok" : (verified.get(id) === false ? "v-bad" : null),
        id + (verified.has(id) ? (verified.get(id) ? " ✓" : " ✗") : ""))));
      sec.append(box);
      if ((order.superseded_task_ids || []).length)
        sec.append(el("div", "mini", "Überholte Zwischenstände: " + order.superseded_task_ids.join(", ")));
      card.append(sec);
    }

    if (order.approval_id) {
      const sec = el("div", "dev-sec dev-approval");
      sec.append(el("div", "dev-label", "Freigabe erforderlich"),
        link("/approvals", order.approval_id + " lokal prüfen und freigeben"),
        el("div", "mini", "Danach in der Chamber „weiter“ senden."));
      card.append(sec);
    }
    if (order.error && order.status !== "PASS") {
      card.append(el("div", "dev-err", "Blockiert/offen: " + order.error));
    }
    if (order.preview_url) {
      const sec = el("div", "dev-sec dev-open");
      sec.append(el("div", "dev-label", "Geprüfte Vorschau (keine produktive NEXUS-Integration)"),
        link(order.preview_url, "▶ Programm öffnen: " + order.preview_url));
      card.append(sec);
    }
    // Letzte Browserprüfung als kompakte Zusammenfassung.
    const lastCheck = [...dev].reverse().find(e => e.step === "check");
    let checkSec = null;
    if (lastCheck) {
      checkSec = el("div", "dev-sec dev-check");
      checkSec.append(el("div", "dev-label", "Letzte Browserprüfung · " + (lastCheck.task_id || "—") +
          (lastCheck.criteria_version ? " · Kriterien v" + lastCheck.criteria_version : "")),
        el("div", lastCheck.passed ? "c-ok" : "c-bad", lastCheck.passed ? "BESTANDEN" : "NICHT BESTANDEN"));
      const entries = Object.entries(lastCheck.criteria || {});
      if (entries.length) {
        const done = entries.filter(([, v]) => v).length;
        checkSec.append(el("div", "mini", "Prüfkriterien erfüllt: " + done + "/" + entries.length +
          (lastCheck.fix_round !== undefined && lastCheck.max_fix_rounds
            ? " · Korrekturrunde " + lastCheck.fix_round + "/" + lastCheck.max_fix_rounds : "")));
      }
      const failed = entries.filter(([, v]) => !v).map(([k]) => CRIT[k] || k);
      if (failed.length) checkSec.append(el("div", "dev-err", "Offene Fehler: " + failed.join(", ")));
      const shot = evidenceUrl(lastCheck.screenshot);
      if (shot) checkSec.append(link(shot, "Screenshot der Prüfung"));
    }
    // Wichtigstes zuerst: Startlink, Freigabe, Blocker, Prüfung, Belege; danach Ablauf und Plan.
    const q = (s) => card.querySelector(":scope > " + s);
    card.replaceChildren(...[q(".dev-head"), q(".mini"), q(".dev-open"), q(".dev-approval"), q(".dev-err"),
      checkSec, q(".dev-ids"), q(".dev-timeline"), q(".dev-plan")].filter(Boolean));
    return card;
  }

  async function poll() {
    const body = document.getElementById("dev-body");
    const sid = session();
    if (!body || !sid || document.hidden) return;
    try {
      const r = await fetch("/api/dev/orders?session_id=" + encodeURIComponent(sid), { cache: "no-store" });
      const data = await r.json();
      if (!data.success) throw Error(data.error || "nicht verfügbar");
      const count = document.getElementById("dev-count");
      if (count) count.textContent = String((data.orders || []).length);
      body.replaceChildren();
      if (!(data.orders || []).length) {
        body.append(el("div", "mini", "Noch kein Entwicklungsauftrag in dieser Sitzung."));
        return;
      }
      data.orders.slice(0, 6).forEach(o => body.append(renderOrder(o)));
    } catch (err) {
      body.replaceChildren(el("div", "mini", "Entwicklungsdaten nicht verfügbar: " + err.message));
    }
  }

  window.addEventListener("load", () => { poll(); setInterval(poll, POLL_MS); });
})();
