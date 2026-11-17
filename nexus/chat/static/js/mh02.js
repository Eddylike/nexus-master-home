/* mh02 (Nexus-UI, 17.11.2026): Master Home Startseite im Glas-Look (Schritt 6).
   Nur Frontend, nur GET-Endpunkte, keine erfundenen Werte: fehlt etwas, steht ein ehrlicher Leerzustand da.
   Rueckweg: ?mh=alt schaltet alles ab (alte Ansicht). Focus-Timer laeuft rein lokal (localStorage). */
(function () {
  "use strict";
  if (/[?&]mh=alt(&|$)/.test(location.search || "")) { document.documentElement.setAttribute("data-mh02", "alt"); return; }

  var byId = function (id) { return document.getElementById(id); };
  var NS = "http://www.w3.org/2000/svg";
  var ICONS = {
    home: "M3 11 12 4l9 7M5 10v10h14V10M10 20v-6h4v6",
    chat: "M21 12a8 8 0 0 1-11.6 7.1L4 20l1.1-4.6A8 8 0 1 1 21 12Z",
    agent: "M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM5 21v-2a7 7 0 0 1 14 0v2",
    tasks: "M9 6h11M9 12h11M9 18h11M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2",
    files: "M3 7V5h6l2 2h10v12H3V7Z",
    project: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
    library: "M12 6C10 4 7 4 4 5v14c3-1 6-1 8 1 2-2 5-2 8-1V5c-3-1-6-1-8 1ZM12 6v14",
    skills: "M13 2 4 14h7l-1 8 9-12h-7l1-8Z",
    notes: "M15 4l5 5M3 21l5-1L21 7l-4-4L4 16l-1 5Z",
    music: "M9 18V5l11-2v13M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0ZM20 16a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z",
    video: "M3 6h13v12H3zM16 10l5-3v10l-5-3",
    gallery: "M3 5h18v14H3zM3 16l5-5 4 4 3-3 6 6M15 9h.01",
    system: "M6 6h12v12H6zM9 9h6v6H9zM9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4",
    settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM19 12l2-1-1-3-2 .3-1.4-1.4L17 5l-3-1-1 2h-2l-1-2-3 1 .3 2L6 8.4 4 8 3 11l2 1v0l-2 1 1 3 2-.3 1.4 1.4L7 19l3 1 1-2h2l1 2 3-1-.3-2 1.4-1.4 2 .4 1-3-2-1Z",
    live: "M4 10v4M8 6v12M12 3v18M16 6v12M20 10v4",
    terminal: "M4 5h16v14H4zM7 9l3 3-3 3M12 15h5",
    activity: "M3 12h4l3-8 4 16 3-8h4",
    apps: "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z",
    download: "M12 3v12M7 10l5 5 5-5M4 21h16",
    voice: "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3ZM5 11a7 7 0 0 0 14 0M12 18v3",
    studio: "M3 5h18v14H3zM8 10h.01M4 17l5-5 3 3 3-3 5 5",
    room: "M4 5h16v11H8l-4 4V5ZM8 9h8M8 12h5",
    calendar: "M3 5h18v16H3zM3 10h18M8 3v4M16 3v4",
    timer: "M12 22a8 8 0 1 0 0-16 8 8 0 0 0 0 16ZM12 10v4l3 2M9 2h6",
    contacts: "M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM4 21v-1a8 8 0 0 1 16 0v1"
  };
  function icon(name) {
    var s = document.createElementNS(NS, "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("aria-hidden", "true"); s.setAttribute("class", "mh02-ico");
    var p = document.createElementNS(NS, "path"); p.setAttribute("d", ICONS[name] || ICONS.apps);
    s.appendChild(p); return s;
  }
  function el(tag, cls, text, id) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (id) n.id = id;
    if (text != null) n.textContent = text;
    return n;
  }
  function btn(cls, title, sub, ic, action, id) {
    var b = el("button", cls, null, id); b.type = "button";
    if (ic) b.appendChild(icon(ic));
    var t = el("span", "mh02-btn-text");
    t.appendChild(el("strong", "", title));
    if (sub) t.appendChild(el("small", "", sub));
    b.appendChild(t);
    b.addEventListener("click", function (ev) { try { action(ev); } catch (e) { console.warn("mh02 Aktion", e); } });
    return b;
  }

  /* ---- echte Ansichten oeffnen (wie master_glass.js) ---- */
  function navigate(view) {
    var orig = document.querySelector('#sideNav button[data-view="' + view + '"]');
    var more = byId("sideMore"), wasOpen = more && more.open;
    if (orig) orig.click();
    if (more && !wasOpen) more.open = false;
    return !!orig;
  }
  function activate(id) { var n = byId(id); if (n) n.click(); return !!n; }
  function openPage(path) { window.open(path, "_blank", "noopener"); }
  function askChat(text) {
    navigate("chat");
    var inp = byId("nxInput"); if (!inp) return;
    inp.value = text; inp.dispatchEvent(new Event("input", { bubbles: true })); inp.focus();
  }
  function chooseAgent(value) {
    var sel = byId("nxGlassTargetSelect");
    if (!sel) return;
    sel.value = value; sel.dispatchEvent(new Event("change", { bubbles: true }));
    var inp = byId("nxInput"); if (inp) inp.focus();
  }

  async function getJson(url) {
    var c = new AbortController(), t = setTimeout(function () { c.abort(); }, 12000);
    try {
      var r = await fetch(url, { cache: "no-store", signal: c.signal });
      if (!r.ok) return { __err: "HTTP " + r.status };
      return await r.json();
    } catch (e) { return { __err: "nicht erreichbar" }; } finally { clearTimeout(t); }
  }
  function fmtTime(ts) {
    var d = new Date(ts); if (isNaN(d)) return String(ts || "");
    return d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  }
  function fmtDateTime(ts) {
    var d = new Date(ts); if (isNaN(d)) return String(ts || "");
    return d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" }) + " " + fmtTime(ts);
  }
  function empty(box, text) { box.replaceChildren(el("p", "mh02-empty", text)); }
  function errState(box, url, res) { box.replaceChildren(el("p", "mh02-empty mh02-err", "Keine Daten: GET " + url + " " + (res && res.__err || "fehlgeschlagen"))); }

  /* ---- Sidebar: Gruppen ---- */
  function navButton(id, label, ic, action, view) {
    var b = el("button", "nx-glass-nav-button mh02-nav-extra", null, id); b.type = "button";
    b.setAttribute("aria-label", label);
    var i = el("span", "nx-glass-icon"); i.setAttribute("aria-hidden", "true"); i.appendChild(icon(ic));
    b.append(i, el("span", "nx-glass-button-label", label));
    if (view) b.dataset.glassView = view;
    b.addEventListener("click", action);
    return b;
  }
  function groupNav() {
    var nav = byId("nxGlassNav");
    if (!nav || nav.dataset.mh02) return !!nav;
    nav.dataset.mh02 = "1";
    var g = function (id) { return byId("nxGlassNav-" + id); };
    var extra = {
      start: navButton("nxGlassNav-mh02start", "Startseite", "home", function () { showHome(true); }),
      projects: navButton("nxGlassNav-mh02projects", "Projekte", "project", function () { navigate("projects"); }, "projects"),
      livetalk: navButton("nxGlassNav-mh02livetalk", "Live Talk", "voice", function () { openPage("/livetalk"); }),
      library: navButton("nxGlassNav-mh02library", "Bibliothek", "library", function () { navigate("library"); }, "library"),
      music: navButton("nxGlassNav-mh02music", "Musik", "music", function () { navigate("music"); }, "music"),
      videos: navButton("nxGlassNav-mh02videos", "Videos", "video", function () { navigate("videos"); }, "videos")
    };
    extra.livetalk.title = "Live Talk in neuem Tab (/livetalk)";
    var groups = [
      ["Zentrale", [extra.start, g("chats"), g("agents")]],
      ["Arbeitsbereiche", [g("tasks"), g("files"), g("notes"), extra.projects, g("calendar"), g("timer")]],
      ["Kommunikation", [g("contacts"), extra.livetalk]],
      ["Kreativit\u00e4t", [extra.library, extra.music, extra.videos]],
      ["Verwaltung", [g("system"), g("devices"), g("network"), g("apps"), g("settings")]]
    ];
    var anchor = byId("nxSideSystem");
    if (anchor && anchor.parentElement !== nav) anchor = null;
    groups.forEach(function (grp) {
      var h = el("div", "mh02-nav-group", grp[0]); h.setAttribute("role", "presentation");
      nav.insertBefore(h, anchor);
      grp[1].forEach(function (b) { if (b) { b.classList.remove("nx-glass-nav-section"); nav.insertBefore(b, anchor); } });
    });
    return true;
  }

  /* ---- Startseite ---- */
  var home, forced = false, touched = false;
  var TILES = [
    ["crew", "Agenten", "Crew & Spezialisten", "agent", function () { navigate("crew"); }],
    ["tasks", "Auftr\u00e4ge", "Hermes-Auftr\u00e4ge & Status", "tasks", function () { activate("btnMasterOrders"); }],
    ["files", "Dateien", "Projektordner durchsuchen", "files", function () { navigate("files"); }],
    ["projects", "Projekte", "Projekte verwalten", "project", function () { navigate("projects"); }],
    ["library", "Bibliothek", "Skills & Prompts", "library", function () { navigate("library"); }],
    ["skills", "F\u00e4higkeiten", "Nachgewiesene Funktionen", "skills", function () { navigate("skills"); }],
    ["memory", "Memory", "Notizen & Gemerktes", "notes", function () { navigate("memory"); }],
    ["music", "Musik", "Lokaler Player & Bibliothek", "music", function () { navigate("music"); }],
    ["videos", "Videos", "Video-Bibliothek", "video", function () { navigate("videos"); }],
    ["galerie", "Galerie", "Bilder & Erzeugtes", "gallery", function () { navigate("galerie"); }],
    ["perf", "System", "Leistung & Prozesse", "system", function () { navigate("perf"); }],
    ["settings", "Einstellungen", "Modelle & Optionen", "settings", function () { navigate("settings"); }]
  ];
  var QUICK = [
    ["livehub", "Live-Hub", "Agenten-Ereignisse", "live", function () { activate("btnLiveHub"); }],
    ["terminal", "Terminal", "Live-Konsole", "terminal", function () { activate("btnTerminal"); }],
    ["homehub", "Home-Hub", "Alle Bereiche", "home", function () { navigate("home"); }],
    ["activity", "Aktivit\u00e4t", "Verlauf & Ereignisse", "activity", function () { navigate("activity"); }],
    ["apps", "Apps", "Programme & Dienste", "apps", function () { navigate("apps"); }],
    ["downloads", "Downloads", "Legale Downloads", "download", function () { navigate("downloads"); }],
    ["voice", "Voice", "Sprachstatus", "voice", function () { navigate("voice"); }],
    ["studio", "Studio", "Neuer Tab /studio", "studio", function () { openPage("/studio"); }],
    ["kommando", "Kommandozentrale", "Neuer Tab /kommando", "room", function () { openPage("/kommando"); }]
  ];
  var TEAM = [
    ["nexus", "NEXUS", "Zentrale \u00b7 KI-Hub \u00b7 Steuerung", "NEXUS"],
    ["hermes", "HERMES", "Planung \u00b7 Ausf\u00fchrung \u00b7 Werkzeuge", "Hermes"],
    ["gpt", "NEXUS GPT", "Chat \u00b7 Analyse \u00b7 Wissen", "NEXUS-GPT"],
    ["aether", "AETHER", "Forschungs-Partner \u00b7 separat", "AETHER"]
  ];

  function card(cls, title, ic, extraHead) {
    var c = el("article", "mh02-card " + (cls || ""));
    var h = el("header", "mh02-card-head");
    if (ic) h.appendChild(icon(ic));
    h.appendChild(el("h3", "", title));
    if (extraHead) h.appendChild(extraHead);
    c.appendChild(h);
    return c;
  }
  function linkBtn(text, action, id) {
    var b = el("button", "mh02-link", text, id); b.type = "button"; b.addEventListener("click", action); return b;
  }

  function greetText(d) {
    var h = d.getHours();
    return (h >= 4 && h < 11 ? "Guten Morgen" : h >= 11 && h < 18 ? "Guten Tag" : "Guten Abend") + ", Eddy";
  }
  function build() {
    home = el("section", "", null, "mh02Home");
    home.setAttribute("aria-label", "Master Home Startseite");

    /* Reihe 1: Begruessung, Focus-Timer, Kalender */
    var top = el("div", "mh02-row mh02-row-top");
    var hero = el("article", "mh02-card mh02-hero");
    hero.append(el("p", "mh02-kicker", "NEXUS \u00b7 AI MASTER HOME"), el("h2", "", "", "mh02Greet"), el("p", "mh02-date", "", "mh02Date"));
    var stats = el("div", "mh02-stats", null, "mh02Stats");
    [["health", "Health"], ["model", "Modell"], ["livetalk", "Live Talk"]].forEach(function (s) {
      var x = el("div", "mh02-stat", null, "mh02Stat-" + s[0]);
      x.append(el("span", "mh02-stat-label", s[1]), el("strong", "mh02-stat-value", "\u2026"));
      stats.appendChild(x);
    });
    hero.appendChild(stats);
    var back = linkBtn("Zum Chat-Verlauf", function () { showHome(false); }, "mh02Back");
    hero.appendChild(back);
    top.appendChild(hero);

    var focus = card("mh02-focus", "Focus-Timer", "timer");
    focus.querySelector("header").appendChild(el("span", "mh02-chip", "lokal", "mh02FocusMode"));
    focus.append(el("div", "mh02-focus-time", "25:00", "mh02FocusTime"), el("div", "mh02-focus-bar", null, "mh02FocusBarWrap"));
    focus.querySelector(".mh02-focus-bar").appendChild(el("i", "", null, "mh02FocusBar"));
    var presets = el("div", "mh02-focus-presets");
    [[25, "25 min"], [50, "50 min"], [5, "5 min Pause"]].forEach(function (p) {
      var b = el("button", "mh02-pill", p[1]); b.type = "button"; b.dataset.min = p[0];
      b.addEventListener("click", function () { focusSet(p[0]); });
      presets.appendChild(b);
    });
    var ctl = el("div", "mh02-focus-ctl");
    var start = el("button", "mh02-primary", "Fokus starten", "mh02FocusStart"); start.type = "button";
    start.addEventListener("click", focusToggle);
    var reset = el("button", "mh02-pill", "Zur\u00fccksetzen", "mh02FocusReset"); reset.type = "button";
    reset.addEventListener("click", function () { focusSet(focusState().min); });
    ctl.append(start, reset);
    focus.append(presets, ctl);
    top.appendChild(focus);

    var cal = card("mh02-cal", "Kalender", "calendar", el("span", "mh02-chip", "", "mh02CalMonth"));
    cal.append(el("div", "mh02-month", null, "mh02Month"), el("h4", "", "Termine"), el("div", "mh02-list", null, "mh02CalList"),
      linkBtn("Termine im Chat abfragen", function () { askChat("Zeige mir meine Kalendertermine."); }));
    top.appendChild(cal);
    home.appendChild(top);

    /* Schnellzugriff */
    var quick = card("mh02-quick", "Schnellzugriff", "skills");
    var qg = el("div", "mh02-quick-grid");
    QUICK.forEach(function (q) { qg.appendChild(btn("mh02-quick-btn", q[1], q[2], q[3], q[4], "mh02Quick-" + q[0])); });
    quick.appendChild(qg);
    home.appendChild(quick);

    /* 12 Hauptkacheln */
    var tiles = card("mh02-tiles-card", "Hauptbereiche", "apps");
    var tg = el("div", "mh02-tiles", null, "mh02Tiles");
    TILES.forEach(function (t) { var b = btn("mh02-tile", t[1], t[2], t[3], t[4], "mh02Tile-" + t[0]); b.dataset.target = t[0]; tg.appendChild(b); });
    tiles.appendChild(tg);
    home.appendChild(tiles);

    /* KI-Team */
    var team = card("mh02-team-card", "KI-Team", "agent");
    var tm = el("div", "mh02-team", null, "mh02Team");
    TEAM.forEach(function (a) {
      var c = el("article", "mh02-agent", null, "mh02Agent-" + a[0]);
      var head = el("div", "mh02-agent-head");
      head.append(el("span", "mh02-dot"), el("strong", "", a[1]));
      c.append(head, el("p", "mh02-agent-role", a[2]),
        el("p", "mh02-agent-line mh02-agent-status", "Status: \u2026"),
        el("p", "mh02-agent-line mh02-agent-rights", "Rechte: \u2026"),
        el("p", "mh02-agent-line mh02-agent-last", "Letzte Aktivit\u00e4t: \u2026"));
      var b = el("button", "mh02-pill mh02-agent-btn", a[1] + " ansprechen"); b.type = "button";
      b.addEventListener("click", function () { chooseAgent(a[0]); });
      c.appendChild(b);
      tm.appendChild(c);
    });
    team.appendChild(tm);
    home.appendChild(team);

    /* Faehigkeiten, Projekte, Workflows */
    var r2 = el("div", "mh02-row mh02-row-3");
    var caps = card("", "F\u00e4higkeiten", "skills", el("span", "mh02-chip", "", "mh02CapSum"));
    caps.append(el("div", "mh02-list", null, "mh02CapList"), linkBtn("Alle F\u00e4higkeiten", function () { navigate("skills"); }));
    var proj = card("", "Projekte", "project", el("span", "mh02-chip", "", "mh02ProjSum"));
    proj.append(el("div", "mh02-list", null, "mh02ProjList"), linkBtn("Projekte verwalten", function () { navigate("projects"); }));
    var wf = card("", "Workflows", "library", el("span", "mh02-chip", "", "mh02WfSum"));
    wf.append(el("div", "mh02-list", null, "mh02WfList"), linkBtn("Workflow-Bibliothek \u00f6ffnen", function () { navigate("library"); }));
    r2.append(caps, proj, wf);
    home.appendChild(r2);

    /* Aufgaben, Auftraege, Sprechzimmer */
    var r3 = el("div", "mh02-row mh02-row-3");
    var tasks = card("", "Aufgaben", "tasks", el("span", "mh02-chip", "", "mh02TaskSum"));
    tasks.append(el("div", "mh02-list", null, "mh02TaskList"), linkBtn("Aufgabe im Chat anlegen", function () { askChat("Erstelle eine neue Aufgabe: "); }));
    var orders = card("", "Hermes-Auftr\u00e4ge", "terminal", el("span", "mh02-chip", "", "mh02OrderSum"));
    orders.append(el("div", "mh02-list", null, "mh02OrderList"), linkBtn("Auftragsliste \u00f6ffnen", function () { activate("btnMasterOrders"); }));
    var room = card("", "Sprechzimmer", "room", el("span", "mh02-chip", "", "mh02RoomSum"));
    room.append(el("div", "mh02-list", null, "mh02RoomList"), linkBtn("Sprechzimmer \u00f6ffnen (/kommando)", function () { openPage("/kommando"); }));
    r3.append(tasks, orders, room);
    home.appendChild(r3);

    home.appendChild(el("p", "mh02-foot", "Alle Werte kommen live aus dem NEXUS-Core (nur Lesezugriffe). Alte Ansicht: Adresse mit ?mh=alt \u00f6ffnen."));
    return home;
  }

  /* ---- Focus-Timer (lokal) ---- */
  var FKEY = "nexus_mh02_focus";
  function focusState() {
    try { var s = JSON.parse(localStorage.getItem(FKEY) || "null"); if (s && s.min) return s; } catch (e) {}
    return { min: 25, left: 1500, endAt: 0 };
  }
  function focusSave(s) { try { localStorage.setItem(FKEY, JSON.stringify(s)); } catch (e) {} }
  function focusSet(min) { focusSave({ min: min, left: min * 60, endAt: 0 }); focusRender(); }
  function focusToggle() {
    var s = focusState();
    if (s.endAt) { s.left = Math.max(0, Math.round((s.endAt - Date.now()) / 1000)); s.endAt = 0; }
    else { if (s.left <= 0) s.left = s.min * 60; s.endAt = Date.now() + s.left * 1000; }
    focusSave(s); focusRender();
  }
  function focusRender() {
    var t = byId("mh02FocusTime"); if (!t) return;
    var s = focusState(), left = s.endAt ? Math.max(0, Math.round((s.endAt - Date.now()) / 1000)) : s.left;
    if (s.endAt && left <= 0) { s.endAt = 0; s.left = 0; focusSave(s); }
    t.textContent = String(Math.floor(left / 60)).padStart(2, "0") + ":" + String(left % 60).padStart(2, "0");
    var total = s.min * 60 || 1;
    byId("mh02FocusBar").style.width = (100 - Math.round(left / total * 100)) + "%";
    byId("mh02FocusStart").textContent = s.endAt ? "Pausieren" : left <= 0 ? "Neu starten" : left < total ? "Fortsetzen" : "Fokus starten";
    byId("mh02FocusMode").textContent = s.endAt ? "l\u00e4uft" : left <= 0 ? "fertig" : "bereit";
    home.querySelectorAll(".mh02-focus-presets .mh02-pill").forEach(function (b) { b.classList.toggle("active", +b.dataset.min === s.min); });
  }

  /* ---- Uhr / Begruessung / Monatsblatt ---- */
  var calEvents = [];
  function tick() {
    var d = new Date();
    byId("mh02Greet").textContent = greetText(d);
    byId("mh02Date").textContent = d.toLocaleDateString("de-DE", { weekday: "long", day: "numeric", month: "long", year: "numeric" }) + " \u00b7 " + d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
    focusRender();
  }
  function eventDate(e) { var v = e && (e.start || e.date || e.datetime || e.when || e.time); var d = v ? new Date(v) : null; return d && !isNaN(d) ? d : null; }
  function renderMonth() {
    var box = byId("mh02Month"), now = new Date(), y = now.getFullYear(), m = now.getMonth();
    byId("mh02CalMonth").textContent = now.toLocaleDateString("de-DE", { month: "long", year: "numeric" });
    var frag = document.createDocumentFragment();
    ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"].forEach(function (w) { frag.appendChild(el("span", "mh02-wd", w)); });
    var first = (new Date(y, m, 1).getDay() + 6) % 7, days = new Date(y, m + 1, 0).getDate();
    for (var i = 0; i < first; i++) frag.appendChild(el("span", "mh02-day mh02-pad", ""));
    var marked = {};
    calEvents.forEach(function (e) { var d = eventDate(e); if (d && d.getFullYear() === y && d.getMonth() === m) marked[d.getDate()] = 1; });
    for (var dnum = 1; dnum <= days; dnum++) {
      var c = el("span", "mh02-day" + (dnum === now.getDate() ? " mh02-today" : "") + (marked[dnum] ? " mh02-has" : ""), String(dnum));
      frag.appendChild(c);
    }
    box.replaceChildren(frag);
  }
  /* ---- Daten laden (nur GET) ---- */
  function setStat(id, value, title) {
    var n = byId("mh02Stat-" + id); if (!n) return;
    n.querySelector(".mh02-stat-value").textContent = value; n.title = title || "";
  }
  function li(main, sub, state) {
    var row = el("div", "mh02-item" + (state ? " mh02-" + state : ""));
    row.appendChild(el("span", "mh02-item-main", main));
    if (sub) row.appendChild(el("span", "mh02-item-sub", sub));
    return row;
  }
  function normAgent(a) {
    a = String(a || "").toLowerCase().replace(/[\s_]+/g, "-");
    if (a === "nexus") return "nexus";
    if (a === "hermes") return "hermes";
    if (a === "nexus-gpt" || a === "gpt") return "gpt";
    if (a === "aether") return "aether";
    return "";
  }

  /* Status je Agent: dieselbe Anzeige, die phase2_room.js aus /api/health, /api/hermes/health, /api/aether/health fuellt */
  function syncAgentStatus(id) {
    var c = byId("mh02Agent-" + id), ps = byId("nxPartnerStatus-" + id);
    if (!c) return;
    var st = ps ? (ps.textContent || "").trim() : "";
    c.querySelector(".mh02-agent-status").textContent = "Status: " + (st || "unbekannt");
    c.dataset.state = ps && ps.classList.contains("ok") ? "ok" : ps && ps.classList.contains("err") ? "err" : "unknown";
  }

  var loading = false;
  async function refresh() {
    if (loading || !home || home.hidden || document.hidden) return;
    loading = true;
    try {
      var r = await Promise.all([
        getJson("/api/health"), getJson("/api/livetalk/status"), getJson("/api/calendar"),
        getJson("/api/capabilities/evidence"), getJson("/api/projects"), getJson("/api/library"),
        getJson("/api/tasks"), getJson("/api/orders"), getJson("/api/sprechzimmer/sessions"),
        getJson("/api/agents/rights"), getJson("/api/live/config"), getJson("/api/live/events")
      ]);
      var health = r[0], lt = r[1], cal = r[2], cap = r[3], proj = r[4], lib = r[5], tasks = r[6], orders = r[7], rooms = r[8], rights = r[9], lcfg = r[10], lev = r[11];

      if (health.__err) { setStat("health", "\u2013", "GET /api/health " + health.__err); setStat("model", "\u2013", ""); }
      else {
        setStat("health", (health.score != null ? health.score : "\u2013") + (health.ok ? " \u00b7 OK" : ""), "Quelle: GET /api/health");
        var o = health.ollama || {};
        setStat("model", o.model || "\u2013", "Quelle: GET /api/health \u00b7 Ollama " + (o.reachable ? "erreichbar" : "nicht erreichbar"));
        byId("mh02Stat-health").dataset.state = health.ok ? "ok" : "warn";
      }
      if (lt.__err) setStat("livetalk", "\u2013", "GET /api/livetalk/status " + lt.__err);
      else setStat("livetalk", (lt.active_sessions || 0) + (lt.active_sessions === 1 ? " Sitzung" : " Sitzungen"), "Quelle: GET /api/livetalk/status");

      var cl = byId("mh02CalList");
      if (cal.__err) { calEvents = []; errState(cl, "/api/calendar", cal); }
      else {
        calEvents = (cal.events || []).slice();
        var upcoming = calEvents.map(function (e) { return { e: e, d: eventDate(e) }; })
          .filter(function (x) { return !x.d || x.d >= new Date(Date.now() - 3600e3); })
          .sort(function (a, b) { return (a.d || 0) - (b.d || 0); }).slice(0, 4);
        if (!upcoming.length) empty(cl, "Keine Termine");
        else cl.replaceChildren.apply(cl, upcoming.map(function (x) { var e = x.e; return li(e.title || e.summary || e.name || e.text || "Termin", x.d ? fmtDateTime(x.d) : ""); }));
      }
      renderMonth();

      var capL = byId("mh02CapList");
      if (cap.__err) { errState(capL, "/api/capabilities/evidence", cap); byId("mh02CapSum").textContent = "\u2013"; }
      else {
        var list = cap.capabilities || [], ready = list.filter(function (c) { return c.ready === true; });
        byId("mh02CapSum").textContent = ready.length + " / " + list.length + " nachgewiesen";
        if (!list.length) empty(capL, "Keine F\u00e4higkeiten gemeldet");
        else capL.replaceChildren.apply(capL, list.slice(0, 7).map(function (c) {
          return li(c.name, c.ready === true ? "nutzbar" : c.registered ? "registriert" : "nicht angebunden", c.ready === true ? "ok" : c.registered ? "warn" : "off");
        }));
      }

      var pl = byId("mh02ProjList");
      if (proj.__err) { errState(pl, "/api/projects", proj); byId("mh02ProjSum").textContent = "\u2013"; }
      else {
        var ps = proj.projects || [];
        byId("mh02ProjSum").textContent = ps.length + (ps.length === 1 ? " Projekt" : " Projekte");
        if (!ps.length) empty(pl, "Keine Projekte");
        else pl.replaceChildren.apply(pl, ps.slice(0, 5).map(function (p) {
          return li(p.name || p.id, (p.id === proj.active ? "aktiv \u00b7 " : "") + (p.description || "") , p.id === proj.active ? "ok" : "");
        }));
      }

      var wl = byId("mh02WfList");
      if (lib.__err) { errState(wl, "/api/library", lib); byId("mh02WfSum").textContent = "\u2013"; }
      else {
        var flows = (lib.items || []).filter(function (i) { return Array.isArray(i.pipeline) && i.pipeline.length; });
        byId("mh02WfSum").textContent = flows.length + " Abl\u00e4ufe";
        if (!flows.length) empty(wl, "Keine Workflows hinterlegt");
        else wl.replaceChildren.apply(wl, flows.slice(0, 6).map(function (f) {
          return li(f.titel || f.id, f.pipeline.length + (f.pipeline.length === 1 ? " Schritt" : " Schritte") + (f.kategorie ? " \u00b7 " + f.kategorie : ""));
        }));
      }

      var tl = byId("mh02TaskList");
      if (tasks.__err) { errState(tl, "/api/tasks", tasks); byId("mh02TaskSum").textContent = "\u2013"; }
      else {
        var ts = tasks.tasks || [];
        var open = ts.filter(function (t) { return !/done|erledigt|completed/i.test(String(t.status || "")); });
        byId("mh02TaskSum").textContent = open.length + " offen";
        if (!ts.length) empty(tl, "Keine Aufgaben");
        else tl.replaceChildren.apply(tl, ts.slice(0, 5).map(function (t) { return li(t.title || t.text || t.name || t.id || "Aufgabe", String(t.status || "")); }));
      }

      var ol = byId("mh02OrderList");
      if (orders.__err) { errState(ol, "/api/orders", orders); byId("mh02OrderSum").textContent = "\u2013"; }
      else {
        var os = orders.orders || [], act = orders.active || null;
        byId("mh02OrderSum").textContent = os.length + " gesamt";
        var rows = [];
        if (act && act.status) rows.push(li("Aktueller Lauf: " + act.status, act.error ? String(act.error).slice(0, 80) : (act.phase || ""), /BLOCK|FAIL|ERR/i.test(act.status) ? "warn" : "ok"));
        os.slice(0, 3).forEach(function (o) {
          rows.push(li(String(o.status || "unbekannt") + (o.total ? " \u00b7 " + (o.completed || 0) + "/" + o.total : ""), o.updated_at ? "Stand " + fmtDateTime(o.updated_at) : ""));
        });
        if (!rows.length) empty(ol, "Keine Auftr\u00e4ge");
        else ol.replaceChildren.apply(ol, rows);
      }

      var rl = byId("mh02RoomList");
      if (rooms.__err) { errState(rl, "/api/sprechzimmer/sessions", rooms); byId("mh02RoomSum").textContent = "\u2013"; }
      else {
        var ss = rooms.sessions || [];
        byId("mh02RoomSum").textContent = ss.length + (ss.length === 1 ? " Sitzung" : " Sitzungen");
        if (!ss.length) empty(rl, "Keine Sitzungen");
        else rl.replaceChildren.apply(rl, ss.slice(0, 4).map(function (s) {
          return li(s.title || s.topic || s.name || s.id || "Sitzung", (s.participants ? [].concat(s.participants).join(", ") : "") + (s.updated_at ? " \u00b7 " + fmtDateTime(s.updated_at) : ""));
        }));
      }

      var last = {};
      if (!lev.__err) (lev.events || []).forEach(function (e) { var k = normAgent(e.agent); if (k) last[k] = e; });
      TEAM.forEach(function (a) {
        var c = byId("mh02Agent-" + a[0]); if (!c) return;
        syncAgentStatus(a[0]);
        var ag = rights.agents && rights.agents[a[3]];
        c.querySelector(".mh02-agent-rights").textContent = rights.__err ? "Rechte: GET /api/agents/rights " + rights.__err : ag ? "Rechte: " + ag.terminal_level + " \u00b7 " + (ag.web_tools || []).length + " Web-Werkzeuge" : "Rechte: nicht gemeldet";
        var ev = last[a[0]];
        c.querySelector(".mh02-agent-last").textContent = lev.__err ? "Letzte Aktivit\u00e4t: GET /api/live/events " + lev.__err : ev ? "Letzte Aktivit\u00e4t: " + fmtTime(ev.ts) + " \u00b7 " + (ev.kind || "") : "Letzte Aktivit\u00e4t: keine Ereignisse";
        var col = lcfg && lcfg.agents && lcfg.agents[a[3]] && lcfg.agents[a[3]].color;
        if (col && /^#[0-9a-f]{3,8}$/i.test(col)) c.style.setProperty("--mh02-agent", col);
      });
      var sel = byId("nxGlassTargetSelect");
      TEAM.forEach(function (a) { var c = byId("mh02Agent-" + a[0]); if (c) c.classList.toggle("mh02-selected", !!sel && sel.value === a[0]); });
      home.dataset.loadedAt = new Date().toISOString();
    } finally { loading = false; }
  }

  /* ---- Sichtbarkeit: Startseite bei leerem Chat oder ueber "Startseite" ---- */
  function syncHome() {
    if (!home) return;
    var w = byId("welcome");
    var emptyChat = !!w && !w.classList.contains("hide");
    var show = emptyChat || forced;
    var was = !home.hidden;
    home.hidden = !show;
    document.body.classList.toggle("mh02-home-on", show);
    byId("mh02Back").hidden = !(forced && !emptyChat);
    if (show && !was) { refresh(); var th = byId("nxThread"); if (th) th.scrollTop = 0; }
  }
  function showHome(on) {
    forced = !!on;
    if (on) navigate("chat");
    syncHome();
    if (on) { var th = byId("nxThread"); if (th) th.scrollTop = 0; }
    var sb = byId("nxGlassNav-mh02start"); if (sb) sb.classList.toggle("active", !!on);
  }

  function mount(n) {
    var thread = byId("nxThread"), welcome = byId("welcome"), nav = byId("nxGlassNav");
    if (!thread || !welcome || !nav) { if (n < 60) setTimeout(function () { mount(n + 1); }, 250); return; }
    if (byId("mh02Home")) return;
    try {
      groupNav();
      welcome.after(build());
      document.body.classList.add("mh02");
      new MutationObserver(syncHome).observe(welcome, { attributes: true, attributeFilter: ["class"] });
      var off = function () { if (forced) showHome(false); };
      var send = byId("btnSend"); if (send) send.addEventListener("click", off);
      var inp = byId("nxInput"); if (inp) inp.addEventListener("keydown", function (e) { if (e.key === "Enter" && !e.shiftKey) off(); });
      var sl = byId("sessionList"); if (sl) sl.addEventListener("click", off);
      var sel = byId("nxGlassTargetSelect"); if (sel) sel.addEventListener("change", function () { setTimeout(function () { TEAM.forEach(function (a) { var c = byId("mh02Agent-" + a[0]); if (c) c.classList.toggle("mh02-selected", sel.value === a[0]); }); }, 50); });
      tick(); setInterval(tick, 1000);
      TEAM.forEach(function (a) {
        var ps = byId("nxPartnerStatus-" + a[0]);
        if (ps) new MutationObserver(function () { syncAgentStatus(a[0]); }).observe(ps, { childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ["class"] });
        syncAgentStatus(a[0]);
      });
      syncHome(); refresh();
      /* app.js scrollt den Verlauf beim Laden nach unten: Startseite oben zeigen, solange Eddy nicht selbst scrollt */
      ["wheel", "touchstart", "keydown", "mousedown"].forEach(function (ev) { thread.addEventListener(ev, function () { touched = true; }, { passive: true }); });
      [300, 1200, 3000, 6000].forEach(function (ms) { setTimeout(function () { if (!touched && !home.hidden && !forced) thread.scrollTop = 0; }, ms); });
      setInterval(refresh, 30000);
      document.addEventListener("visibilitychange", function () { if (!document.hidden) refresh(); });
      window.NEXUS_MH02 = Object.freeze({ refresh: refresh, show: function () { showHome(true); }, hide: function () { showHome(false); }, version: "mh02b" });
    } catch (e) { console.error("mh02 mount", e); }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { mount(0); });
  else mount(0);
})();
