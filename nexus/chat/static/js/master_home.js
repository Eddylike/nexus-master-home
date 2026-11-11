(function () {
  "use strict";
  const byId = id => document.getElementById(id);
  const panel = byId("masterOrders");
  let busy = false, fetching = false, sessionId = null, canResume = false;
  let latest = null;
  function element(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    if (cls) node.className = cls;
    return node;
  }
  function button(text, fn) {
    const node = element("button", text);
    node.type = "button";
    node.onclick = fn;
    return node;
  }
  async function get(url) {
    const response = await fetch(url, {cache: "no-store"});
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json();
    if (data.success === false) throw new Error(data.error || "Nicht verfügbar");
    return data;
  }
  function send(text) {
    if (busy) return;
    panel.close();
    window.dispatchEvent(new CustomEvent("nexus:send", {detail: text}));
  }
  function draft(text) {
    panel.close();
    const input = byId("nxInput");
    input.value = text;
    input.dispatchEvent(new Event("input"));
    input.focus();
  }
  function controls() {
    byId("masterResume").disabled = busy || !canResume;
    byId("masterResume").title = canResume ? "Gespeicherten Auftrag dieser Sitzung fortsetzen" : "Kein gespeicherter Auftrag dieser Sitzung";
    panel.querySelectorAll("[data-master-send]").forEach(b => b.disabled = busy);
  }
  function renderResult(result) {
    const box = byId("masterResult");
    box.replaceChildren();
    if (!result) { box.append(element("p", "Wähle einen Chat oder starte einen Auftrag.")); return; }
    const data = result.data || {};
    const proven = ["persisted_bridge_tasks", "persisted_order_journals", "registry_and_receipts"].includes(data.provenance);
    box.append(element("strong", proven ? (data.execution_status || result.status || "Status unbekannt") : "Chatantwort · kein verifizierter Auftragsabschluss"));
    if (result.error) box.append(element("p", result.error, "master-error"));
    box.append(element("p", result.reply || result.content || ""));
    if (result.error) box.append(element("p", result.error, "master-error"));
    if ((data.task_ids || []).length) box.append(element("p", "Werkzeugbelege: " + data.task_ids.join(", "), "master-mono"));
    for (const item of data.verification || []) {
      box.append(element("p", (item.equal === true ? "Inhalt geprüft: " : "Ergebnis: ") + item.path));
    }
  }
  function renderOrders(data) {
    const box = byId("masterOrderList");
    box.replaceChildren();
    const rows = [...(data.orders || []), ...(data.project_orders || []), ...(data.code_orders || [])];
    if (!rows.length) box.append(element("p", "Noch keine gespeicherten Hermes-Aufträge."));
    for (const order of rows) {
      const card = element("details", undefined, "master-card");
      card.append(element("summary", (order.status || "UNKNOWN") + " · " + (order.task || order.name || "Auftrag")));
      if (order.total) card.append(element("p", order.completed + " / " + order.total + " Ergebnisse mit gespeichertem Inhaltsvergleich"));
      if (order.phase) card.append(element("p", "Phase: " + order.phase));
      if (order.error) card.append(element("p", order.error, "master-error"));
      if ((order.task_ids || []).length) card.append(element("p", order.task_ids.join(", "), "master-mono"));
      for (const file of order.artifacts || []) {
        const line = element("p", (file.verified ? "Geprüft: " : "Geplant: ") + file.path);
        if (/^workspace\/projects\/hermes\/[A-Za-z0-9_./-]+\.(txt|md|json|py)$/.test(file.path)) {
          const read = button("Im Chat lesen", () => send("Lies " + file.path));
          read.disabled = busy;
          line.append(read);
        }
        card.append(line);
        if (file.sha256) card.append(element("small", "SHA-256 " + file.sha256, "master-mono"));
      }
      box.append(card);
    }
    const receipts = byId("masterReceipts");
    receipts.replaceChildren();
    for (const item of data.receipts || []) {
      receipts.append(element("p", (item.id || "Beleg-ID fehlt") + " · " + item.task + " · " + item.status
        + (item.path ? " · " + item.path : "") + (item.error_code ? " · " + item.error_code : ""), "master-mono"));
    }
    byId("masterApprovalCount").textContent = data.approval_error ? "Freigabestatus nicht verfügbar" : (data.pending_count ?? "?") + " offene lokale Freigaben";
    const approvals = byId("masterApprovals");
    approvals.replaceChildren();
    for (const item of data.pending_approvals || []) approvals.append(element("p", item.approval_id + " · " + item.task + " · " + item.status));
  }
  async function refresh() {
    if (fetching) return;
    fetching = true;
    try {
      const data = await get("/api/orders");
      renderOrders(data);
      byId("masterUpdated").textContent = "Auftragsbelege aktualisiert: " + new Date().toLocaleTimeString("de-DE");
      if (sessionId) {
        const session = (await get("/api/session?id=" + encodeURIComponent(sessionId))).session || {};
        canResume = !!session.hermes_order;
        const messages = (session.messages || []).filter(m => m.role === "assistant");
        if (messages.length && !busy) { latest = messages[messages.length - 1]; renderResult(latest); }
      }
    } catch (error) {
      byId("masterUpdated").textContent = "Status nicht verfügbar: " + error.message + ". Es wurde kein Auftrag erneut gesendet.";
    } finally { fetching = false; controls(); }
  }
  byId("btnMasterOrders").onclick = () => { panel.showModal(); renderResult(latest); refresh(); };
  byId("masterClose").onclick = () => panel.close();
  byId("masterRefresh").onclick = refresh;
  byId("masterReload").onclick = () => { if (!busy) location.reload(); };
  byId("masterResume").onclick = () => send("weiter");
  panel.querySelectorAll("[data-master-send]").forEach(b => b.onclick = () => send(b.dataset.masterSend));
  panel.querySelectorAll("[data-master-draft]").forEach(b => b.onclick = () => draft(b.dataset.masterDraft));
  const approvalLink = byId("masterApprovalLink");
  if (!["127.0.0.1", "localhost", "[::1]"].includes(location.hostname)) {
    approvalLink.removeAttribute("href");
    approvalLink.textContent = "Freigaben am NEXUS-PC: http://127.0.0.1:8299/approvals";
  }
  window.addEventListener("nexus:session", e => {
    sessionId = e.detail.id;
    canResume = !!e.detail.hermes_order;
    latest = (e.detail.messages || []).filter(m => m.role === "assistant").pop() || null;
    renderResult(latest); controls();
  });
  window.addEventListener("nexus:busy", e => {
    busy = e.detail.busy;
    sessionId = e.detail.session_id || sessionId;
    byId("masterWorkState").textContent = busy ? "Auftrag läuft · Belege werden aktualisiert" : "Bereit";
    byId("masterReload").disabled = busy;
    controls();
    if (!busy || panel.open) refresh();
  });
  window.addEventListener("nexus:result", e => { latest = e.detail; renderResult(latest); });
  setInterval(() => { if (panel.open && !document.hidden) refresh(); }, 3000);
  get("/api/sessions").then(d => { sessionId = d.active; return refresh(); }).catch(() => {});
})();
