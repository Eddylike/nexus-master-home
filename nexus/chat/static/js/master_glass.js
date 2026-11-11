/* Master Home glass dashboard. Additive presentation of the existing desktop UI.
   Telemetry is measured by the existing monitor/status APIs. Missing fields stay
   UNKNOWN; partner readiness and all chat/avatar controls retain their owners. */
(function () {
  "use strict";

  const byId = id => document.getElementById(id);
  const query = selector => document.querySelector(selector);
  const paths = {
    chat: '<path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8A8.5 8.5 0 0 1 12.5 3H13a8.5 8.5 0 0 1 8 8v.5Z"/><path d="M8 9h8M8 13h5"/>',
    tasks: '<path d="m9 5 1.5 1.5L14 3M9 12l1.5 1.5L14 10M9 19l1.5 1.5L14 17M17 5h4M17 12h4M17 19h4M3 5h1M3 12h1M3 19h1"/>',
    agent: '<circle cx="12" cy="7" r="4"/><path d="M5 21v-3a7 7 0 0 1 14 0v3M2 18v-2m20 2v-2"/>',
    files: '<path d="M3 7V4h6l3 3h9v13H3V7Z"/>',
    notes: '<path d="m15 4 5 5M3 21l5-1 13-13-4-4L4 16l-1 5ZM4 16l4 4"/>',
    calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18M8 15h1M12 15h1M16 15h1M8 18h1M12 18h1"/>',
    timer: '<circle cx="12" cy="14" r="8"/><path d="M12 10v5l3 2M9 2h6M12 2v4M18 5l2 2"/>',
    contacts: '<circle cx="12" cy="7" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2M5 14l-2 2M19 14l2 2"/>',
    settings: '<path d="m10 3-.6 2.3-2 .8L5.3 5 3 9l1.8 1.6v2.8L3 15l2.3 4 2.1-1.1 2 .8L10 21h4l.6-2.3 2-.8 2.1 1.1 2.3-4-1.8-1.6v-2.8L21 9l-2.3-4-2.1 1.1-2-.8L14 3h-4Z"/><circle cx="12" cy="12" r="3"/>',
    devices: '<rect x="7" y="2" width="10" height="20" rx="2"/><path d="M10 18h4"/>',
    network: '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18M5 6h14M5 18h14"/>',
    apps: '<rect x="3" y="3" width="6" height="6" rx="1"/><rect x="15" y="3" width="6" height="6" rx="1"/><rect x="3" y="15" width="6" height="6" rx="1"/><rect x="15" y="15" width="6" height="6" rx="1"/>',
    cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4"/>',
    gpu: '<rect x="3" y="6" width="18" height="12" rx="2"/><circle cx="10" cy="12" r="3"/><path d="M17 9v6M3 10H1m2 4H1m6 4v3m4-3v3m4-3v3"/>',
    ram: '<rect x="2" y="7" width="20" height="10" rx="1"/><path d="M6 10v4M10 10v4M14 10v4M18 10v4M5 17v3m4-3v3m6-3v3m4-3v3"/>',
    disk: '<rect x="4" y="3" width="16" height="18" rx="3"/><circle cx="12" cy="10" r="3"/><path d="M8 17h8"/>',
    brain: '<path d="M9 4a3 3 0 0 0-5 3 4 4 0 0 0-1 7 4 4 0 0 0 6 5M15 4a3 3 0 0 1 5 3 4 4 0 0 1 1 7 4 4 0 0 1-6 5M9 3v18m6-18v18M5 9l4 2m6 0 4-2M5 15l4-1m6 0 4 1"/>',
    bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
    mic: '<rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/>',
    attach: '<path d="m9 17 7-7a3 3 0 0 0-4-4l-8 8a5 5 0 0 0 7 7L21 11a7 7 0 0 0-10-10L2 10"/>',
    send: '<path d="m22 2-7 20-4-9L2 9 22 2ZM11 13 22 2"/>',
    voice: '<path d="M11 5 6 9H2v6h4l5 4V5ZM15 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/>',
    live: '<path d="M4 10v4m4-8v12m4-16v20m4-16v12m4-8v4"/>',
    power: '<path d="M12 2v10M6 5a9 9 0 1 0 12 0"/>',
    mode: '<path d="M5 20V8h14v12M8 8V3h8v5M2 20h20M9 11v2m6-2v2m-6 3v2m6-2v2"/>',
    online: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2"/>',
    shield: '<path d="m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6l9-4Z"/><path d="m8 12 3 3 5-6"/>',
    whatsapp: '<path d="M4 20l1.3-3.9A8 8 0 1 1 8 19Z"/><path d="M9 9c0 3 3 6 6 6l1-1.5-2-1-1 1c-1-.5-2-1.5-2.5-2.5l1-1-1-2Z"/>',
    facebook: '<rect x="3" y="3" width="18" height="18" rx="4"/><path d="M15 8h-1.5A1.5 1.5 0 0 0 12 9.5V21M9.5 13H15"/>',
    youtube: '<rect x="2.5" y="5.5" width="19" height="13" rx="4"/><path d="m10 9 5 3-5 3Z"/>',
    folder: '<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2h8.5A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5Z"/>',
    mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3.5 6.5 8.5 7 8.5-7"/>',
    nexusall: '<rect x="2" y="4" width="20" height="14" rx="2"/><path d="m10 8 5 3-5 3z"/><path d="M12 18v3m-3-2 3 2 3-2"/>',
    library: '<path d="M12 6C10 4 7 4 4 5v14c3-1 6-1 8 1 2-2 5-2 8-1V5c-3-1-6-1-8 1Z"/><path d="M12 6v14"/>',
    studio: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m4 17 5-5 3 3 3-3 5 5"/><path d="M18 2v3m-1.5-1.5h3"/>',
    more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>'
  };

  function icon(name) {
    const span = document.createElement("span");
    span.className = "nx-glass-icon";
    span.setAttribute("aria-hidden", "true");
    // SVG paths are fixed application constants, never endpoint/user content.
    span.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">' + (paths[name] || paths.apps) + '</svg>';
    return span;
  }

  function element(tag, id, className, text) {
    const node = document.createElement(tag);
    if (id) node.id = id;
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function button(id, label, image, action, className) {
    const node = element("button", id, className || "nx-glass-button");
    node.type = "button";
    node.setAttribute("aria-label", label);
    node.append(icon(image), element("span", "", "nx-glass-button-label", label));
    node.addEventListener("click", action);
    return node;
  }

  function osOpen(url, payload) {
    return async (event) => {
      const card = event && event.currentTarget;
      const state = card && card.querySelector(".nx-reference-app-state");
      if (state) state.textContent = "startet …";
      try {
        const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        const data = await res.json();
        if (state) state.textContent = data && data.success ? "geöffnet" : "Fehler";
      } catch (err) {
        if (state) state.textContent = "Fehler";
      }
    };
  }

  function navigate(view) {
    const original = query('#sideNav button[data-view="' + view + '"]');
    const more = byId("sideMore");
    const wasOpen = more && more.open;
    if (original) original.click();
    if (more && !wasOpen) more.open = false;
  }

  function activate(id) {
    const original = byId(id);
    if (original) original.click();
  }

  function prepare(text) {
    navigate("chat");
    const input = byId("nxInput");
    if (!input) return;
    input.value = text;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
  }

  function makeNav() {
    const side = byId("nxSide"), more = byId("sideMore");
    const nav = element("nav", "nxGlassNav");
    nav.setAttribute("aria-label", "Master Home Navigation");
    const actions = [
      ["chats", "Chats", "chat", () => {
        const open = document.body.classList.toggle("nx-glass-history");
        byId("nxGlassNav-chats").setAttribute("aria-expanded", String(open));
        if (open) byId("chatSearch").focus();
      }],
      ["tasks", "Aufgaben", "tasks", () => activate("btnMasterOrders")],
      ["agents", "Agenten", "agent", () => navigate("crew"), "crew"],
      ["files", "Dateien", "files", () => navigate("files"), "files"],
      ["notes", "Notizen", "notes", () => navigate("memory"), "memory"],
      ["calendar", "Kalender", "calendar", () => prepare("Zeige mir meine Kalendertermine."), "", true],
      ["timer", "Timer", "timer", () => prepare("Starte einen Timer für 10 Minuten."), "", true],
      ["contacts", "Kontakte", "contacts", () => prepare("Zeige mir meine Kontakte."), "", true],
      ["system", "System", "settings", () => navigate("perf"), "perf"],
      ["devices", "Geräte", "devices", openSystemStatus],
      ["network", "Netzwerk", "network", () => navigate("perf"), "perf"],
      ["apps", "Apps", "apps", () => navigate("apps"), "apps"],
      ["settings", "Einstellungen", "settings", () => navigate("settings"), "settings"]
    ];
    actions.forEach(([id, label, image, action, view, draft], index) => {
      const node = button("nxGlassNav-" + id, label, image, action, "nx-glass-nav-button");
      if (view) node.dataset.glassView = view;
      if (draft) {
        node.title = label + ": Anfrage im Chat vorbereiten";
        node.dataset.action = "draft";
      }
      if (index === 8) node.classList.add("nx-glass-nav-section");
      if (id === "chats") {
        node.setAttribute("aria-expanded", "false");
        node.setAttribute("aria-controls", "nxGlassHistory");
      }
      nav.appendChild(node);
    });
    side.insertBefore(nav, more);
    const history = element("section", "nxGlassHistory");
    history.setAttribute("aria-label", "Chatverlauf");
    history.append(element("strong", "", "nx-glass-history-title", "Deine Chats"));
    [byId("chatSearch"), byId("sessionList"), query(".side-foot")].forEach(node => {
      if (node) history.appendChild(node);
    });
    side.insertBefore(history, more);
    const summary = query("#sideMore > summary");
    if (summary) summary.textContent = "Weitere Funktionen";
    more.open = false;
    const brandSub = query(".logo-sub");
    if (brandSub) brandSub.textContent = "AI MASTER HOME";
    const newChatIcon = query("#btnNewChat .btn-new-icon");
    if (newChatIcon) newChatIcon.replaceChildren(icon("chat"));
    const foot = element("div", "nxGlassSideBrand");
    foot.append(element("span", "", "nx-glass-mini-logo", "N"));
    const wordmark = element("span", "", "nx-glass-side-wordmark");
    wordmark.append(element("strong", "", "", "NEXUS"), element("small", "", "", "Master Home"));
    foot.append(wordmark, button("nxGlassHome", "Home", "online", () => navigate("home"), "nx-glass-home"));
    side.appendChild(foot);

    const originalNav = byId("sideNav");
    new MutationObserver(() => {
      const active = originalNav.querySelector("button.active");
      nav.querySelectorAll("[data-glass-view]").forEach(node => {
        node.classList.toggle("active", !!active && node.dataset.glassView === active.dataset.view);
      });
    }).observe(originalNav, { attributes: true, attributeFilter: ["class"], subtree: true });
  }

  function openSystemStatus() {
    const detail = byId("sideStatus");
    if (detail) detail.open = true;
    const panel = byId("nx-shell-panel");
    if (panel && panel.classList.contains("hide")) activate("btnNexusShell");
    if (detail) detail.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function makeHeader() {
    const top = byId("nxTop"), status = element("div", "nxGlassHeaderStatus");
    const chips = [
      ["Health", "brain", "HEALTH —", openSystemStatus]
    ];
    chips.forEach(([id, image, label, action]) => {
      status.appendChild(button("nxGlass" + id, label, image, action, "nx-glass-chip"));
    });
    top.insertBefore(status, byId("btnMasterOrders"));
    const systemControls = element("details", "nxGlassSystemControls");
    systemControls.append(element("summary", "", "", "Systemdetails"));
    const existingTools = element("div", "", "nx-glass-system-controls");
    ["statusChips", "btnMasterOrders", "btnChamber", "btnDesktopBg", "btnToggleRight"].forEach(id => {
      const node = byId(id);
      if (node) existingTools.appendChild(node);
    });
    systemControls.appendChild(existingTools);
    top.appendChild(systemControls);
    const tools = element("div", "nxGlassHeaderTools");
    tools.append(button("nxGlassActivity", "Aktivität", "bell", () => navigate("activity"), "nx-glass-tool"));
    const clock = element("time", "nxGlassClock");
    clock.append(element("span", "nxGlassDate"), element("strong", "nxGlassTime"));
    tools.appendChild(clock);
    top.appendChild(tools);
    const tickClock = () => {
      const now = new Date();
      clock.dateTime = now.toISOString();
      byId("nxGlassDate").textContent = now.toLocaleDateString("de-DE", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
      byId("nxGlassTime").textContent = now.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
    };
    tickClock();
    setInterval(tickClock, 1000);
    const health = byId("chipHealth");
    const syncHealth = () => {
      const text = health.textContent.replace(/^Health\s*/i, "HEALTH ");
      const node = byId("nxGlassHealth");
      node.querySelector(".nx-glass-button-label").textContent = text;
      node.dataset.quality = /unbekannt|—/i.test(text) ? "unknown" : health.classList.contains("ok") ? "ok" : "warn";
      node.title = "Quelle: /api/health · " + health.textContent;
    };
    syncHealth();
    new MutationObserver(syncHealth).observe(health, { childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ["class"] });
  }

  function makeDashboard() {
    const room = byId("nxAiRoom");
    const telemetry = element("section", "nxGlassTelemetry");
    telemetry.setAttribute("aria-label", "Echte Systemwerte");
    [["cpu", "System", "cpu"], ["gpu", "GPU", "gpu"], ["ram", "RAM", "ram"], ["disk", "Speicher", "disk"], ["network", "Netzwerk", "network"], ["ai", "Lokale KI", "brain"]].forEach(([id, title, image]) => {
      const node = element("button", "nxGlassMetric-" + id, "nx-glass-metric");
      node.type = "button";
      node.dataset.quality = "unknown";
      node.dataset.source = "/api/system/monitor";
      const heading = element("span", "", "nx-glass-metric-heading");
      heading.append(icon(image), element("span", "", "nx-glass-metric-title", title));
      const value = element("span", "nxGlassValue-" + id, "nx-glass-metric-value", "UNKNOWN");
      const detail = element("span", "nxGlassDetail-" + id, "nx-glass-metric-detail", "Messung ausstehend");
      const meter = element("span", "", "nx-glass-meter");
      meter.setAttribute("aria-hidden", "true");
      meter.append(element("i", "nxGlassBar-" + id));
      node.append(heading, value, detail, meter);
      node.addEventListener("click", () => navigate(id === "ai" ? "settings" : "perf"));
      telemetry.appendChild(node);
    });
    room.after(telemetry);
    const roomControls = element("details", "nxGlassRoomControls");
    roomControls.append(element("summary", "", "", "Raumsteuerung"));
    [query(".nx-agent-tools"), query(".nx-avatar-warning")].forEach(node => {
      if (node) roomControls.appendChild(node);
    });
    room.appendChild(roomControls);
    document.querySelectorAll(".nx-partners > .nx-partner").forEach(card => {
      const wave = element("span", "", "nx-glass-wave");
      wave.setAttribute("aria-hidden", "true");
      for (let index = 0; index < 15; index += 1) wave.appendChild(element("i"));
      card.lastElementChild.appendChild(wave);
    });
  }

  function makeComposer() {
    const attachment = byId("btnAttach");
    if (attachment) {
      attachment.title = "Projektdateien öffnen";
      attachment.setAttribute("aria-label", "Projektdateien öffnen");
      attachment.addEventListener("click", () => navigate("files"));
    }
    const box = query(".composer-box");
    const target = element("label", "nxGlassTarget");
    target.append(element("span", "", "", "Ziel:"));
    const select = element("select", "nxGlassTargetSelect");
    select.setAttribute("aria-label", "Gesprächspartner wählen");
    [["nexus", "NEXUS"], ["hermes", "HERMES"], ["gpt", "NEXUS GPT"], ["aether", "AETHER"]].forEach(([value, text]) => {
      const option = element("option", "", "", text);
      option.value = value;
      select.appendChild(option);
    });
    select.addEventListener("change", () => {
      const card = query('.nx-partner[data-partner="' + select.value + '"]');
      if (card) card.click();
    });
    target.appendChild(select);
    box.insertBefore(target, byId("btnMic"));
    const syncTarget = () => {
      const partner = window.NEXUS_PHASE2_PARTNER || "nexus";
      select.value = partner;
      const core = byId("nxGlassCore"); if (core) core.classList.toggle("active", partner === "nexus");
    };
    document.querySelectorAll(".nx-partner").forEach(card => card.addEventListener("click", syncTarget));
    syncTarget();
    new MutationObserver(syncTarget).observe(byId("nxRoomState"), { childList: true, characterData: true, subtree: true });
    [["btnAttach", "attach"], ["btnMic", "mic"], ["btnSend", "send"], ["btnLive", "live"], ["btnToggleRight", "more"]].forEach(([id, image]) => {
      const node = byId(id);
      if (node) node.replaceChildren(icon(image));
    });
    const actions = element("nav", "nxGlassQuickActions");
    actions.setAttribute("aria-label", "Chat vorbereiten und Werkzeuge öffnen");
    [
      ["task", "Neue Aufgabe erstellen", () => prepare("Erstelle eine neue Aufgabe: ")],
      ["analyze", "Datei analysieren", () => activate("btnAttach")],
      ["note", "Notiz anlegen", () => prepare("Merke dir: ")],
      ["weather", "Wetter heute", () => prepare("Wie ist das Wetter heute?")]
    ].forEach(([id, label, action]) => {
      const node = element("button", "nxGlassQuick-" + id, "nx-glass-quick", label);
      node.type = "button";
      node.addEventListener("click", action);
      if (["task", "note", "timer", "weather"].includes(id)) node.title = "Anfrage im Chat vorbereiten";
      actions.appendChild(node);
    });
    actions.appendChild(button("nxGlassMore", "Weitere Funktionen", "more", () => activate("btnExtras"), "nx-glass-quick nx-glass-more"));
    byId("nxComposer").appendChild(actions);
  }

  function makeReferenceFrame() {
    const app = byId("nxApp");
    if (!app || byId("nxReferenceStage")) return;
    const stage = element("div", "nxReferenceStage");
    app.before(stage);
    stage.appendChild(app);
    // Live telemetry sits directly above the composer, over the artwork's telemetry slot.
    const pinTelemetry = () => { const tl = byId("nxGlassTelemetry"), cp = byId("nxComposer"); if (tl && cp && tl.nextElementSibling !== cp) cp.before(tl); return !!(tl && cp); };
    if (!pinTelemetry()) setTimeout(pinTelemetry, 400);

    // The reference scene supplies decorative artwork. Its central console is
    // covered by the existing live application; these links open existing views.
    const scene = element("div", "nxReferenceScene");
    scene.dataset.artwork = "/static/assets/master-glass/reference-final/master-home-binding.png";
    scene.appendChild(element("h1", "", "nx-reference-sr-only", "NEXUS AI Master Home"));
    const cards = element("nav", "nxReferenceOuterCards");
    cards.setAttribute("aria-label", "NEXUS Funktionsbereiche");

    function artLink(parent, id, label, image, action, bounds, title) {
      const node = button(id, label, image, action, "nx-reference-art-link");
      node.replaceChildren(element("span", "", "nx-reference-sr-only", label));
      node.title = title || label;
      ["x", "y", "w", "h"].forEach((name, index) => {
        node.style.setProperty("--" + name, bounds[index] + "%");
      });
      parent.appendChild(node);
      return node;
    }

    [
      ["whatsapp", "WhatsApp Business öffnen", "whatsapp", osOpen("/api/os/launch_app", { name: "whatsapp" }), [1.3, 15.6, 17.5, 15.4], "WhatsApp"],
      ["facebook", "Facebook öffnen", "facebook", osOpen("/api/os/open_url", { url: "https://www.facebook.com/" }), [1.3, 32.4, 17.5, 15.4], "Facebook"],
      ["youtube", "YouTube öffnen", "youtube", osOpen("/api/os/open_url", { url: "https://www.youtube.com/" }), [1.3, 49.2, 17.5, 15.4], "YouTube"],
      ["studio", "NEXUS Studio öffnen", "studio", () => window.open("/studio", "_blank"), [1.3, 66, 17.5, 15.4], "NEXUS Studio"],
      ["library", "Skill- & Prompt-Bibliothek öffnen", "library", () => navigate("library"), [80.6, 63.2, 18.5, 17.0], "Skill- & Prompt-Bibliothek"],
      ["nexusall", "NEXUS All öffnen: Browser, Videoplayer und Downloader", "nexusall", () => window.open("/nexusall", "_blank"), [1.3, 84, 97.8, 9.5], "NEXUS All"],
      ["files", "Dateien im Explorer öffnen (G:\\)", "folder", osOpen("/api/os/open_path", { path: "G:\\", folder: true }), [80.6, 15.6, 18.5, 20.5], "Dateien"],
      ["mail", "E-Mail öffnen (Outlook, sonst Gmail)", "mail", osOpen("/api/os/launch_app", { name: "outlook" }), [80.6, 38.2, 18.5, 22.2], "E-Mail"]
    ].forEach(([id, label, image, action, bounds, name]) => {
      const card = artLink(cards, "nxReferenceCard-" + id, label, image, action, bounds);
      // Ursache leerer Kacheln: artLink rendert nur unsichtbaren Text ueber ein Hintergrundbild mit leeren Glasrahmen.
      if (name) {
        card.classList.add("nx-reference-app-tile");
        card.dataset.app = id;
        const logo = element("span", "", "nx-reference-app-logo");
        logo.setAttribute("aria-hidden", "true");
        logo.appendChild(icon(image));
        card.append(logo, element("span", "", "nx-reference-app-name", name), element("span", "", "nx-reference-app-state", ""));
      }
    });

    scene.append(cards);
    stage.after(scene);
  }

  function number(value) {
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  }

  function percentage(value) {
    const numeric = number(value);
    return numeric == null ? "UNKNOWN" : new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 }).format(numeric) + "%";
  }

  function gb(value) {
    const numeric = number(value);
    return numeric == null ? "UNKNOWN" : new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 }).format(numeric) + " GB";
  }

  function rate(value) {
    const numeric = number(value);
    if (numeric == null || numeric < 0) return "UNKNOWN";
    if (numeric >= 1048576) return (numeric / 1048576).toFixed(1).replace(".", ",") + " MB/s";
    if (numeric >= 1024) return (numeric / 1024).toFixed(1).replace(".", ",") + " KB/s";
    return Math.round(numeric) + " B/s";
  }

  function setChip(id, text, quality, title) {
    const node = byId("nxGlass" + id);
    if (!node) return;
    node.querySelector(".nx-glass-button-label").textContent = text;
    node.dataset.quality = quality;
    node.title = [value, detail].filter(Boolean).join(" \u00b7 ") + (title ? "\n" + title : ""); /* 20261102 Nexus-UI: voller Wert als Tooltip */
  }

  function setMetric(id, value, detail, pct, quality, title) {
    const node = byId("nxGlassMetric-" + id);
    byId("nxGlassValue-" + id).textContent = value;
    byId("nxGlassDetail-" + id).textContent = detail;
    node.dataset.quality = quality;
    node.title = [value, detail].filter(Boolean).join(" \u00b7 ") + (title ? "\n" + title : ""); /* 20261102 Nexus-UI: voller Wert als Tooltip */
    const measured = number(pct);
    node.classList.toggle("nx-glass-has-meter", measured != null);
    byId("nxGlassBar-" + id).style.width = (measured == null ? 0 : Math.min(100, Math.max(0, measured))) + "%";
  }

  function renderMonitor(sample) {
    const data = sample && sample.success !== false ? sample : {};
    const cpu = data.cpu || {}, ram = data.ram || {}, gpu = data.gpu || {};
    // Prefer the actual project drive measurement, with the primary disk fallback.
    const projectDisk = Array.isArray(data.disks) && data.disks.find(item =>
      String(item.mount || item.path || "").replace(/[\\/]+$/, "").toUpperCase() === "G:");
    const disk = projectDisk ? { ...projectDisk, path: projectDisk.mount || projectDisk.path } : data.disk || {};
    const graphic = gpu.available && gpu.gpus && gpu.gpus[0] || {};
    const network = data.network || {}, ollama = data.ollama || {};
    const source = field => "Quelle: /api/system/monitor · " + (field.source || "Monitor") + "\nStand: " + (data.timestamp || "UNKNOWN") + (field.note ? "\n" + field.note : "");
    setChip("Cpu", "CPU " + percentage(cpu.percent), number(cpu.percent) == null ? "unknown" : "ok", source(cpu));
    setChip("Gpu", "GPU " + percentage(graphic.percent), number(graphic.percent) == null ? "unknown" : "ok", source(gpu));
    setChip("Ram", "RAM " + percentage(ram.percent), number(ram.percent) == null ? "unknown" : "ok", source(ram));
    setMetric("cpu", "CPU", percentage(cpu.percent), cpu.percent, number(cpu.percent) == null ? "unknown" : "ok", source(cpu));
    setMetric("gpu", number(graphic.percent) != null ? percentage(graphic.percent) : (graphic.name ? String(graphic.name).replace(/^(AMD|NVIDIA|Intel)\(R\)?\s*/i, "").replace(/Radeon\s*/i, "") : "\u2013"), number(graphic.percent) != null ? (graphic.name || "") : "Auslastung nicht messbar", graphic.percent, number(graphic.percent) == null ? "unknown" : "ok", source(gpu));
    setMetric("ram", gb(ram.total_gb), percentage(ram.percent), ram.percent, number(ram.percent) == null ? "unknown" : "ok", source(ram));
    setMetric("disk", (disk.path ? disk.path.replace(/[\\/]+$/, "") + " " : "") + gb(disk.free_gb) + " frei", percentage(disk.percent) + " belegt", disk.percent, number(disk.free_gb) == null ? "unknown" : "ok", source(disk));
    const up = (network.interfaces || []).filter(iface => iface.up === true);
    const name = up.length ? up[0].name : network.source ? "Netzwerk" : "UNKNOWN";
    const rates = "↑ " + rate(network.bytes_sent_rate) + "   ↓ " + rate(network.bytes_recv_rate);
    const networkKnown = number(network.bytes_sent_rate) != null && number(network.bytes_recv_rate) != null;
    setMetric("network", name, rates, null, networkKnown ? "ok" : "unknown", source(network));
    const reachable = ollama.reachable;
    setMetric("ai", ollama.preferred ? "Hermes \u00b7 " + ollama.preferred : "Modell UNKNOWN", reachable === true ? "ONLINE" : reachable === false ? "OFFLINE" : "UNKNOWN", null, reachable === true ? "ok" : reachable === false ? "warn" : "unknown", source(ollama) + (ollama.error ? "\n" + ollama.error : ""));
    byId("nxGlassTelemetry").dataset.checkedAt = data.timestamp || "";
    byId("nxGlassTelemetry").dataset.available = String(!!data.timestamp);
  }

  function renderSystem(data) {
    const fresh = !!data && !data.stale;
    const core = fresh && data.components && data.components.CORE;
    const state = core && (core.state || core.status) || "UNKNOWN";
    const mode = fresh && data.OPERATING_MODE || "UNKNOWN";
    const tip = "Quelle: /api/nexus-os/status\nStand: " + (data && data.timestamp || "UNKNOWN") + (core && core.reason ? "\n" + core.reason : "") + (data && data.stale ? "\nMessung veraltet" : "");
    setChip("Online", state, state === "ONLINE" ? "ok" : state === "UNKNOWN" ? "unknown" : "warn", tip);
    setChip("Mode", "MODE " + mode, mode === "FULL" ? "ok" : mode === "UNKNOWN" ? "unknown" : "warn", tip);
  }

  async function readJson(url) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(url, { cache: "no-store", signal: controller.signal });
      if (!response.ok) return null;
      return await response.json();
    } catch (_) {
      return null;
    } finally {
      clearTimeout(timeout);
    }
  }

  let lastMonitor = null; // 20261102 Nexus-UI: Fehlversuch nicht als UNKNOWN stehen lassen
  let refreshing = false;
  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    try {
      await Promise.all([
        readJson("/api/system/monitor").then(d => { if (d) { lastMonitor = d; renderMonitor(d); } else { if (!lastMonitor) renderMonitor(null); setTimeout(() => { if (!document.hidden) refresh(); }, 3000); } }),
        readJson("/api/nexus-os/status").then(renderSystem)
      ]);
    } finally {
      refreshing = false;
    }
  }

  function init() {
    if (!byId("nxAiRoom") || byId("nxGlassNav")) return;
    document.body.classList.add("nx-glass-master", "nx-reference");
    makeNav();
    makeHeader();
    makeDashboard();
    makeComposer();
    makeReferenceFrame();
    // Start with animated reference portraits through the existing view switch.
    // The requested 3D viewer stays available without changing its controller.
    const viewToggle = byId("btnVrmView");
    if (viewToggle && viewToggle.textContent === "Originalporträts anzeigen") viewToggle.click();
    renderMonitor(null);
    renderSystem(null);
    refresh();
    setInterval(() => { if (!document.hidden) refresh(); }, 15000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
    window.NEXUS_GLASS = Object.freeze({ refresh });
  }

  /* 20261102 Nexus-UI: Szene selten nicht gebaut -> Start wiederholen, bis #nxReferenceScene steht; Grund in body[data-glass-boot] */
  function boot(n) {
    let why = "ok";
    try {
      if (!byId("nxAiRoom")) why = "kein nxAiRoom";
      else if (!byId("nxGlassNav")) init();
      else if (!byId("nxReferenceScene")) { why = "nachgebaut"; makeReferenceFrame(); }
    } catch (e) { why = "fehler: " + (e && e.message || e); try { console.error("master_glass boot", e); } catch (_) {} }
    const ok = !!byId("nxReferenceScene");
    if (document.body) document.body.dataset.glassBoot = (ok ? "ok" : "warte") + " #" + n + " " + why;
    if (!ok && n < 40) setTimeout(() => boot(n + 1), n < 10 ? 150 : 500);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => boot(0));
  else boot(0);
  window.addEventListener("load", () => { if (!byId("nxReferenceScene")) boot(100); });
})();
