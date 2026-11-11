/* THINK-05/06 2026-11-10: Denkprozess-Fenster (nativ, Master Home).
   - Globaler Schalter An/Aus: localStorage "nxThinkEnabled" + Server /api/chat/think.
   - An: /api/chat/stream bekommt think:true, Denk-Tokens (SSE type "thinking") laufen live ins Panel.
   - Aus: think:false, Panel eingeklappt.
   - Gespeicherter Denkprozess je Antwort: aufklappbar (auch nach Reload, /api/chat/thinking).
   Keine Aenderung an app.js noetig: fetch wird nur fuer /api/chat/stream umhuellt (Body + tee). */
(function () {
  "use strict";
  if (window.__nxThink) return;
  var LS = "nxThinkEnabled";
  var S = { enabled: false, live: "", running: false, model: "", lastLen: 0, items: [], fetchAt: 0 };
  window.__nxThink = S;
  var HL = /^(\s*(?:[-*\d.)]+\s*)?(?:\*\*)?(?:wait|moment|warte|pr\u00fcfe|pruefe|hmm|aber|actually|let me (?:check|verify|re-?check)|double[- ]check|nochmal|stop)\b)/i;

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function fmt(text) {
    return String(text || "").split(/\n/).map(function (line) {
      var e = esc(line);
      return HL.test(line) ? '<mark class="nx-think-hl">' + e + "</mark>" : e;
    }).join("\n");
  }
  function el(tag, id, cls, html) { var e = document.createElement(tag); if (id) e.id = id; if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }

  var panel = el("aside", "nxThinkPanel", "", null);
  panel.setAttribute("aria-label", "Denkprozess");
  panel.innerHTML =
    '<div class="nx-think-head">' +
      '<button type="button" id="nxThinkToggle" role="switch" aria-checked="false" title="Denkprozess an/aus (think)">' +
        '<span class="nx-think-knob" aria-hidden="true"></span><span class="nx-think-label">Denkprozess</span>' +
        '<span id="nxThinkState" class="nx-think-state">Aus</span></button>' +
    "</div>" +
    '<div id="nxThinkBody" class="nx-think-body" aria-live="polite">' +
      '<p id="nxThinkEmpty" class="nx-think-empty">Kein Denkprozess aktiv</p>' +
      '<pre id="nxThinkText" class="nx-think-text" hidden></pre>' +
    "</div>" +
    '<div id="nxThinkMeta" class="nx-think-meta"></div>';
  var tgl = panel.querySelector("#nxThinkToggle"), stateEl = panel.querySelector("#nxThinkState"),
      txt = panel.querySelector("#nxThinkText"), empty = panel.querySelector("#nxThinkEmpty"), meta = panel.querySelector("#nxThinkMeta");

  function mount() {
    var chat = document.getElementById("nxChat");
    if (!chat) return setTimeout(mount, 300);
    if (panel.parentNode !== chat) chat.appendChild(panel);
  }
  function apply(on, persist) {
    S.enabled = !!on;
    document.body.classList.toggle("nx-think-on", S.enabled);
    document.body.classList.add("nx-think-ready");
    tgl.setAttribute("aria-checked", S.enabled ? "true" : "false");
    stateEl.textContent = S.enabled ? "An" : "Aus";
    if (persist) {
      try { localStorage.setItem(LS, S.enabled ? "1" : "0"); } catch (e) {}
      try { origFetch("/api/chat/think", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: S.enabled }) }).catch(function () {}); } catch (e) {}
    }
    render();
  }
  function render() {
    var has = !!S.live;
    empty.hidden = has;
    txt.hidden = !has;
    if (has) { txt.innerHTML = fmt(S.live); var b = panel.querySelector("#nxThinkBody"); if (S.running) b.scrollTop = b.scrollHeight; }
    empty.textContent = S.running && S.enabled ? "Denkt nach \u2026" : "Kein Denkprozess aktiv";
    meta.textContent = has ? (S.live.length + " Zeichen" + (S.model ? " \u00b7 " + S.model : "") + (S.running ? " \u00b7 live" : "")) : (S.enabled ? "think: an" : "think: aus");
    panel.classList.toggle("is-live", S.running);
  }
  tgl.addEventListener("click", function () { apply(!S.enabled, true); });

  // ---- Stream anzapfen ----
  var origFetch = window.fetch.bind(window);
  function consume(stream) {
    var reader = stream.getReader(), dec = new TextDecoder(), buf = "";
    S.live = ""; S.running = true; S.model = ""; render();
    function finish(ev) {
      S.running = false;
      if (ev) {
        if (ev.thinking && ev.thinking.length >= S.live.length) S.live = ev.thinking;
        S.model = ev.model || S.model;
      }
      S.lastLen = S.live.length;
      render();
      if (S.live) { var t = S.live, m = S.model; setTimeout(function () { attachLast(t, m); }, 400); setTimeout(function () { attachLast(t, m); }, 1500); }
    }
    function pump() {
      return reader.read().then(function (r) {
        if (r.done) { if (S.running) finish(null); return; }
        buf += dec.decode(r.value, { stream: true });
        var parts = buf.split("\n\n"); buf = parts.pop() || "";
        parts.forEach(function (block) {
          block.split("\n").forEach(function (line) {
            if (line.indexOf("data:") !== 0) return;
            var raw = line.slice(5).trim(); if (!raw || raw === "[DONE]") return;
            var ev; try { ev = JSON.parse(raw); } catch (e) { return; }
            if (ev.type === "thinking") { S.live += ev.text || ""; render(); }
            else if (ev.type === "done") finish(ev);
          });
        });
        return pump();
      }).catch(function () { if (S.running) finish(null); });
    }
    pump();
  }
  window.fetch = function (input, init) {
    try {
      var url = typeof input === "string" ? input : (input && input.url) || "";
      if (/\/api\/chat\/stream(?:\?|$)/.test(url) && init && typeof init.body === "string") {
        var b = JSON.parse(init.body);
        if (b && typeof b === "object" && b.think === undefined) { b.think = S.enabled; init = Object.assign({}, init, { body: JSON.stringify(b) }); }
        return origFetch(input, init).then(function (res) {
          try {
            if (res.ok && res.body && res.body.tee) {
              var two = res.body.tee(); consume(two[1]);
              return new Response(two[0], { status: res.status, statusText: res.statusText, headers: res.headers });
            }
          } catch (e) {}
          return res;
        });
      }
    } catch (e) {}
    return origFetch.apply(window, arguments);
  };

  // ---- Je Nachricht aufklappbar ----
  function details(thinking, model) {
    var d = el("details", "", "nx-think-msg", null);
    d.innerHTML = '<summary>Denkprozess \u00b7 ' + thinking.length + " Zeichen" + (model ? " \u00b7 " + esc(model) : "") + "</summary><pre>" + fmt(thinking) + "</pre>";
    d.dataset.len = String(thinking.length);
    return d;
  }
  function put(row, thinking, model) {
    if (!row || row.querySelector(".nx-think-msg")) return false;
    var body = row.querySelector(".msg-body") || row, who = body.querySelector(".who");
    var d = details(thinking, model);
    if (who && who.nextSibling) body.insertBefore(d, who.nextSibling); else body.insertBefore(d, body.firstChild);
    return true;
  }
  function rows() { return [].slice.call(document.querySelectorAll("#messages .msg-row.assistant")); }
  function attachLast(thinking, model) { var r = rows(); if (r.length) put(r[r.length - 1], thinking, model); }
  function rawOf(row) { var c = row.querySelector(".content"); return ((c && (c.getAttribute("data-raw") || c.textContent)) || "").trim(); }
  function attachItems() {
    var r = rows().filter(function (x) { return !x.querySelector(".nx-think-msg"); });
    S.items.forEach(function (it) {
      var head = String(it.content_head || "").trim().slice(0, 60);
      for (var i = 0; i < r.length; i++) {
        var raw = rawOf(r[i]);
        if (head ? raw.indexOf(head) === 0 : !raw) { put(r[i], it.thinking, it.model); r.splice(i, 1); break; }
      }
    });
  }
  function loadItems() {
    var now = Date.now(); if (now - S.fetchAt < 1500) return; S.fetchAt = now;
    origFetch("/api/chat/thinking").then(function (x) { return x.json(); }).then(function (j) {
      if (j && j.success && Array.isArray(j.items)) { S.items = j.items; attachItems(); }
    }).catch(function () {});
  }
  var deb = 0;
  function watch() {
    var m = document.getElementById("messages"); if (!m) return setTimeout(watch, 500);
    new MutationObserver(function () { clearTimeout(deb); deb = setTimeout(function () { if (!S.running) loadItems(); attachItems(); }, 500); }).observe(m, { childList: true });
    loadItems();
  }

  // ---- Start ----
  var ls = null; try { ls = localStorage.getItem(LS); } catch (e) {}
  mount(); apply(ls === "1", false); watch();
  origFetch("/api/chat/think").then(function (x) { return x.json(); }).then(function (j) {
    if (!j || !j.success) return;
    if (ls === null) apply(!!j.enabled, true);
    else if (!!j.enabled !== (ls === "1")) apply(ls === "1", true);
  }).catch(function () {});

  // ---- Pruefhaken ?thinkcheck=1 (DOM-Test, headless) ----
  if (/[?&]thinkcheck=1/.test(location.search)) (window.__nxStableLoad || function (f) { setTimeout(f, 0); })(function () { var rel = window.__nxRelease; window.__nxRelease = function () {}; setTimeout(function () {
    var r = {};
    try {
      var pr = panel.getBoundingClientRect(), cr = (document.getElementById("nxComposer") || panel).getBoundingClientRect(),
          th = (document.getElementById("nxThread") || panel).getBoundingClientRect();
      r.mounted = panel.parentNode && panel.parentNode.id; r.enabled = S.enabled; r.aria = tgl.getAttribute("aria-checked");
      r.emptyText = empty.textContent; r.emptyVisible = !empty.hidden && empty.offsetHeight > 0;
      r.panel = [pr.left | 0, pr.top | 0, pr.width | 0, pr.height | 0];
      r.overlapComposer = pr.width > 0 && cr.right > pr.left + 1 && cr.left < pr.right && cr.bottom > pr.top && cr.top < pr.bottom;
      r.overlapThread = pr.width > 0 && th.right > pr.left + 1 && th.left < pr.right && th.bottom > pr.top && th.top < pr.bottom;
      r.msgDetails = document.querySelectorAll("#messages .nx-think-msg").length; r.items = S.items.length;
      r.bodyClass = document.body.className.indexOf("nx-think-on") >= 0;
    } catch (e) { r.err = String(e); }
    var p = document.createElement("pre"); p.id = "thinkcheck"; p.textContent = JSON.stringify(r); document.body.appendChild(p);
    if (rel) rel();
  }, 1500); });
})();
