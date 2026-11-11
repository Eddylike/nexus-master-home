(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const messagesEl = $("#messages");
  const welcome = $("#welcome");
  const input = $("#nxInput");
  const sendBtn = $("#btnSend");
  let activeSession = null;
  let busy = false;
  let nxRewrite = null; // 2026-10-28 CHAT04: 'regenerate' | 'edit'
  let stickToBottom = true;

  function threadEl() { return $("#nxThread"); }
  function nearBottom(el, px) {
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < (px || 80);
  }
  function scrollThreadToBottom(force) {
    const el = threadEl();
    if (!el) return;
    if (force || stickToBottom) {
      el.scrollTop = el.scrollHeight;
      el.classList.add("pinned-bottom");
    }
  }
  function pageScroll(dir) {
    const el = threadEl();
    if (!el) return;
    const amount = Math.max(180, Math.floor(el.clientHeight * 0.85));
    el.scrollBy({ top: dir * amount, behavior: "smooth" });
    stickToBottom = nearBottom(el, 100);
    el.classList.toggle("pinned-bottom", stickToBottom);
  }

  let streamAbort = null;
  let lastScreenDataUrl = null;
  let currentSpeaker = null;
  let partnerRequest = null;
  const partnerNames = { nexus: "NEXUS", hermes: "HERMES", gpt: "NEXUS GPT", aether: "AETHER" };
  const primaryPartners = ["hermes", "nexus", "gpt"];

  function partnerName(partner) {
    return partnerNames[partner] || "NEXUS";
  }

  function partnerState(partner, state, result) {
    const detail = { partner, state };
    if (result) detail.result = result;
    window.dispatchEvent(new CustomEvent("nexus:partner-state", { detail }));
  }

  function resultSucceeded(result) {
    if (!result || result.aborted) return false;
    const status = String(result.status || (result.data && result.data.execution_status) || "").toUpperCase();
    const failed = ["BLOCKED", "FAILED", "ERROR", "PARTIAL", "CANCELLED"].includes(status);
    return result.success === true && !result.error && !failed;
  }

  function reportPartnerResult(partner, result) {
    if (!result || result.aborted) return;
    const ok = resultSucceeded(result);
    partnerState(partner, ok ? "success" : "error", result);
    window.dispatchEvent(new CustomEvent("nexus:result", { detail: { ...result, partner } }));
  }

  function historicalPartner(message) {
    return responseSpeaker(message, "nexus");
  }

  function responseSpeaker(result, fallback) {
    /* 20261102 Nexus-UI: kein Absturz, falls chat_pipeline.js (noch) nicht geladen ist */
    const p = window.NEXUS_CHAT_PIPELINE;
    if (p && typeof p.speaker === "function") return p.speaker(result, fallback);
    const r = result || {}, d = r.data || {};
    return String(r.partner || d.partner || fallback || "nexus").toLowerCase();
  }

  function setMessageSpeaker(row, partner) {
    if (!row || !partnerNames[partner]) return;
    row.dataset.partner = partner;
    row.dataset.speaker = partner;
    const who = row.querySelector(".who");
    if (who) who.textContent = partnerName(partner);
  }

  // Voice live state
  const voice = {
    fromMic: false,
    live: false,
    status: "off", // off | listening | thinking | speaking | text
    recognition: null,
    ttsAudio: $("#nxTts") || $("#nxAudio") || new Audio(),
    speaking: false,
    speakingPartner: null,
    pendingPartner: null,
    speakQueue: Promise.resolve(),
    bargeIn: false,
    // Standard: Antworten vorlesen; nur Text wenn Eddie es befiehlt
    autoSpeak: localStorage.getItem("nexus_auto_speak") !== "0",
    speakGen: 0,
    // voicefix-20261006: Live-Mikro Zustandsautomat
    holds: 0,          // >0 = thinking/speaking -> Erkennung AUS
    recPaused: false,
    recRunning: false,
    recTimer: null,
    recFastEnds: 0,
    recStartedAt: 0,
    micHeld: false,
  };
  window.voice = voice;
  voice.ttsAudio.addEventListener("playing", function () {
    voice.speaking = true;
    voice.speakingPartner = voice.pendingPartner || "nexus";
    setVoiceChip("speaking");
  });
  ["pause", "ended", "error"].forEach(function (event) {
    voice.ttsAudio.addEventListener(event, function () {
      voice.speaking = false;
      voice.speakingPartner = null;
    });
  });

  const partnerVoices = {
    nexus:  { voices: ["de-DE-ConradNeural"],  rate: "-7%", pitch: "-8Hz" },
    hermes: { voices: ["de-DE-KillianNeural"], rate: "-2%", pitch: "+0Hz" },
    aether: { voices: ["de-DE-AmalaNeural"],   rate: "-4%", pitch: "-2Hz" },
    gpt:    { voices: ["de-DE-AmalaNeural"],   rate: "-4%", pitch: "-2Hz" },
  };

  // Screen share
  const screen = {
    active: false,
    stream: null,
    timer: null,
    video: $("#screenPreview"),
  };

  // Live stream follow
  const liveFollow = {
    active: false,
    timer: null,
    intervalMs: 8000,
  };

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      ...opts,
    });
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) return res.json();
    if (ct.includes("audio/")) return { success: res.ok, blob: await res.blob(), status: res.status };
    return { success: res.ok, status: res.status };
  }

  function autosize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 200) + "px";
  }

  function setSpeakUi(on) {
    document.body.classList.toggle("nx-speaking", !!on);
    const pulse = $("#speakPulse");
    if (pulse) pulse.classList.toggle("on", !!on);
    const last = messagesEl && messagesEl.querySelector(".msg-row.assistant:last-child");
    if (last) last.classList.toggle("is-speaking", !!on);
  }

  function setVoiceChip(status) {
    voice.status = status;
    const chip = $("#chipVoice");
    if (!chip) return;
    const map = {
      off: voice.autoSpeak ? "Vorlesen an" : "Nur Text",
      listening: "Listening",
      thinking: "Thinking",
      speaking: "Speaking",
      text: "Nur Text",
    };
    chip.textContent = map[status] || status;
    chip.title = voice.autoSpeak
      ? "Antworten werden vorgelesen (sag: nur schreiben)"
      : "Nur Text — sag: sprich wieder";
    const ok = status === "listening" || (status === "off" && voice.autoSpeak);
    chip.className = "chip " + (status === "speaking" || status === "thinking" ? "warn" : ok ? "ok" : "");
    chip.classList.toggle("speaking-chip", status === "speaking");
    setSpeakUi(status === "speaking");
  }

  function setAutoSpeak(on, announce) {
    voice.autoSpeak = !!on;
    localStorage.setItem("nexus_auto_speak", on ? "1" : "0");
    if (!voice.live && !voice.speaking) setVoiceChip(on ? "off" : "text");
    if (announce) addMsg("assistant", on ? "Ab jetzt lese ich Antworten vor." : "Ab jetzt nur Text — ohne Vorlesen.");
  }

  // nexus_auto_speak_init
  setVoiceChip(voice.autoSpeak ? "off" : "text");
  // nexus_unlock_bind
  ["pointerdown", "keydown", "click"].forEach(function (ev) {
    document.addEventListener(ev, function () { unlockSpeech(); }, { passive: true });
  });
  (function () {
    const btn = document.getElementById("btnAutoSpeak");
    if (!btn) return;
    const sync = function () {
      btn.textContent = voice.autoSpeak ? "🔊" : "🔇";
      btn.title = voice.autoSpeak ? "Vorlesen an" : "Nur Text";
    };
    sync();
    btn.addEventListener("click", function () {
      unlockSpeech();
      setAutoSpeak(!voice.autoSpeak, true);
      sync();
      if (voice.autoSpeak) {
        // Sofort im Klick-Gesture sprechen (Browser erlaubt das)
        speakReply("Vorlesen ist an. Ich spreche deine Antworten.");
      }
    });
  })();
  if (localStorage.getItem("nexus_speak_amala_force") !== "1") {
      localStorage.setItem("nexus_speak_amala_force", "1");
      localStorage.setItem("nexus_auto_speak", "1");
      voice.autoSpeak = true;
    }
    if (localStorage.getItem("nexus_speak6_force") !== "1") {
    localStorage.setItem("nexus_speak6_force", "1");
    localStorage.setItem("nexus_auto_speak", "1");
    voice.autoSpeak = true;
    setVoiceChip("off");
  }



  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /** Minimal markdown-lite: **bold**, `code`, preserve newlines via pre-wrap */
  function formatLite(text) {
    if (window.NXMD && window.NXMD.render) return window.NXMD.render(text || ""); // 2026-10-28 CHAT02
    let s = escapeHtml(text || "");
    s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    return s;
  }

  function setContent(el, text, asMarkdown) {
    if (!el) return;
    el.dataset.raw = text || "";
    if (asMarkdown && /^\s*ENTWICKLUNGSAUFTRAG/.test(text || "")) el.innerHTML = formatBuildReply(text || "");
    else if (asMarkdown) el.innerHTML = formatLite(text);
    else el.textContent = text || "";
  }

  // chatfix_20261102: Build-Protokoll einklappen, kurze Zusammenfassung zeigen.
  function formatBuildReply(text) {
    const cut = text.indexOf("\n\n");
    const proto = cut > 0 ? text.slice(0, cut) : text;
    const rest = cut > 0 ? text.slice(cut + 2).trim() : "";
    const st = (rest.match(/\b(PASS|FAIL|PARTIAL|BLOCKED|UNKNOWN)\b/) || proto.match(/\b(PASS|FAIL|PARTIAL|BLOCKED|UNKNOWN)\b/) || [])[1] || "UNKNOWN";
    const files = (proto.match(/\((?:Code-Plan[^,]*),\s*(\d+) Dateien\)/) || [])[1];
    const esc = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    let next = "Details pr\u00fcfen und Auftrag ggf. erneut starten.";
    if (st === "PASS") next = "Ergebnis im Projekt ansehen.";
    else if (/CODE_EXECUTION_DISABLED/.test(text)) next = "Testlauf ist ohne Freigabe (NEXUS_CODE_EXEC=1) gesperrt \u2013 Code wurde nur statisch gepr\u00fcft.";
    const summary = "<p><strong>Bauauftrag bearbeitet</strong> \u2013 Status: <strong>" + esc(st) + "</strong>"
      + (files ? " \u00b7 " + esc(files) + " Datei(en) geplant" : "") + "</p><p>N\u00e4chster Schritt: " + esc(next) + "</p>";
    const restLong = rest.length > 600;
    const restHtml = rest && !restLong ? formatLite(rest) : "";
    const details = "<details class=\"build-proto\"><summary>Build-Protokoll anzeigen</summary><pre style=\"white-space:pre-wrap;font-size:12px\">"
      + esc(proto + (restLong ? "\n\n" + rest : "")) + "</pre></details>";
    return summary + restHtml + details;
  }

  function addCopyButton(row, contentEl) {
    const actions = document.createElement("div");
    actions.className = "msg-actions";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn-copy";
    btn.textContent = "Kopieren";
    btn.title = "Kopieren";
    btn.onclick = async (e) => {
      e.stopPropagation();
      const raw = contentEl.dataset.raw || contentEl.textContent || "";
      try {
        await navigator.clipboard.writeText(raw);
        btn.textContent = "Kopiert";
        btn.classList.add("copied");
        setTimeout(() => {
          btn.textContent = "Kopieren";
          btn.classList.remove("copied");
        }, 1200);
      } catch (_) {
        btn.textContent = "Fehler";
        setTimeout(() => { btn.textContent = "Kopieren"; }, 1200);
      }
    };
    actions.appendChild(btn);
    row.appendChild(actions);
  }

  function addMsg(role, text, meta, partner) {
    text = String(text == null ? "" : text).replace(/\s*\[Antwortvorgabe:[\s\S]*?\]\s*$/, ""); /* n4StripPolicy: interne Vorgabe nie anzeigen */
    if (welcome) welcome.classList.add("hide");
    const row = document.createElement("div");
    row.className = "msg-row " + role;
    const speaker = partnerNames[partner] ? partner : (currentSpeaker || "nexus");
    row.dataset.partner = role === "user" ? "human" : speaker;
    const body = document.createElement("div");
    body.className = "msg-body";
    const who = document.createElement("div");
    who.className = "who";
    who.textContent = role === "user" ? "Du" : partnerName(speaker);
    const content = document.createElement("div");
    content.className = "content";
    if (role === "assistant") setContent(content, text || "", true);
    else setContent(content, text || "", false);
    body.appendChild(who);
    body.appendChild(content);
    if (meta) {
      const m = document.createElement("div");
      m.className = "meta";
      if (/model_response_unverified/.test(String(meta))) { m.innerHTML = '<span class="n4-badge" title="model_response_unverified">ungepr\u00fcft</span>'; } else m.textContent = meta;
      body.appendChild(m);
    }
    row.appendChild(body);
    if (role === "assistant") setMessageSpeaker(row, speaker);
    if (role === "assistant") addCopyButton(row, content);
    messagesEl.appendChild(row);
    scrollThreadToBottom(false);
    return row;
  }

  function showTyping(partner) {
    const row = addMsg("assistant", "", null, partner);
    row.classList.add("typing", "is-streaming");
    const content = row.querySelector(".content");
    content.innerHTML = '<span class="dots"><span></span><span></span><span></span></span><span class="stream-shimmer" aria-hidden="true"></span>';
    if (partner === "hermes") { const l = document.createElement("span"); l.className = "hermes-wait"; l.textContent = " Hermes lädt Modell…"; content.appendChild(l); }
    return row;
  }

  async function refreshHealth() {
    try {
      const h = await api("/api/health");
      if (!h || h.success === false) throw new Error("Health unavailable");
      $("#chipHealth").textContent = "Health " + (h.score ?? "—");
      $("#chipHealth").className = "chip " + ((h.score || 0) >= 70 ? "ok" : "warn");
      const o = h.ollama || {};
      $("#chipOllama").textContent = o.reachable ? "Ollama OK" : "Ollama DOWN";
      $("#chipOllama").className = "chip " + (o.reachable ? "ok" : "warn");
      const modelLabel = o.model || "—";
      $("#chipModel").textContent = modelLabel;
      $("#ctxStatus").textContent =
        "Score " + h.score + " · Port " + h.port + " · " + (o.reachable ? "Ollama online" : "Ollama offline");
      const sel = $("#modelSelect");
      if (sel && o.models) {
        const cur = sel.value;
        sel.innerHTML = '<option value="">auto</option>';
        o.models.forEach((m) => {
          const opt = document.createElement("option");
          opt.value = m;
          opt.textContent = m;
          sel.appendChild(opt);
        });
        if (cur) sel.value = cur;
      }
    } catch (_) {
      $("#chipHealth").textContent = "Health unbekannt";
      $("#chipHealth").className = "chip warn";
      $("#chipOllama").textContent = "Ollama unbekannt";
      $("#chipOllama").className = "chip";
      $("#chipModel").textContent = "Modell —";
      $("#ctxStatus").textContent = "Status nicht verfügbar · Verbindung prüfen";
    }
  }

  async function refreshSessions() {
    // chatfix_20261102: Netzwerkwackler (z. B. Core-Neustart) still bis 3x neu versuchen.
    let data = null;
    for (let attempt = 0; attempt < 3 && !data; attempt++) {
      try { data = await api("/api/sessions"); }
      catch (e) { await new Promise((r) => setTimeout(r, 1500 * (attempt + 1))); }
    }
    if (!data) return;
    activeSession = data.active;
    const q = ($("#chatSearch").value || "").toLowerCase();
    const list = $("#sessionList");
    list.innerHTML = "";
    (data.sessions || []).forEach((s) => {
      if (q && !(s.title || "").toLowerCase().includes(q)) return;
      const b = document.createElement("button");
      b.type = "button";
      b.className = "sess" + (s.id === activeSession ? " active" : "");
      b.textContent = String(s.title || s.id).replace(/\s*\[Antwortvo[\s\S]*$/, "");
      b.onclick = async () => {
        if (busy) return;
        await api("/api/sessions/switch", { method: "POST", body: JSON.stringify({ id: s.id }) });
        await loadSession(s.id);
        refreshSessions();
        document.getElementById("nxApp").classList.remove("side-open");
      };
      list.appendChild(b);
    });
  }

  async function loadSession(id) {
    const data = await api("/api/session?id=" + encodeURIComponent(id || ""));
    const s = data.session || {};
    activeSession = s.id;
    messagesEl.innerHTML = "";
    $("#chatTitle").textContent = String(s.title || "NEXUS").replace(/\s*\[Antwortvo[\s\S]*$/, "");
    (s.messages || []).forEach((m) => addMsg(m.role === "user" ? "user" : "assistant", m.content, m.role === "user" ? "" : ((m.data && m.data.provenance) || "Historischer Modelltext – nicht verifiziert"), historicalPartner(m)));
    if (!(s.messages || []).length) welcome.classList.remove("hide");
    else welcome.classList.add("hide");
    stickToBottom = true;
    scrollThreadToBottom(true);
    window.dispatchEvent(new CustomEvent("nexus:session", {detail: s}));
  }

  async function refreshContext() {
    try {
      const st = await api("/api/status");
      $("#ctxProject").textContent = st.project_id || "Kein aktives Projekt";
      const files = await api("/api/files");
      $("#ctxFiles").textContent = (files.items || []).map((i) => i.name).slice(0, 8).join(", ") || "(leer)";
    } catch (_) {}
  }

  function maybeAttachScreen(text) {
    if (!screen.active || !lastScreenDataUrl) return text;
    const low = (text || "").toLowerCase();
    const tips = ["bildschirm", "screen", "was siehst", "was siehst du", "schau", "anzeige", "desktop"];
    if (tips.some((t) => low.includes(t))) {
      return text + "\n\n[Hinweis: Screen-Frame ist aktiv — bitte /api/screen/analyze nutzen.]";
    }
    return text;
  }

  async function sendStream(text, partner = "nexus") {
    const body = { message: text, session_id: activeSession };
    if (nxRewrite) { body[nxRewrite] = true; nxRewrite = null; } // 2026-10-28 CHAT04
    const model = $("#modelSelect") && $("#modelSelect").value;
    if (model) body.model = model;

    const typing = showTyping(partner);
    const contentEl = typing.querySelector(".content");
    let full = "";
    let doneMeta = {};
    let receivedDone = false;
    const ctrl = new AbortController();
    streamAbort = ctrl;

    try {
      const res = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: ctrl.signal,
      });
      if (!res.ok || !res.body) throw new Error("stream_http_" + res.status);
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      typing.classList.remove("typing");
      setContent(contentEl, "", false);
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() || "";
        for (const block of parts) {
          const lines = block.split("\n").filter((l) => l.startsWith("data:"));
          for (const line of lines) {
            const raw = line.slice(5).trim();
            if (!raw || raw === "[DONE]") continue;
            let ev;
            try {
              ev = JSON.parse(raw);
            } catch (_) {
              continue;
            }
            if (ev.type === "token") {
              full += ev.text || "";
              typing.classList.add("is-streaming");
              setContent(contentEl, full, false);
              scrollThreadToBottom(false);
            } else if (ev.type === "done") {
              full = ev.reply || full;
              typing.classList.remove("is-streaming", "typing");
              setContent(contentEl, full, true);
              doneMeta = ev;
              setMessageSpeaker(typing, responseSpeaker(ev, partner));
              receivedDone = true;
              if (ev.session_id) activeSession = ev.session_id;
              if (ev.intent || ev.model) {
                let meta = typing.querySelector(".meta");
                if (!meta) {
                  meta = document.createElement("div");
                  meta.className = "meta";
                  typing.querySelector(".msg-body").appendChild(meta);
                }
                meta.textContent = ev.intent || ev.model || "";
              }
            }
          }
        }
      }
      if (!receivedDone) throw new Error("stream_incomplete");
      typing.classList.remove("is-streaming", "typing");
      if (!full) {
        setContent(contentEl, "Keine Antwort.", true);
      } else {
        setContent(contentEl, full, true);
      }
      return { reply: full || doneMeta.reply || "", ...doneMeta };
    } catch (e) {
      if (e.name === "AbortError") {
        // 2026-10-28 CHAT04: Teilantwort sichtbar lassen und als abgebrochen markieren
        if (full) {
          typing.classList.remove("is-streaming", "typing");
          setContent(contentEl, full + "\n\n*[abgebrochen]*", true);
        } else typing.remove();
        return { reply: "", aborted: true };
      }
      // The server may already have executed a write. Never resubmit implicitly.
      typing.remove();
      const reply = "Verbindung unterbrochen; Ausführungsstatus unklar. Der Auftrag wurde nicht erneut gesendet. Sitzung neu laden und mit 'weiter' den gespeicherten Auftrag fortsetzen.";
      addMsg("assistant", reply, "BLOCKED");
      return { success: false, reply, error: "STREAM_INTERRUPTED_NO_RESUBMIT", data: { execution_status: "BLOCKED" } };
    } finally {
      streamAbort = null;
    }
  }

  window.addEventListener("nexus:send", (event) => {
    const shared = window.NEXUS_CHAT_PIPELINE;
    const req = shared ? shared.request(event.detail) : { text: typeof event.detail === "string" ? event.detail : "", partner: null };
    if (req.text) send(req.text, req.partner);
  });

  const concisePolicy = "Antworte standardmaessig kurz, praezise und nur mit den notwendigen Informationen. Wenn der Nutzer ausdruecklich Details oder eine ausfuehrliche Erklaerung verlangt, darfst du entsprechend ausfuehrlich antworten.";
  const introductionPrompts = {
    nexus: "Stelle dich vor. Antworte ausschliesslich mit deinem Namen NEXUS und einer nummerierten Liste deiner tatsaechlichen Kernfaehigkeiten: Koordination, Verbindung und Systemsteuerung. Keine Einleitung, kein Schluss.",
    hermes: "Stelle dich vor. Antworte ausschliesslich mit deinem Namen HERMES und einer nummerierten Liste deiner tatsaechlichen Kernfaehigkeiten: technische Implementierung, Werkzeuge und Ausfuehrung. Keine Einleitung, kein Schluss.",
    gpt: "Stelle dich als NEXUS GPT vor. Nenne kurz deine tatsaechlichen Faehigkeiten als OpenAI-Modell fuer Analyse, Wissen und Planung. Behaupte keine lokale Ausfuehrung ohne echte Werkzeugbelege. Keine Einleitung, kein Schluss.",
    aether: "Stelle dich vor. Antworte ausschliesslich mit deinem Namen AETHER und einer nummerierten Liste deiner tatsaechlichen Kernfaehigkeiten: Forschung, kreative Problemloesung, Zukunftsplanung und Bewertung menschlicher Auswirkungen. Keine Einleitung, kein Schluss."
  };
  function conciseMessage(text) {
    return text + "\n\n[Antwortvorgabe: " + concisePolicy + "]";
  }
  function gptProvenance(result) {
    return !!(result && result.success === true && !result.error && !result.bridge_fallback &&
      typeof result.model === "string" && result.model && result.reply &&
      ((result.mode === "cloud/openai" && result.source === "openai_orchestrator" &&
        typeof result.response_id === "string" && result.response_id) ||
       (result.mode === "local/ollama" && result.source === "gpt_local" && result.provider === "ollama")));
  }

  async function requestPartner(partner, text) {
    const previousSpeaker = currentSpeaker;
    currentSpeaker = partner;
    partnerRequest = { partner, started: Date.now(), executing: false };
    partnerState(partner, "thinking");
    window.dispatchEvent(new CustomEvent("nexus:busy", { detail: { busy: true, session_id: activeSession, partner } }));
    let result;
    try {
      if (partner === "nexus") {
        result = await sendStream(text, partner);
      } else {
        const route = partner === "hermes" ? "/api/hermes/agent" : (partner === "aether" ? "/api/aether/agent" : (partner === "gpt" && !window.NEXUS_GPT_CLOUD ? "/api/gpt/agent" : "/api/chat"));
        const payload = { message: text, session_id: activeSession, chat_session_id: activeSession };
        // The cloud route owns model selection. The local Ollama selector is not
        // forwarded as an unsupported promise about the OpenAI model in use.
        if (partner === "gpt" && window.NEXUS_GPT_CLOUD) payload.mode = "cloud";  // t329u: Standard lokal
        const typing = showTyping(partner);
        try {
          result = await api(route, { method: "POST", body: JSON.stringify(payload) });
        } finally {
          typing.remove();
        }
        if (!result || result.success !== true || result.error || !result.reply) {
          result = { ...(result || {}), success: false,
            error: (result && result.error) || "PARTNER_RESPONSE_FAILED",
            reply: (result && result.reply) || (partnerName(partner) + ": " + ((result && result.error) || "Keine erfolgreiche Antwort vom Endpunkt.")) };
        } else if (partner === "gpt" && !gptProvenance(result)) {
          result = { ...result, success: false, error: "GPT_PROVENANCE_FAILED", reply: "NEXUS GPT: Antwort ohne gültigen OpenAI-Herkunftsnachweis." };
        } else if (partner === "aether" && !(window.NEXUS_CHAT_PIPELINE ? window.NEXUS_CHAT_PIPELINE.aetherProvenance(result) : (result.partner === "aether" && result.provider === "ollama" && result.model === "qwen3.5:9b"))) {
          result = { ...result, success: false, error: "AETHER_PROVENANCE_FAILED", reply: "AETHER: Antwort ohne gültigen lokalen Herkunftsnachweis." };
        } else if (partner === "hermes" && !(result.agent === "hermes" && result.source === "hermes" && result.provider === "ollama-qwen35")) {
          result = { ...result, success: false, error: "HERMES_PROVENANCE_FAILED", reply: "HERMES: Antwort ohne gültigen Agenten-Herkunftsnachweis." };
        }
        const proof = result.success !== true ? "Fehler · " + result.error :
          (partner === "gpt" ? "OpenAI · " + result.model : result.provider + " · " + result.model);
        addMsg("assistant", result.reply, proof, responseSpeaker(result, partner));
        if (result.chat_session_id) activeSession = result.chat_session_id;
        // Hermes returns its own CLI session ID, which is not a Nexus chat ID.
      }
    } catch (error) {
      result = { success: false, error: "PARTNER_REQUEST_FAILED", reply: partnerName(partner) + ": " + error.message };
      addMsg("assistant", result.reply, "Verbindungsfehler", partner);
    } finally {
      partnerRequest = null;
      currentSpeaker = previousSpeaker;
    }
    const actualPartner = responseSpeaker(result, partner);
    if (actualPartner !== partner) partnerState(partner, "idle");
    if (result && result.aborted) partnerState(actualPartner, "idle");
    if (result) result.partner = actualPartner;
    reportPartnerResult(actualPartner, result);
    return result;
  }

  async function applyResultUi(result) {
    if (!result || result.success === false || result.error) return;
    if (result.intent === "ui" && result.data && result.data.action) {
      await executeUiAction(result.data.action, result.data);
    }
    if (result.intent === "os_close_opened" || result.intent === "os_close_url") {
      await executeUiAction("close_pages", result.data || {});
    }
  }

  async function runGroup(text, introductions) {
    const results = [];
    for (const partner of primaryPartners) {
      const prompt = conciseMessage(introductions ? introductionPrompts[partner] : text);
      const result = await requestPartner(partner, prompt);
      const actualPartner = responseSpeaker(result, partner);
      results.push({ partner: actualPartner, result });
      if (result && result.aborted) break;
      try {
        await applyResultUi(result);
      } catch (error) {
        const failure = { success: false, error: "UI_ACTION_FAILED", reply: partnerName(partner) + ": " + error.message };
        results[results.length - 1].result = failure;
        addMsg("assistant", failure.reply, "Oberflächenaktion fehlgeschlagen", partner);
        reportPartnerResult(partner, failure);
      }
      if (result && result.reply && !result.aborted && (voice.autoSpeak || voice.live || voice.fromMic)) {
        await speakReply(result.reply, actualPartner);
      }
    }
    const success = results.every(item => resultSucceeded(item.result));
    return { success, reply: "", error: success ? null : "GROUP_PARTIAL_FAILURE",
      data: { group: true, introductions: !!introductions, order: primaryPartners.slice(), results } };
  }

  async function runIntroductions() {
    return runGroup("", true);
  }

  async function send(preset, forcedPartner) {
    let text = (preset || input.value || "").trim();
    if (!text || busy) return;
    let responsePartner = forcedPartner || window.NEXUS_PHASE2_PARTNER || "nexus";
    if (!partnerNames[responsePartner]) responsePartner = "nexus";
    const group = !forcedPartner && window.NEXUS_CHAT_MODE === "group";
    currentSpeaker = responsePartner;
    unlockSpeech();
    if (localStorage.getItem("nexus_auto_speak") === null) voice.autoSpeak = true;
    // If screen active and user asks about screen → analyze frame first path via vision
    const low = text.toLowerCase();
    const screenAsk =
      screen.active &&
      lastScreenDataUrl &&
      ["bildschirm", "screen", "was siehst", "schau auf", "desktop"].some((t) => low.includes(t));

    busy = true;
    window.dispatchEvent(new CustomEvent("nexus:busy", {detail: {busy: true, session_id: activeSession, partner: group ? "group" : responsePartner}}));
    sendBtn.disabled = true;
    input.value = "";
    autosize();
    addMsg("user", text);
    try { setActivityChip("Denkt…", true); } catch (_) {}

    try {
      liveHold(); // voicefix-20261006: Mikro aus waehrend thinking/speaking
      let result;
      let resultReported = false;
      // heynexus-20261006: Sprachbefehle (PC-App / Handy-App / WhatsApp mit Bestaetigung) zuerst
      if (low === "vorstellen") {
        result = await runIntroductions();
        resultReported = true;
      } else {
      currentSpeaker = "nexus";
      const vcmd = await voiceCommandRoute(text);
      currentSpeaker = responsePartner;
      if (vcmd) {
        result = vcmd;
        responsePartner = "nexus";
      } else if (screenAsk) {
        responsePartner = "nexus";
        currentSpeaker = responsePartner;
        partnerState(responsePartner, "thinking");
        setVoiceChip(voice.live ? "thinking" : voice.status);
        const typing = showTyping();
        const r = await api("/api/screen/analyze", {
          method: "POST",
          body: JSON.stringify({
            image: lastScreenDataUrl,
            prompt: text + " Antworte kurz auf Deutsch.",
          }),
        });
        typing.remove();
        result = { ...r, reply: r.reply || r.error || "Keine Analyse", model: r.model };
        addMsg("assistant", result.reply, result.model || "vision");
      } else {
        result = group ? await runGroup(text, false) : await requestPartner(responsePartner, conciseMessage(text));
        resultReported = true;
      }
      }
      await Promise.allSettled([refreshSessions(), refreshContext()]);
      try {
        const sessions = await api("/api/sessions");
        const cur = (sessions.sessions || []).find((s) => s.id === activeSession);
        if (cur) $("#chatTitle").textContent = String(cur.title || "NEXUS").replace(/\s*\[Antwortvo[\s\S]*$/, "");
      } catch (_) {}

      responsePartner = responseSpeaker(result, responsePartner);
      if (!resultReported) reportPartnerResult(responsePartner, result);
      await applyResultUi(result);
      // Standard: vorlesen (autoSpeak). Aus nur auf Befehl "nur schreiben".
      // UI-Modus-Umschalter selbst nicht nochmal vorlesen-Schleife.
      // Nach "nur schreiben" nicht noch vorlesen; bei "sprich wieder" die Bestätigung laut sagen
      const skipSpeak =
        result && result.data && result.data.action === "speak_off";
      if (
        result &&
        result.reply &&
        !result.aborted &&
        !skipSpeak &&
        (voice.autoSpeak || voice.live || voice.fromMic)
      ) {
        await speakReply(result.reply, responsePartner);
      }
      if (voice.vcPending && result && result.intent === "voice_command") vcArmTimeout(); // heynexus-20261006: 15 s Stille = verwerfen
      voice.fromMic = false;
      return result;
    } catch (e) {
      const result = { success: false, error: "SEND_FAILED", reply: "Netzwerkfehler: " + e.message };
      addMsg("assistant", result.reply, "Fehler", responsePartner);
      reportPartnerResult(responsePartner, result);
      return result;
    } finally {
      busy = false;
      currentSpeaker = null;
      partnerRequest = null;
      window.dispatchEvent(new CustomEvent("nexus:busy", {detail: {busy: false, session_id: activeSession, partner: group ? "group" : responsePartner}}));
      sendBtn.disabled = false;
      if (!voice.live) input.focus();
      else if (voice.status !== "speaking") setVoiceChip("listening");
      liveRelease(); // voicefix-20261006: genau ein verzoegerter Neustart nach Antwort+Wiedergabe
    }
  }

  function unlockSpeech() {
    if (voice.audioUnlocked) return;
    voice.audioUnlocked = true;
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (Ctx) {
        const ctx = new Ctx();
        if (ctx.state === "suspended") ctx.resume().catch(function () {});
        const buf = ctx.createBuffer(1, 1, 22050);
        const src = ctx.createBufferSource();
        src.buffer = buf;
        src.connect(ctx.destination);
        src.start(0);
        voice._audioCtx = ctx;
      }
    } catch (e) {}
    try {
      if (window.speechSynthesis) {
        const u = new SpeechSynthesisUtterance(".");
        u.volume = 0;
        u.lang = "ru-RU";
        window.speechSynthesis.speak(u);
        window.speechSynthesis.cancel();
      }
    } catch (e) {}
  }

  function finishSpeakChip() {
    voice.speaking = false;
    voice.speakingPartner = null;
    if (voice.live) setVoiceChip("listening");
    else setVoiceChip(voice.autoSpeak ? "off" : "text");
  }

  function speakBrowser(text) {
    return new Promise(function (resolve) {
      try {
        if (!window.speechSynthesis) {
          resolve(false);
          return;
        }
        window.speechSynthesis.cancel();
        const u = new SpeechSynthesisUtterance(String(text).slice(0, 3500));
        u.lang = "de-DE";
        u.rate = 1.02;
        const pickVoice = function () {
          const voices = window.speechSynthesis.getVoices() || [];
          return (
            voices.find(function (v) {
              return /^ru/i.test(v.lang) && /female|Irina|Elena|Svetlana|Google/i.test(v.name + " Female");
            }) ||
            voices.find(function (v) {
              return /^ru/i.test(v.lang);
            }) ||
            voices.find(function (v) {
              return /de(-|_)?DE/i.test(v.lang) && /Katja|Hedda|Google Deutsch|Anna|Microsoft/i.test(v.name);
            }) ||
            voices.find(function (v) {
              return /^de/i.test(v.lang);
            }) ||
            null
          );
        };
        let settled = false;
        const done = function (ok) {
          if (settled) return;
          settled = true;
          finishSpeakChip();
          resolve(!!ok);
        };
        u.onend = function () { done(true); };
        u.onerror = function () { done(false); };
        setVoiceChip("speaking");
        voice.speaking = true;
        const go = function () {
          const v2 = pickVoice();
          if (v2) u.voice = v2;
          window.speechSynthesis.speak(u);
        };
        if (!(window.speechSynthesis.getVoices() || []).length) {
          window.speechSynthesis.onvoiceschanged = go;
          setTimeout(go, 200);
        } else {
          go();
        }
        setTimeout(function () {
          if (voice.speaking) done(true);
        }, Math.min(120000, 2000 + String(text).length * 80));
      } catch (e) {
        finishSpeakChip();
        resolve(false);
      }
    });
  }


  async function playTtsBlob(blob, partner = "nexus", text = "") {
    if (!blob || blob.size < 200) return false;
    liveHold();
    try { return await playTtsBlobInner(blob, partnerNames[partner] ? partner : "nexus", text); } finally { liveRelease(); }
  }

  async function playTtsBlobInner(blob, partner, text) {
    unlockSpeech();
    stopTts();
    const gen = voice.speakGen;
    setVoiceChip("thinking");
    voice.pendingPartner = partner;
    const url = URL.createObjectURL(blob);
    const audio = voice.ttsAudio;
    try { audio.pause(); } catch (e0) {}
    audio.src = url;
    try { audio.muted = false; audio.volume = 1; } catch (e) {}
    if (window.NEXUS_AVATARS) window.NEXUS_AVATARS.bindAudio(audio, partner, null, text);
    // voicefix-20261006: Sicherheits-Timer aus echter Audiodauer (vorher zu kurz -> Ende gemeldet, Audio lief weiter)
    const okPlay = await new Promise(function (resolve) {
      let settled = false;
      let hang = null;
      let genWatch = null;
      const done = function (v) {
        if (settled) return;
        settled = true;
        try { clearTimeout(hang); } catch (e1) {}
        try { clearInterval(genWatch); } catch (e1b) {}
        try { audio.onloadedmetadata = null; } catch (e1c) {}
        try { URL.revokeObjectURL(url); } catch (e2) {}
        resolve(!!v);
      };
      const onHang = function () { try { audio.pause(); } catch (e0) {} done(true); };
      hang = setTimeout(onHang, ttsHangMs(null, blob));
      audio.onloadedmetadata = function () {
        if (settled) return;
        try { clearTimeout(hang); } catch (e) {}
        hang = setTimeout(onHang, ttsHangMs(audio, blob));
      };
      genWatch = setInterval(function () { if (gen !== voice.speakGen) done(false); }, 400);
      audio.onended = function () { done(true); };
      audio.onerror = function () { done(false); };
      audio.play().then(function () {}).catch(function () { done(false); });
    });
    if (gen === voice.speakGen) finishSpeakChip();
    return okPlay && gen === voice.speakGen;
  }

  async function voiceTurnFromBlob(blob, filename) {
    if (!blob || blob.size < 100) return null;
    liveHold();
    try { return await voiceTurnFromBlobInner(blob, filename); } finally { liveRelease(); }
  }

  async function voiceTurnFromBlobInner(blob, filename) {
    busy = true;
    setVoiceChip("thinking");
    try {
      const buf = await blob.arrayBuffer();
      const bytes = new Uint8Array(buf);
      let bin = "";
      for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
      const b64 = btoa(bin);
      const res = await fetch("/api/voice/turn", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          audio_b64: b64,
          filename: filename || "voice.webm",
          language: "de",
          session_id: activeSession || undefined,
          speak: true,
        }),
      });
      const data = await res.json();
      const partner = responseSpeaker(data, "nexus");
      if (data && data.session_id) activeSession = data.session_id;
      if (data && data.text) addMsg("user", "🎤 " + data.text);
      const reply = (data && (data.reply || data.error)) || "Keine Antwort.";
      addMsg("assistant", reply, data && data.model, partner);
      reportPartnerResult(partner, data);
      if (data && data.tts_b64) {
        const raw = atob(data.tts_b64);
        const arr = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
        await playTtsBlob(new Blob([arr], { type: "audio/mpeg" }), partner, reply);
      } else if (reply && voice.autoSpeak) {
        await speakReply(reply, partner);
      }
      return data;
    } catch (e) {
      addMsg("assistant", "Voice-Turn Fehler: " + (e.message || e));
      return null;
    } finally {
      busy = false;
      if (!voice.live) setVoiceChip(voice.autoSpeak ? "off" : "text");
    }
  }

  async function speakReply(text, partner) {
    if (!text) return false;
    const speaker = partnerVoices[partner] ? partner : "nexus";
    const job = voice.speakQueue.then(async function () {
      liveHold();
      try { return await speakReplyInner(text, speaker); } finally { liveRelease(); }
    });
    // Serial queue: a failed item must not poison later replies.
    voice.speakQueue = job.catch(function () { return false; });
    return job;
  }

  async function speakReplyInner(text, partner) {
    if (!text) return;
    const clean = String(text)
      .replace(/```[\s\S]*?```/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    if (!clean) return;

    unlockSpeech();
    stopTts(); // bumps speakGen — cancel prior; we own this gen (no second bump)
    const gen = voice.speakGen;
    setVoiceChip("thinking");
    voice.pendingPartner = partner;
    const profile = partnerVoices[partner] || partnerVoices.nexus;
    const stillMe = function () { return gen === voice.speakGen; };

    // Partnerbezogenes Edge-TTS; kein Browser/SAPI-Fallback.
    async function tryEdge(segment) {
      const ctrl = new AbortController();
      const timer = setTimeout(function () { ctrl.abort(); }, 22000);
      try {
        const res = await fetch("/api/voice/tts", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: segment, voices: profile.voices, rate: profile.rate, pitch: profile.pitch, source: partner }),
          signal: ctrl.signal,
        });
        if (!stillMe()) return false;
        const ct = (res && res.headers.get("content-type")) || "";
        if (!(res && res.ok && ct.indexOf("audio") >= 0)) return false;
        const blob = await res.blob();
        if (!stillMe() || blob.size < 200) return false;
        const url = URL.createObjectURL(blob);
        const audio = voice.ttsAudio;
        audio.src = url;
        try { audio.muted = false; audio.volume = 1; } catch (e) {}
        if (window.NEXUS_AVATARS) window.NEXUS_AVATARS.bindAudio(audio, partner, null, segment);
        // voicefix-20261006: Sicherheits-Timer aus echter Audiodauer statt Textlaenge
        const played = await new Promise(function (resolve) {
          let settled = false;
          let hang = null;
          let genWatch = null;
          const done = function (v) {
            if (settled) return;
            settled = true;
            try { clearTimeout(hang); } catch (e1) {}
            try { clearInterval(genWatch); } catch (e1b) {}
            try { audio.onloadedmetadata = null; } catch (e1c) {}
            if (window.NEXUS_AVATARS) window.NEXUS_AVATARS.stopAudio();
            try { URL.revokeObjectURL(url); } catch (e2) {}
            resolve(!!v);
          };
          const onHang = function () { try { audio.pause(); } catch (e0) {} done(true); };
          hang = setTimeout(onHang, ttsHangMs(null, blob));
          audio.onloadedmetadata = function () {
            if (settled) return;
            try { clearTimeout(hang); } catch (e) {}
            hang = setTimeout(onHang, ttsHangMs(audio, blob));
          };
          genWatch = setInterval(function () { if (!stillMe()) done(false); }, 400);
          audio.onended = function () { done(true); };
          audio.onerror = function () { done(false); };
          const p = audio.play();
          if (p && p.then) {
            p.then(function () {}).catch(function () {
              try { unlockSpeech(); } catch (e2) {}
              try { audio.muted = false; audio.volume = 1; } catch (e3) {}
              audio.play().then(function () {}).catch(function () { done(false); });
            });
          }
        });
        return played && stillMe();
      } catch (e) {
        return false;
      } finally {
        clearTimeout(timer);
      }
    }

    // Lange Antworten in geordneten Segmenten sprechen, ohne Text im Chat zu verändern.
    const chunks = [];
    let rest = clean;
    while (rest.length) {
      if (rest.length <= 1600) { chunks.push(rest); break; }
      let cut = rest.lastIndexOf(". ", 1600);
      if (cut < 800) cut = rest.lastIndexOf(" ", 1600);
      if (cut < 400) cut = 1600;
      else if (rest.slice(cut, cut + 2) === ". ") cut += 1;
      chunks.push(rest.slice(0, cut).trim());
      rest = rest.slice(cut).trim();
    }
    let ok = true;
    for (const chunk of chunks) {
      if (!stillMe()) { ok = false; break; }
      if (!(await tryEdge(chunk))) { ok = false; break; }
    }
    if (stillMe()) finishSpeakChip();
    if (!ok && stillMe()) console.warn("NEXUS: partnerbezogene Edge-TTS-Wiedergabe fehlgeschlagen.");
    return ok;
  }

  function stopTts() {
    try {
      voice.speakGen += 1;
    } catch (e) {}
    try {
      if (window.speechSynthesis) window.speechSynthesis.cancel();
    } catch (e) {}
    const audio = voice.ttsAudio;
    try {
      audio.pause();
      audio.currentTime = 0;
      audio.removeAttribute("src");
      audio.load();
    } catch (e) {}
    voice.speaking = false;
    voice.speakingPartner = null;
    voice.pendingPartner = null;
    if (window.NEXUS_AVATARS) window.NEXUS_AVATARS.stopAudio();
  }

  // 2026-10-28 CHAT04: Stopp / Neu generieren / Bearbeiten (PC-Chat + Master Home)
  function nxStopGeneration() {
    if (streamAbort) { try { streamAbort.abort(); } catch (_) {} }
    try { stopTts(); } catch (_) {}
  }
  function nxRows(role) { return Array.from(messagesEl.querySelectorAll(".msg-row." + role)); }
  function nxRefreshTurnActions() {
    messagesEl.querySelectorAll(".btn-regen,.btn-edit").forEach((b) => b.remove());
    if (busy) return;
    const lastA = nxRows("assistant").pop(), lastU = nxRows("user").pop();
    if (!lastU) return;
    const mk = (cls, label, fn) => { const b = document.createElement("button"); b.type = "button"; b.className = cls; b.textContent = label; b.title = label; b.onclick = (e) => { e.stopPropagation(); fn(); }; return b; };
    const userText = () => ((lastU.querySelector(".content") || {}).dataset || {}).raw || "";
    if (lastA) {
      let acts = lastA.querySelector(".msg-actions");
      if (!acts) { acts = document.createElement("div"); acts.className = "msg-actions"; lastA.appendChild(acts); }
      acts.appendChild(mk("btn-regen", "Neu generieren", () => {
        const q = userText(); if (!q || busy) return;
        lastA.remove(); lastU.remove(); nxRewrite = "regenerate"; send(q);
      }));
    }
    let ua = lastU.querySelector(".msg-actions");
    if (!ua) { ua = document.createElement("div"); ua.className = "msg-actions"; lastU.appendChild(ua); }
    ua.appendChild(mk("btn-edit", "Bearbeiten", () => {
      const q = userText(); if (busy) return;
      if (lastA && lastA.compareDocumentPosition(lastU) & Node.DOCUMENT_POSITION_PRECEDING) lastA.remove();
      lastU.remove(); nxRewrite = "edit"; input.value = q; autosize(); input.focus();
    }));
  }
  (function nxStopButton() {
    if (!sendBtn || document.getElementById("btnStopGen")) return;
    const b = document.createElement("button");
    b.type = "button"; b.id = "btnStopGen"; b.className = "btn-send btn-stop-gen"; b.title = "Antwort stoppen"; b.setAttribute("aria-label", "Antwort stoppen");
    b.textContent = "\u25A0"; b.style.display = "none"; b.onclick = nxStopGeneration;
    sendBtn.parentNode.insertBefore(b, sendBtn);
    window.addEventListener("nexus:busy", (ev) => {
      const on = !!(ev.detail && ev.detail.busy);
      b.style.display = on ? "" : "none";
      if (!on) setTimeout(nxRefreshTurnActions, 0);
    });
    window.addEventListener("nexus:session", () => setTimeout(nxRefreshTurnActions, 0));
  })();

  // 2026-10-28 SYNC: gemeinsamer Verlauf mit NEXUS OS GPT (Handy). Pollt den aktiven Server-Verlauf
  // und laedt ihn neu, wenn er ausserhalb dieses Fensters geaendert wurde (nicht waehrend eigener Antwort).
  (function nxSharedSync() {
    let sig = null, wasBusy = false;
    setInterval(async () => {
      if (document.hidden) return;
      if (busy) { wasBusy = true; return; }
      try {
        const r = await api("/api/session/rev");
        const s = (r.id || "") + "|" + r.count + "|" + (r.updated || "");
        if (sig === null || wasBusy) { sig = s; wasBusy = false; return; }
        if (s !== sig) { sig = s; await loadSession(r.id); refreshSessions(); }
      } catch (_) {}
    }, 3000);
  })();

  window.NEXUS_CHAT_ACTIONS = Object.freeze({ send, stopTts, stop: nxStopGeneration, refreshTurnActions: nxRefreshTurnActions });

  function bargeIn() {
    if (voice.speaking) {
      stopTts();
      voice.bargeIn = true;
      if (streamAbort) {
        try {
          streamAbort.abort();
        } catch (_) {}
      }
      setVoiceChip("listening");
    }
  }



  let perfTimer = null;

  const PERF_CIRC = 2 * Math.PI * 52;

  function setPerfRing(ringId, pctId, pct) {
    const ring = document.getElementById(ringId);
    const pctEl = document.getElementById(pctId);
    if (ring) {
      ring.classList.remove("warn", "crit", "na");
      ring.style.strokeDasharray = String(PERF_CIRC);
      if (pct == null || Number.isNaN(Number(pct))) {
        ring.style.strokeDashoffset = String(PERF_CIRC);
        ring.classList.add("na");
      } else {
        const v = Math.max(0, Math.min(100, Number(pct)));
        ring.style.strokeDashoffset = String(PERF_CIRC * (1 - v / 100));
        if (v >= 90) ring.classList.add("crit");
        else if (v >= 70) ring.classList.add("warn");
      }
    }
    if (pctEl) {
      pctEl.textContent = (pct == null || Number.isNaN(Number(pct))) ? "—" : (Math.round(Number(pct)) + "%");
    }
  }

  function _fmtRate(v) {
    if (v == null || Number.isNaN(Number(v))) return "n/a";
    const n = Number(v);
    const a = Math.abs(n);
    if (a >= 1048576) return (n / 1048576).toFixed(2) + " MB/s";
    if (a >= 1024) return (n / 1024).toFixed(1) + " KB/s";
    return Math.round(n) + " B/s";
  }

  function _renderSpark(samples) {
    const canvas = document.getElementById("perfSpark");
    if (!canvas || !canvas.getContext) return;
    const ctx = canvas.getContext("2d");
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    const vals = (samples || []).map(s => s.cpu).filter(v => v != null && !Number.isNaN(Number(v)));
    if (vals.length < 2) {
      ctx.fillStyle = "rgba(84,112,142,.8)";
      ctx.font = "11px sans-serif";
      ctx.fillText("Noch zu wenig Verlauf…", 10, h / 2 + 4);
      return;
    }
    const max = Math.max(100, ...vals);
    ctx.strokeStyle = "rgba(14,110,190,.9)";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    vals.forEach((v, i) => {
      const x = (i / (vals.length - 1)) * (w - 4) + 2;
      const y = h - 4 - (Number(v) / max) * (h - 8);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
    // fill under
    ctx.lineTo(w - 2, h - 2);
    ctx.lineTo(2, h - 2);
    ctx.closePath();
    ctx.fillStyle = "rgba(14,110,190,.15)";
    ctx.fill();
  }

  async function refreshPerf() {
    const stamp = document.getElementById("perfStamp");
    const meta = document.getElementById("perfMeta");
    try {
      const m = await api("/api/system/monitor");
      if (!m || m.success === false) {
        if (stamp) stamp.textContent = "Monitor nicht verfügbar";
        return;
      }
      const cpu = (m.cpu && m.cpu.percent != null) ? Number(m.cpu.percent) : null;
      const ram = m.ram || {};
      const disk = m.disk || {};
      const gpu = m.gpu || {};

      setPerfRing("perfCpuRing", "perfCpuPct", cpu);
      const cpuEl = document.getElementById("perfCpuVal");
      if (cpuEl) {
        cpuEl.textContent = cpu != null
          ? `${cpu} % · ${m.cpu.count ?? "?"} Kerne`
          : "UNKNOWN";
      }

      setPerfRing("perfRamRing", "perfRamPct", ram.percent);
      const ramEl = document.getElementById("perfRamVal");
      if (ramEl) {
        ramEl.textContent = (ram.percent != null)
          ? `${ram.used_gb ?? "?"} / ${ram.total_gb ?? "?"} GB · frei ${ram.available_gb ?? "?"}`
          : "UNKNOWN";
      }

      setPerfRing("perfDiskRing", "perfDiskPct", disk.percent);
      const diskEl = document.getElementById("perfDiskVal");
      if (diskEl) {
        diskEl.textContent = (disk.percent != null)
          ? `${disk.used_gb ?? "?"} / ${disk.total_gb ?? "?"} GB · frei ${disk.free_gb ?? "?"}`
          : "UNKNOWN";
      }

      let gpuPct = null;
      let gpuText = "nicht verfügbar";
      if (gpu.available && Array.isArray(gpu.gpus) && gpu.gpus.length) {
        const g0 = gpu.gpus[0];
        gpuPct = g0.percent != null ? Number(g0.percent) : null;
        gpuText = g0.name || "GPU";
        if (g0.percent != null) gpuText += ` · ${g0.percent} %`;
        if (g0.mem_used_gb != null) gpuText += ` · VRAM ${g0.mem_used_gb}/${g0.mem_total_gb} GB`;
        else if (g0.note) gpuText += ` · ${g0.note}`;
      } else if (gpu.note) {
        gpuText = gpu.note;
      }
      setPerfRing("perfGpuRing", "perfGpuPct", gpuPct);
      const gpuEl = document.getElementById("perfGpuVal");
      if (gpuEl) gpuEl.textContent = gpuText;
      const gpuPctEl = document.getElementById("perfGpuPct");
      if (gpuPctEl && gpuPct == null) gpuPctEl.textContent = "UNKNOWN";

      // Disks list
      const disksEl = document.getElementById("perfDisks");
      if (disksEl) {
        const disks = m.disks || [];
        if (!disks.length) {
          disksEl.innerHTML = '<span class="muted">Keine Laufwerke</span>';
        } else {
          disksEl.innerHTML = disks.map(d => {
            const pct = d.percent != null ? Number(d.percent) : null;
            const cls = pct == null ? "" : (pct >= 95 ? "crit" : pct >= 90 ? "warn" : "");
            const w = pct == null ? 0 : Math.min(100, Math.max(0, pct));
            return `<div class="perf-disk-row">
              <span title="${d.mount || ""}">${(d.mount || "?").slice(0, 10)}</span>
              <div class="perf-disk-bar ${cls}"><i style="width:${w}%"></i></div>
              <span>${pct != null ? Math.round(pct) + "%" : "UNKNOWN"}</span>
            </div>`;
          }).join("");
        }
      }

      // Network
      const netEl = document.getElementById("perfNet");
      if (netEl) {
        const n = m.network || {};
        const ifaces = (n.interfaces || []).filter(i => i.up).map(i => i.name).slice(0, 4);
        netEl.textContent =
          `↑ ${_fmtRate(n.bytes_sent_rate)} · ↓ ${_fmtRate(n.bytes_recv_rate)}` +
          (ifaces.length ? ` · ${ifaces.join(", ")}` : "");
      }

      // Processes
      const tbody = document.getElementById("perfProcBody");
      if (tbody) {
        const top = ((m.processes || {}).top_cpu) || [];
        if (!top.length) {
          const note = (m.processes || {}).note || "Keine Prozessdaten";
          tbody.innerHTML = `<tr><td colspan="4">${note}</td></tr>`;
        } else {
          tbody.innerHTML = top.map(p =>
            `<tr><td>${(p.name || "?").slice(0, 22)}</td><td>${p.pid ?? ""}</td>` +
            `<td>${p.cpu_percent ?? "—"}%</td><td>${p.memory_percent ?? "—"}%</td></tr>`
          ).join("");
        }
      }

      // Ollama
      const ollEl = document.getElementById("perfOllama");
      if (ollEl) {
        const o = m.ollama || {};
        if (o.reachable) {
          ollEl.innerHTML = `<span class="ok">● online</span> · ${o.preferred || "?"} · ${(o.models || []).length} Modelle`;
        } else {
          ollEl.innerHTML = `<span class="bad">● offline</span> · ${o.error || o.url || ""}`;
        }
      }

      // NEXUS
      const nxEl = document.getElementById("perfNexus");
      if (nxEl) {
        const nx = m.nexus || {};
        const listen = nx.listening === true ? '<span class="ok">Port OK</span>' :
          (nx.listening === false ? '<span class="bad">Port zu</span>' : "Port ?");
        const up = nx.uptime_s != null ? Math.round(nx.uptime_s) + " s" : "UNKNOWN";
        nxEl.innerHTML = `${listen} · PID ${nx.pid ?? "?"} · RSS ${nx.rss_mb ?? "?"} MB · Up ${up}`;
      }

      // Alerts

      // LLM (alias Ollama for dashboard label)
      const llmEl = document.getElementById("perfLlm");
      if (llmEl) {
        const o = m.ollama || {};
        if (o.reachable) {
          llmEl.innerHTML = `<span class="ok">online</span> · ${_unk(o.preferred)} · ${(o.models || []).length} Modelle`;
        } else if (o && (o.error || o.url)) {
          llmEl.innerHTML = `<span class="bad">offline</span> · ${_unk(o.error || o.url)}`;
        } else {
          llmEl.innerHTML = '<span class="unknown">UNKNOWN</span>';
        }
      }

      // Bridge probe (no token in UI — 401 means up-but-auth; network fail = UNKNOWN)
      const brEl = document.getElementById("perfBridge");
      if (brEl) {
        try {
          // uiwork11: oeffentlicher Status statt /api/bridge/health (kein Token, kein 401 in der Konsole)
          const _st = await fetch("/api/nexus-os/status", { cache: "no-store" }).then((r) => r.json()).catch(() => null); const _b = _st && _st.components && _st.components.BRIDGE; if (!_b) throw new Error("bridge status unknown"); const _on = _b.status === "ONLINE" || _b.status === "HEALTHY"; const br = { status: _on ? (_b.auth_enforced ? 401 : 200) : 503, ok: _on && !_b.auth_enforced, json: async () => _b };
          if (br.status === 401) {
            brEl.innerHTML = '<span class="ok">UP</span> · Auth required (401) · Funnel not assumed';
          } else if (br.ok) {
            const bj = await br.json().catch(() => ({}));
            brEl.innerHTML = `<span class="ok">UP</span> · ${_unk(bj.version || bj.protocol_version)} · ${_unk(bj.process_level)}`;
          } else {
            brEl.innerHTML = `<span class="bad">HTTP ${br.status}</span>`;
          }
        } catch (be) {
          brEl.innerHTML = '<span class="unknown">UNKNOWN</span>';
        }
      }

      // Battery — API does not expose; never fake
      const batEl = document.getElementById("perfBattery");
      if (batEl) {
        const bat = m.battery;
        if (bat && typeof bat === "object" && (bat.percent != null || bat.power_plugged != null)) {
          batEl.textContent = `percent=${_unk(bat.percent)} plugged=${_unk(bat.power_plugged)}`;
          batEl.classList.remove("unknown");
        } else {
          batEl.textContent = "UNKNOWN";
          batEl.classList.add("unknown");
        }
      }

      const alEl = document.getElementById("perfAlerts");
      if (alEl) {
        const alerts = m.alerts || [];
        if (!alerts.length) {
          alEl.innerHTML = '<li class="ok">Keine Alerts</li>';
        } else {
          alEl.innerHTML = alerts.map(a =>
            `<li class="${a.level || "info"}">[${(a.level || "").toUpperCase()}] ${a.message_de || a.code}</li>`
          ).join("");
        }
      }

      if (stamp) {
        stamp.textContent = "Live · " + (m.timestamp || "") + " · alle 2 s · echte Werte";
      }
      if (meta) {
        const plat = m.platform || {};
        meta.textContent = [
          `Plattform: ${plat.system || "?"} ${plat.release || ""} · ${plat.machine || ""}`,
          `Python ${plat.python || "?"} · Host ${plat.node || "?"}`,
          m.psutil ? "Quelle: psutil (+ Fallback)" : "Quelle: stdlib /proc · subprocess",
        ].join("\n");
      }

      // History sparkline (async, non-blocking)
      api("/api/system/monitor/history?n=60").then(h => {
        if (h && h.samples) _renderSpark(h.samples);
      }).catch(() => {});
    } catch (e) {
      if (stamp) stamp.textContent = "Monitor nicht erreichbar";
    }
  }


  function _unk(v) {
    return (v === null || v === undefined || v === "" || Number.isNaN(v)) ? "UNKNOWN" : v;
  }

  async function refreshMemory() {
    const list = document.getElementById("memoryList");
    if (!list) return;
    list.textContent = "Erinnerungen werden geladen …";
    try {
      const query = document.getElementById("memoryQuery");
      const r = await api("/api/memory?q=" + encodeURIComponent(query ? query.value : ""));
      if (!r || r.success === false) throw new Error((r && r.error) || "Gedächtnis nicht verfügbar");
      list.replaceChildren();
      const items = r.items || [];
      if (!items.length) { list.textContent = "Keine passenden Erinnerungen."; return; }
      for (const item of items) {
        const card = document.createElement("div"); card.className = "mem-item";
        const meta = document.createElement("div"); meta.className = "mem-id";
        const labels = {user_input: "Benutzerangabe", user_correction: "Korrigierte Benutzerangabe", execution_journal: "Gespeicherter Auftragsbeleg", legacy_unverified: "Ungeprüfter Altbestand"};
        meta.textContent = (labels[item.provenance] || item.provenance || "Herkunft unbekannt") + " · " + (item.id || "") + (item.created ? " · " + item.created : "");
        const text = document.createElement("div"); text.textContent = item.text || "";
        card.append(meta, text);
        if (item.note) { const note = document.createElement("p"); note.textContent = item.note; card.append(note); }
        if ((item.task_ids || []).length) { const ids = document.createElement("p"); ids.textContent = "Belege: " + item.task_ids.join(", "); card.append(ids); }
        if ((item.revisions || []).length) {
          const history = document.createElement("details");
          const title = document.createElement("summary"); title.textContent = "Frühere Fassungen (" + item.revisions.length + ")"; history.append(title);
          for (const revision of item.revisions) { const line = document.createElement("p"); line.textContent = (revision.superseded_at || "") + " · " + revision.text; history.append(line); }
          card.append(history);
        }
        if (item.id && item.provenance !== "execution_journal") {
          const edit = document.createElement("button"); edit.type = "button"; edit.textContent = "Korrigieren";
          edit.onclick = () => {
            edit.disabled = true;
            const form = document.createElement("form");
            const field = document.createElement("textarea"); field.value = item.text || ""; field.rows = 4; field.style.width = "100%"; field.setAttribute("aria-label", "Korrigierter Erinnerungstext");
            const save = document.createElement("button"); save.type = "submit"; save.textContent = "Korrektur speichern";
            const cancel = document.createElement("button"); cancel.type = "button"; cancel.textContent = "Abbrechen";
            const status = document.createElement("p"); status.setAttribute("role", "status");
            cancel.onclick = () => { form.remove(); edit.disabled = false; };
            form.onsubmit = async event => {
              event.preventDefault(); if (!field.value.trim() || save.disabled) return;
              save.disabled = true; cancel.disabled = true;
              try {
                const result = await api("/api/memory/correct", {method: "POST", body: JSON.stringify({id: item.id, text: field.value})});
                if (!result || result.success !== true) throw new Error((result && result.error) || "Speichern nicht bestätigt");
                await refreshMemory();
              } catch (error) { status.textContent = "Korrektur nicht bestätigt: " + error.message + ". Bitte vor erneutem Speichern aktualisieren."; cancel.disabled = false; }
            };
            form.append(field, save, cancel, status); card.append(form); field.focus();
          };
          card.append(edit);
        }
        list.append(card);
      }
    } catch (error) { list.textContent = "Gedächtnis nicht verfügbar: " + error.message; }
  }

  async function refreshSkills() {
    const list = document.getElementById("skillsList");
    const status = document.getElementById("skillsStatus");
    if (status) status.textContent = "Fähigkeiten werden geladen …";
    if (list) list.replaceChildren();
    try {
      // Existing public, redacted capability projection; no Bridge token in the UI.
      const r = await api("/api/capabilities/evidence");
      if (!r || r.success !== true) throw new Error((r && r.error) || "Status nicht verfügbar");
      if (status) status.textContent = (r.bridge_reachable ? "Bridge erreichbar" : "Bridge nicht erreichbar") + " · Registrierung und nachgewiesene Nutzbarkeit werden getrennt angezeigt.";
      for (const capability of r.capabilities || []) {
        const card = document.createElement("div"); card.className = "skill-item";
        const title = document.createElement("strong"); title.textContent = capability.name;
        const state = document.createElement("p");
        state.textContent = capability.ready === true ? "Nachgewiesen nutzbar im angegebenen Umfang" : (capability.registered ? "Registriert · Nutzbarkeit nicht vollständig nachgewiesen" : "Nicht angebunden / nicht verfügbar");
        const scope = document.createElement("p"); scope.textContent = capability.scope || "";
        card.append(title, state, scope);
        if ((capability.evidence_task_ids || []).length) { const ids = document.createElement("p"); ids.textContent = "Belege: " + capability.evidence_task_ids.join(", "); card.append(ids); }
        if (list) list.append(card);
      }
      const hermes = await api("/api/hermes/brain");
      if (list && hermes.internal === true) {
        const card = document.createElement("div"); card.className = "skill-item";
        card.textContent = "Hermes: interner NEXUS-Arbeitsablauf · " + (hermes.delegate_enabled ? "Delegation konfiguriert; Verfügbarkeit separat prüfen." : "Externer Hermes-Agent nicht aktiviert.") + " Codeausführung bleibt deaktiviert.";
        list.append(card);
      }
    } catch (error) { if (status) status.textContent = "Fähigkeitsstatus nicht verfügbar: " + error.message; }
  }

  async function refreshVoicePanel() {
    const box = document.getElementById("voiceStatusBox");
    if (!box) return;
    box.textContent = "Lade…";
    try {
      const r = await api("/api/voice/status");
      const lines = [];
      lines.push("ok: " + _unk(r && r.ok));
      lines.push("status: " + _unk(r && r.status));
      const tts = (r && r.tts) || {};
      lines.push("tts.edge_tts: " + _unk(tts.edge_tts));
      const stt = (r && r.stt) || {};
      lines.push("stt.available: " + _unk(stt.available));
      lines.push("stt.mode: " + _unk(stt.mode));
      lines.push("stt.model: " + _unk(stt.model));
      lines.push("stt.loaded: " + _unk(stt.loaded));
      lines.push("speak_pc: " + _unk(r && r.speak_pc));
      box.textContent = lines.join("\n");
    } catch (e) {
      box.textContent = "UNKNOWN · " + String(e.message || e);
    }
  }

  function stopPerfPoll() {
    // uiwork11: fehlte (ReferenceError beim Oeffnen von System)
    if (perfTimer) { clearInterval(perfTimer); perfTimer = null; }
  }

  function openPerfPanel() {
    // Body-Portal + Overlay-Bind via showWorkspacePanel (kein Klickdiebstahl)
    showWorkspacePanel("perfPanel");
    refreshPerf();
    stopPerfPoll();
    perfTimer = setInterval(refreshPerf, 2000);
  }

  function closePerfPanel() {
    const panel = document.getElementById("perfPanel");
    if (panel) panel.classList.remove("show");
    stopPerfPoll();
  }


  async function executeUiAction(action, data) {
    if (!action) return false;
    data = data || {};
    switch (action) {
      case "new_chat":
        await api("/api/sessions/new", { method: "POST", body: "{}" });
        await refreshSessions();
        await loadSession();
        return true;
      case "delete_chat":
        if (activeSession) {
          await api("/api/sessions/delete", { method: "POST", body: JSON.stringify({ id: activeSession }) });
          await refreshSessions();
          await loadSession();
        }
        return true;
      case "scroll_up":
        pageScroll(-1);
        return true;
      case "scroll_down":
        pageScroll(1);
        return true;
      case "scroll_top": {
        const el = threadEl();
        if (el) { el.scrollTo({ top: 0, behavior: "smooth" }); stickToBottom = false; }
        return true;
      }
      case "scroll_bottom":
        stickToBottom = true;
        scrollThreadToBottom(true);
        return true;
      case "screen_on":
        await startScreen();
        return true;
      case "screen_off":
        stopScreen();
        return true;
      case "screen_analyze":
        await analyzeScreen();
        return true;
      case "voice_off":
        stopLiveVoice();
        return true;
      case "speak_off":
        setAutoSpeak(false, false);
        stopTts();
        return true;
      case "speak_on":
        setAutoSpeak(true, false);
        return true;
      case "speak_last": {
        const rows = messagesEl.querySelectorAll(".msg-row.assistant .content");
        const last = rows[rows.length - 1];
        if (last) await speakReply(last.dataset.raw || last.textContent || "", last.closest(".msg-row").dataset.partner);
        return true;
      }
      case "streams_open": {
        const more = document.getElementById("sideMore");
        if (more) more.open = true;
        const btn = document.querySelector('#sideNav button[data-view="streams"]');
        if (btn) btn.click();
        return true;
      }
      case "open_perf": {
        const more = document.getElementById("sideMore");
        if (more) more.open = true;
        const btn = document.querySelector('#sideNav button[data-view="perf"]');
        if (btn) btn.click();
        else openPerfPanel();
        return true;
      }
      case "apps_open": {
        const more = document.getElementById("sideMore");
        if (more) more.open = true;
        const btn = document.querySelector('#sideNav button[data-view="apps"]');
        if (btn) btn.click();
        return true;
      }
      case "videos_open": {
        const more = document.getElementById("sideMore");
        if (more) more.open = true;
        const btn = document.querySelector('#sideNav button[data-view="videos"]');
        if (btn) btn.click();
        return true;
      }
      case "downloads_open": {
        const more = document.getElementById("sideMore");
        if (more) more.open = true;
        const btn = document.querySelector('#sideNav button[data-view="downloads"]');
        if (btn) btn.click();
        return true;
      }
      case "close_pages": {
        try {
          const body = {};
          if (data.url) body.url = data.url;
          await api("/api/os/close_opened", { method: "POST", body: JSON.stringify(body) });
        } catch (e) { /* OS optional */ }
        const wrap = $("#appFrameWrap");
        const frame = $("#appFrame");
        if (wrap) wrap.classList.add("hide");
        if (frame) {
          try { frame.removeAttribute("src"); } catch (_) {}
          try { frame.src = "about:blank"; } catch (_) {}
        }
        return true;
      }
      default:
        return false;
    }
  }

  // ===== heynexus-20261006: Sprachbefehle ueber /api/voice/command (PC-App, Handy-App, WhatsApp mit Bestaetigung) =====
  const VC_CONFIRM_TIMEOUT_MS = 15000;
  const VC_HINT = /(öffne|oeffne|öffner|starte|start|mach|zeig|schreib|schick|sende|whats\s?app|handy|telefon)/i;

  function vcClearTimer() {
    if (voice.vcTimer) { clearTimeout(voice.vcTimer); voice.vcTimer = null; }
  }

  function vcRemoveButtons() {
    document.querySelectorAll(".nx-vc-confirm").forEach(function (el) { el.remove(); });
  }

  function vcShowButtons(pending) {
    vcRemoveButtons();
    const row = document.createElement("div");
    row.className = "nx-vc-confirm";
    row.style.cssText = "display:flex;gap:8px;margin:4px 0 12px 52px;flex-wrap:wrap;";
    function mk(label, answer, primary) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = label;
      b.style.cssText = "padding:6px 14px;border-radius:8px;cursor:pointer;border:1px solid " +
        (primary ? "#2e9d5b;background:#2e9d5b;color:#fff" : "#888;background:transparent;color:inherit");
      b.onclick = function () { vcRemoveButtons(); send(answer); };
      return b;
    }
    if (pending.kind === "confirm_send") {
      row.appendChild(mk("Ja, senden", "Ja, senden", true));
      row.appendChild(mk("Nein", "Abbrechen", false));
    } else {
      row.appendChild(mk("Abbrechen", "Abbrechen", false));
    }
    messagesEl.appendChild(row);
    try { scrollThreadToBottom(false); } catch (_) {}
  }

  function vcHandleResponse(r) {
    const reply = (r && r.reply) || "Fehler beim Sprachbefehl.";
    addMsg("assistant", reply, "Sprachbefehl" + (r && r.intent ? " · " + r.intent : ""));
    vcRemoveButtons();
    vcClearTimer();
    if (r && r.confirm && r.confirm.id) {
      voice.vcPending = { id: r.confirm.id, kind: r.confirm.kind, expiresAt: Date.now() + ((r.confirm.expires_in || 60) * 1000) };
      vcShowButtons(voice.vcPending);
    } else {
      voice.vcPending = null;
    }
    return { reply: reply, intent: "voice_command", voiceCommand: r };
  }

  async function voiceCommandRoute(text) {
    const pending = voice.vcPending;
    if (pending) {
      vcClearTimer();
      if (Date.now() <= pending.expiresAt) {
        try {
          const r = await api("/api/voice/confirm", { method: "POST", body: JSON.stringify({ id: pending.id, answer: text }) });
          return vcHandleResponse(r);
        } catch (_) {
          voice.vcPending = null;
          vcRemoveButtons();
          return { reply: "Bestätigung fehlgeschlagen. Nichts gesendet.", intent: "voice_command" };
        }
      }
      voice.vcPending = null;
      vcRemoveButtons();
    }
    if (!(voice.fromMic || voice.live) || !VC_HINT.test(text || "")) return null;
    let r = null;
    try {
      r = await api("/api/voice/command", {
        method: "POST",
        body: JSON.stringify({ text: text, source: "browser_live", chat_fallback: false, llm_fallback: /whats\s?app|handy/i.test(text) }),
      });
    } catch (_) { return null; }
    if (!r || !r.handled || r.intent === "chat" || r.intent === "empty") return null;
    return vcHandleResponse(r);
  }

  function vcArmTimeout() {
    vcClearTimer();
    const p = voice.vcPending;
    if (!p) return;
    voice.vcTimer = setTimeout(async function () {
      voice.vcTimer = null;
      if (voice.vcPending !== p || busy) return;
      voice.vcPending = null;
      vcRemoveButtons();
      let r = null;
      try { r = await api("/api/voice/confirm", { method: "POST", body: JSON.stringify({ id: p.id, answer: "timeout" }) }); } catch (_) {}
      const msg = (r && r.reply) || "Keine Antwort, verworfen. Nichts gesendet.";
      addMsg("assistant", msg, "Sprachbefehl");
      if (voice.live || voice.autoSpeak) { try { await speakReply(msg); } catch (_) {} }
    }, VC_CONFIRM_TIMEOUT_MS);
  }

  function vcLivePing(on) {
    try {
      fetch("/api/voice/live_state", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active: !!on }), keepalive: true }).catch(function () {});
    } catch (_) {}
  }

  function vcLiveState(on) {
    vcLivePing(on);
    if (voice.vcLiveTimer) { clearInterval(voice.vcLiveTimer); voice.vcLiveTimer = null; }
    if (on) voice.vcLiveTimer = setInterval(function () { vcLivePing(voice.live); }, 20000);
  }
  window.addEventListener("pagehide", function () { if (voice.live) vcLivePing(false); });

  function stripWakeWord(text) {
    return (text || "").replace(/^(hey\s+)?(nexus|nexxus|nexa)([,\s]+|[:\-]\s*)/i, "").trim() || text;
  }

  // ===== voicefix-20261006: Live-Mikro als Zustandsautomat =====
  // listening: Erkennung an | thinking/speaking: Erkennung AUS (voice.holds > 0)
  // Nach Antwort+Wiedergabe genau EIN Neustart nach LIVE_RESUME_DELAY_MS.
  // onend / no-speech / aborted: Neustart nur per Timer mit Backoff (kein An/Aus-Sturm).
  const LIVE_RESUME_DELAY_MS = 450;

  function ttsHangMs(audio, blob) {
    // Edge-TTS MP3 = 48 kbit/s = 6 Bytes pro ms. Sicherheitsnetz = Dauer + 4 s.
    const est = blob && blob.size ? Math.round(blob.size / 6) : 0;
    const dur = audio && isFinite(audio.duration) && audio.duration > 0 ? Math.round(audio.duration * 1000) : 0;
    return Math.min(240000, Math.max(est, dur) + 4000);
  }

  function liveClearTimer() {
    if (voice.recTimer) {
      clearTimeout(voice.recTimer);
      voice.recTimer = null;
    }
  }

  function liveRecStart() {
    liveClearTimer();
    const rec = voice.recognition;
    if (!voice.live || !rec || voice.recPaused || voice.recRunning) return;
    try {
      rec.start();
      voice.recRunning = true;
      voice.recStartedAt = Date.now();
    } catch (_) {
      // InvalidStateError = laeuft noch -> onend plant neu
    }
  }

  function liveScheduleStart(ms) {
    liveClearTimer();
    if (!voice.live || voice.recPaused) return;
    voice.recTimer = setTimeout(liveRecStart, ms);
  }

  function liveHold() {
    voice.holds = (voice.holds || 0) + 1;
    voice.recPaused = true;
    liveClearTimer();
    const rec = voice.recognition;
    if (voice.live && rec && voice.recRunning) {
      try { rec.abort(); } catch (_) {}
    }
  }

  function liveRelease() {
    voice.holds = Math.max(0, (voice.holds || 0) - 1);
    if (voice.holds > 0) return;
    voice.recPaused = false;
    if (!voice.live) return;
    voice.recFastEnds = 0;
    setVoiceChip("listening");
    liveScheduleStart(LIVE_RESUME_DELAY_MS);
  }

  function micHoldOn() {
    if (!voice.micHeld) { voice.micHeld = true; liveHold(); }
  }

  function micHoldOff() {
    if (voice.micHeld) { voice.micHeld = false; liveRelease(); }
  }

  function startLiveVoice() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      addMsg("assistant", "Web Speech API nicht verf\u00fcgbar in diesem Browser. Live-Voice braucht Chrome/Edge.");
      return;
    }
    if (voice.live) return;
    voice.live = true;
    vcLiveState(true); // heynexus-20261006: Weckwort-Dienst gibt Mikro frei
    $("#togVoice").classList.add("on");
    $("#btnLive").classList.add("on");
    setVoiceChip("listening");

    const rec = new SR();
    rec.lang = "de-DE";
    rec.continuous = true;
    rec.interimResults = true;
    voice.recognition = rec;
    voice.recRunning = false;
    voice.recFastEnds = 0;
    voice.recPaused = (voice.holds || 0) > 0;

    rec.onresult = async (ev) => {
      // Ergebnisse nach abort()/waehrend thinking+speaking ignorieren (Echo der eigenen Stimme)
      if (voice.recognition !== rec || voice.recPaused || busy) return;
      let interim = "";
      let finalText = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const t = ev.results[i][0].transcript;
        if (ev.results[i].isFinal) finalText += t;
        else interim += t;
      }
      if (finalText.trim()) {
        const cmd = stripWakeWord(finalText.trim());
        setVoiceChip("thinking");
        voice.fromMic = true;
        // send() haelt das Mikro (liveHold) synchron vor dem ersten await und gibt es nach der Wiedergabe genau einmal frei
        await send(cmd);
      } else if (interim) {
        input.value = interim;
        autosize();
      }
    };
    rec.onerror = (e) => {
      const err = e && e.error;
      if (err === "not-allowed" || err === "service-not-allowed") {
        addMsg("assistant", "Mikrofon-Zugriff verweigert.");
        stopLiveVoice();
      } else if (err && err !== "no-speech" && err !== "aborted") {
        console.warn("NEXUS Live-Voice:", err);
      }
      // kein Sofort-Neustart hier - onend plant mit Backoff
    };
    rec.onend = () => {
      if (voice.recognition !== rec) return;
      voice.recRunning = false;
      if (!voice.live || voice.recPaused || voice.recTimer) return;
      const ran = Date.now() - (voice.recStartedAt || 0);
      voice.recFastEnds = ran < 1500 ? Math.min((voice.recFastEnds || 0) + 1, 5) : 0;
      const delay = voice.recFastEnds ? Math.min(5000, 300 * Math.pow(2, voice.recFastEnds)) : 250;
      liveScheduleStart(delay);
    };
    if (!voice.recPaused) liveRecStart();
  }

  function stopLiveVoice() {
    voice.live = false;
    vcLiveState(false); // heynexus-20261006
    liveClearTimer();
    $("#togVoice").classList.remove("on");
    $("#btnLive").classList.remove("on");
    setVoiceChip("off");
    stopTts();
    const rec = voice.recognition;
    voice.recognition = null;
    voice.recRunning = false;
    if (rec) {
      try {
        rec.onend = null;
        rec.onresult = null;
        rec.onerror = null;
        rec.abort();
      } catch (_) {}
    }
  }


  function mediaUrl(rel) {
    return "/media/" + String(rel || "").replace(/\\/g, "/");
  }

  async function refreshMusic() {
    const list = $("#musicList");
    const title = $("#musicNowTitle");
    const meta = $("#musicNowMeta");
    const player = $("#nexusAudioPlayer") || $("#nxAudio");
    if (!list) return;
    await api("/api/media/library").catch(() => {});
    const data = await api("/api/media");
    const state = data.state || data;
    const items = (state.items || []).filter((x) => x.kind === "audio" || !x.kind || String(x.path||"").match(/\.(mp3|wav|ogg|flac|m4a|aac)$/i));
    list.innerHTML = "";
    if (!items.length) {
      list.innerHTML = '<div class="streams-note">Keine Audio-Dateien. Lege mp3/wav unter workspace/media ab.</div>';
      if (title) title.textContent = "Keine Tracks";
      return;
    }
    const cur = state.current;
    if (title) title.textContent = (cur && cur.name) || items[0].name;
    if (meta) meta.textContent = (state.count || items.length) + " Tracks · Index " + (state.index || 0);
    items.forEach((it, idx) => {
      const tile = document.createElement("button");
      tile.type = "button";
      tile.className = "media-tile" + (cur && cur.path === it.path ? " active" : "");
      tile.innerHTML = '<div class="mt-icon">🎵</div><div class="mt-name">' + (it.name || it.path) + '</div><div class="mt-meta">' + Math.round((it.size||0)/1024) + ' KB</div>';
      tile.onclick = () => playMusicTrack(idx, it);
      list.appendChild(tile);
    });
  }

  async function playMusicTrack(index, it) {
    const r = await api("/api/media/play_at", { method: "POST", body: JSON.stringify({ index }) });
    const player = $("#nexusAudioPlayer") || $("#nxAudio");
    const cur = (r.state && r.state.current) || it;
    const url = "/media/" + (cur.path || (it && it.path) || "");
    if (player && cur && cur.path) {
      player.src = url;
      player.play().catch(() => {});
    }
    const title = $("#musicNowTitle");
    const meta = $("#musicNowMeta");
    if (title) title.textContent = cur.name || cur.path;
    if (meta) meta.textContent = r.reply || "Playing";
    refreshMusic();
  }

  async function refreshVideos() {
    const list = $("#videoList");
    if (!list) return;
    const data = await api("/api/videos");
    list.innerHTML = "";
    const items = data.items || [];
    if (!items.length) {
      list.innerHTML = '<div class="streams-note">Keine lokalen Videos. Lege mp4/webm unter workspace/media oder Downloads ab — oder spiele eine YouTube/mp4-URL oben ab.</div>';
      return;
    }
    items.forEach((it) => {
      const tile = document.createElement("button");
      tile.type = "button";
      tile.className = "media-tile";
      tile.innerHTML = '<div class="mt-icon">🎬</div><div class="mt-name">' + (it.name || it.path) + '</div><div class="mt-meta">' + (it.source || "video") + '</div>';
      tile.onclick = () => playLocalVideo(it);
      list.appendChild(tile);
    });
  }

  function playLocalVideo(it) {
    const video = $("#nexusVideo");
    const embed = $("#nexusVideoEmbed");
    if (embed) {
      embed.classList.add("hide");
      embed.removeAttribute("src");
    }
    if (!video) return;
    video.classList.remove("hide");
    video.src = it.play_url || mediaUrl(it.path);
    video.play().catch(() => {});
    addMsg("assistant", "Spiele in NEXUS: " + (it.name || it.path));
  }

  async function playVideoUrl() {
    const url = ($("#videoUrl").value || "").trim();
    if (!url) return;
    const r = await api("/api/videos/resolve", { method: "POST", body: JSON.stringify({ url }) });
    const video = $("#nexusVideo");
    const embed = $("#nexusVideoEmbed");
    if (!r.success) {
      addMsg("assistant", r.reply || "URL ungültig");
      return;
    }
    if (!$("#videosPanel") || !$("#videosPanel").classList.contains("show")) showWorkspacePanel("videosPanel");
    else $("#videosPanel").classList.add("show");
    if (r.mode === "embed" && r.embed && r.embed.embed) {
      if (video) {
        video.pause();
        video.removeAttribute("src");
        video.load();
        video.classList.add("hide");
      }
      embed.classList.remove("hide");
      embed.src = r.embed.embed;
      addMsg("assistant", r.reply || r.embed.note || "YouTube läuft jetzt im NEXUS-Player.");
    } else if (r.mode === "direct") {
      embed.classList.add("hide");
      embed.removeAttribute("src");
      video.classList.remove("hide");
      video.src = r.url;
      video.play().catch(() => {});
      addMsg("assistant", "Direktvideo läuft im NEXUS-Player.");
    } else {
      addMsg("assistant", r.reply || "Bitte YouTube-Link oder mp4/webm-URL verwenden.");
    }
  }


  let filesCursor = "";


  window.nxForwardOverlayClick = window.nxForwardOverlayClick || function (e, prevBtn, prevId) {
    const t0 = document.elementFromPoint(e.clientX, e.clientY);
    if (!t0 || t0.id === "nxOverlay") return false;
    const t = t0.closest('button, a, [role="button"], .chip, [data-view]');
    if (!t || t.closest(".workspace-panel")) return false;
    if (prevBtn && t === prevBtn) return false;
    if (prevId && t.dataset && t.dataset.nxPanel === prevId) return false;
    setTimeout(function () { t.click(); }, 0);
    return true;
  };

  function showWorkspacePanel(id) {
    closeAllWorkspacePanels();
    const el = document.getElementById(id);
    const ov = document.getElementById("nxOverlay");
    if (!el) { console.error("panel missing", id); return; }
    // Portal Panel + Overlay to body — Overlay darf Panel-Klicks nie stehlen
    if (el.parentElement !== document.body) {
      el.dataset.nxHome = "1";
      document.body.appendChild(el);
    }
    if (ov && ov.parentElement !== document.body) {
      document.body.appendChild(ov);
    }
    el.classList.add("show");
    if (!el.dataset.nxBound) {
      el.dataset.nxBound = "1";
      el.addEventListener("click", function (e) { e.stopPropagation(); });
      el.addEventListener("mousedown", function (e) { e.stopPropagation(); });
      el.addEventListener("pointerdown", function (e) { e.stopPropagation(); });
    }
    if (ov) {
      // Overlay unter Panel: z-index via CSS; Klick nur auf Overlay selbst schließt
      ov.classList.add("show");
      ov.onclick = function (e) {
        if (e.target !== ov) return;
        // uiwork22: Klick durchreichen - erst schliessen, dann Ziel unter dem Mauszeiger klicken (ausser leere Flaeche / gleiche Kachel)
        const prevActive = document.querySelector("#sideNav button.active");
        closeAllWorkspacePanels();
        if (window.nxForwardOverlayClick && window.nxForwardOverlayClick(e, prevActive, id)) return;
        const chatBtn = document.querySelector('#sideNav button[data-view="chat"]');
        if (chatBtn) {
          document.querySelectorAll("#sideNav button").forEach((x) => x.classList.remove("active"));
          chatBtn.classList.add("active");
        }
      };
    }
  }


  async function refreshGallery() {
    const grid = $("#galleryGrid");
    if (!grid) return;
    grid.innerHTML = '<div class="streams-note">Lade Galerie…</div>';
    try {
      const r = await api("/api/gallery");
      const items = (r && r.items) || [];
      if (!items.length) {
        grid.innerHTML = '<div class="streams-note">Keine Bilder. Lege jpg/png unter workspace/media ab.</div>';
        return;
      }
      grid.innerHTML = "";
      items.forEach((it) => {
        const a = document.createElement("a");
        a.href = it.url;
        a.target = "_blank";
        a.rel = "noopener";
        a.title = (it.name || "") + (it.source ? " · " + it.source : "");
        a.style.cssText = "display:block;aspect-ratio:1;border-radius:10px;overflow:hidden;background:#e4edf6";
        const img = document.createElement("img");
        img.src = it.url;
        img.alt = it.name || "Bild";
        img.loading = "lazy";
        img.style.cssText = "width:100%;height:100%;object-fit:cover";
        a.appendChild(img);
        grid.appendChild(a);
      });
    } catch (e) {
      grid.innerHTML = '<div class="streams-note">Galerie-Fehler: ' + e + "</div>";
    }
  }

  function setDesktopMap(q) {
    const query = (q || "").trim() || "München";
    const enc = encodeURIComponent(query);
    const frame = $("#mapsFrame");
    const link = $("#mapsExtLink");
    fetch("https://nominatim.openstreetmap.org/search?format=json&limit=1&q=" + enc, {
      headers: { Accept: "application/json" },
    })
      .then((r) => r.json())
      .then((arr) => {
        if (!arr || !arr[0]) {
          if (link) link.href = "https://www.openstreetmap.org/search?query=" + enc;
          return;
        }
        const it = arr[0];
        const south = parseFloat(it.boundingbox[0]);
        const north = parseFloat(it.boundingbox[1]);
        const west = parseFloat(it.boundingbox[2]);
        const east = parseFloat(it.boundingbox[3]);
        const bbox = [west, south, east, north].join(",");
        if (frame) {
          frame.src =
            "https://www.openstreetmap.org/export/embed.html?bbox=" +
            encodeURIComponent(bbox) +
            "&layer=mapnik&marker=" +
            encodeURIComponent(it.lat + "," + it.lon);
        }
        if (link) link.href = "https://www.openstreetmap.org/#map=14/" + it.lat + "/" + it.lon;
      })
      .catch(() => {
        if (link) link.href = "https://www.openstreetmap.org/search?query=" + enc;
      });
  }

  function closeAllWorkspacePanels() {
    ["musicPanel","streamsPanel","videosPanel","galleryPanel","mapsPanel","appsPanel","crewPanel","perfPanel","filesPanel","projectsPanel","downloadsPanel","libraryPanel","settingsPanel","activityPanel","homePanel","memoryPanel","skillsPanel","voicePanel"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.classList.remove("show");
    });
    // uiwork12: alle Workspace-Panels schliessen (memory/skills/voice blieben sonst offen und ueberlagerten)
    document.querySelectorAll(".workspace-panel.show").forEach((el) => el.classList.remove("show"));
    const ov = document.getElementById("nxOverlay");
    if (ov) {
      ov.classList.remove("show");
      ov.onclick = null;
    }
    try { closePerfPanel(); } catch (_) {}
  }

  async function refreshFiles(sub) {
    if (typeof sub === "string") filesCursor = sub;
    const list = $("#filesList");
    const preview = $("#filesPreview");
    if (!list) return;
    if ($("#filesPath")) $("#filesPath").value = filesCursor || "";
    const q = filesCursor ? ("?path=" + encodeURIComponent(filesCursor)) : "";
    const data = await api("/api/files" + q);
    list.innerHTML = "";
    if (preview) { preview.classList.add("hide"); preview.textContent = ""; }
    if (!data.success) {
      list.innerHTML = '<div class="streams-note">' + (data.error || "Fehler") + "</div>";
      return;
    }
    const items = data.items || [];
    if (!items.length) {
      list.innerHTML = '<div class="streams-note">Ordner leer. Lege Dateien unter workspace/files ab oder schreib über den Chat.</div>';
      return;
    }
    items.forEach((it) => {
      const row = document.createElement("div");
      row.className = "file-row";
      const rel = (filesCursor ? filesCursor.replace(/\\/g, "/").replace(/\/$/, "") + "/" : "") + it.name;
      row.innerHTML = '<span class="name">' + (it.is_dir ? "📁 " : "📄 ") + it.name + '</span>' +
        '<span class="meta">' + (it.is_dir ? "Ordner" : (it.size + " B")) + "</span>";
      const openBtn = document.createElement("button");
      openBtn.type = "button";
      openBtn.className = "primary";
      openBtn.textContent = it.is_dir ? "Öffnen" : "Lesen";
      openBtn.onclick = async () => {
        if (it.is_dir) refreshFiles(rel);
        else {
          const r = await api("/api/files/read", { method: "POST", body: JSON.stringify({ path: rel }) });
          if (preview) {
            preview.classList.remove("hide");
            preview.textContent = r.success ? (r.content || "") : (r.error || "Fehler");
          }
        }
      };
      const expBtn = document.createElement("button");
      expBtn.type = "button";
      expBtn.textContent = "Explorer";
      expBtn.onclick = async () => {
        // open workspace files root via OS; path hint in reply
        await api("/api/os/open_path", { method: "POST", body: JSON.stringify({ path: "G:\\meine-ki\\nexus-ai\\workspace\\files" + (filesCursor ? ("\\" + filesCursor.replace(/\//g, "\\")) : ""), reveal: !!it.is_dir }) });
      };
      row.appendChild(openBtn);
      row.appendChild(expBtn);
      list.appendChild(row);
    });
  }

  async function refreshProjects() {
    const list = $("#projectsList");
    if (!list) return;
    const data = await api("/api/projects");
    list.innerHTML = "";
    const projects = data.projects || [];
    if (!projects.length) {
      list.innerHTML = '<div class="streams-note">Noch keine Projekte. Lege oben eines an.</div>';
      return;
    }
    projects.forEach((p) => {
      const row = document.createElement("div");
      row.className = "project-row";
      const active = data.active && (data.active === p.id);
      row.innerHTML = '<span class="name">' + (active ? "★ " : "") + (p.name || p.id) + '</span>' +
        '<span class="meta">' + (p.description || p.id || "") + "</span>";
      const act = document.createElement("button");
      act.type = "button";
      act.className = "primary";
      act.textContent = "Aktiv";
      act.onclick = async () => {
        await api("/api/projects/active", { method: "POST", body: JSON.stringify({ id: p.id }) });
        refreshProjects();
        addMsg("assistant", "Aktives Projekt: " + (p.name || p.id));
      };
      const del = document.createElement("button");
      del.type = "button";
      del.textContent = "Löschen";
      del.onclick = async () => {
        if (!confirm("Projekt löschen?")) return;
        await api("/api/projects/delete", { method: "POST", body: JSON.stringify({ id: p.id }) });
        refreshProjects();
      };
      row.appendChild(act);
      row.appendChild(del);
      list.appendChild(row);
    });
  }

  async function refreshDownloads() {
    const list = $("#downloadsList");
    const st = $("#downloadsStatus");
    if (!list) return;
    const data = await api("/api/downloads");
    list.innerHTML = "";
    const items = data.items || data.downloads || data.jobs || data.files || [];
    if (st) st.textContent = data.reply || (items.length ? items.length + " Einträge" : "Keine Downloads");
    if (!items.length) {
      list.innerHTML = '<div class="streams-note">Noch keine Downloads.</div>';
      return;
    }
    items.forEach((it) => {
      const row = document.createElement("div");
      row.className = "file-row";
      const label = it.name || it.url || it.id || JSON.stringify(it);
      row.innerHTML = '<span class="name">' + label + '</span><span class="meta">' + (it.status || it.state || "") + "</span>";
      list.appendChild(row);
    });
  }

  async function refreshSettings() {
    const sel = $("#settingsModel");
    const health = $("#settingsHealth");
    if (sel && !sel.options.length) {
      const h = await api("/api/health");
      const models = (h.ollama && h.ollama.models) || [];
      let st = {};
      try { st = await api("/api/ai/status"); } catch (_) { st = {}; }
      const herm = (st && st.chat_model_preferred) || "";
      sel.innerHTML = "";
      models.filter((m) => !/embed/i.test(m)).forEach((m) => {
        const o = document.createElement("option");
        o.value = m; o.textContent = (m === herm ? "Hermes (" + m + ")" : m);
        sel.appendChild(o);
      });
      if (herm) sel.value = herm;
    }
    const auto = $("#settingsAutoSpeak");
    if (auto) auto.checked = !!(voice && voice.autoSpeak);
    const right = $("#settingsRightPanel");
    if (right) right.checked = !$("#nxApp").classList.contains("right-collapsed");
    if (health) {
      const h = await api("/api/health");
      health.textContent = JSON.stringify(h, null, 2);
    }
  }



  // heynexus-20261006: Weckwort-Schalter in den Einstellungen
  (function initWakewordToggle() {
    const el = document.getElementById("settingsWakeword");
    if (!el) return;
    const hint = document.getElementById("settingsWakewordHint");
    async function refresh() {
      try {
        const s = await api("/api/voice/wakeword/state");
        el.checked = !!s.enabled;
        if (hint) {
          const sv = s.service || {};
          hint.textContent = sv.alive
            ? ("Dienst läuft · " + (sv.state || "") + (sv.rss_mb ? " · RAM " + sv.rss_mb + " MB" : ""))
            : "Weckwort-Dienst nicht gestartet (start_wakeword.ps1).";
        }
      } catch (_) {}
    }
    el.addEventListener("change", async function () {
      try { await api("/api/voice/wakeword/config", { method: "POST", body: JSON.stringify({ enabled: !!el.checked }) }); } catch (_) {}
      refresh();
    });
    document.querySelectorAll('[data-view="settings"]').forEach(function (b) { b.addEventListener("click", refresh); });
    refresh();
  })();

  let crewActiveId = null;
  const DEFAULT_COMPOSER_PLACEHOLDER = "Nachricht an NEXUS…";

  function applyCrewActiveUi(active) {
    const chip = $("#chipCrew");
    const inp = $("#nxInput");
    const ag = active && active.agent;
    const id = (active && active.id) || null;
    crewActiveId = id;
    if (chip) {
      if (ag) {
        chip.textContent = (ag.emoji ? ag.emoji + " " : "") + "@" + ag.tag;
        chip.title = "Crew aktiv: @" + ag.tag + (ag.title ? " — " + ag.title : "");
        chip.classList.remove("hide");
        if (ag.color) chip.style.borderColor = ag.color;
      } else {
        chip.textContent = "Crew —";
        chip.title = "Aktiver Crew-Agent";
        chip.classList.add("hide");
        chip.style.borderColor = "";
      }
    }
    if (inp) {
      inp.placeholder = ag
        ? ("@" + ag.tag + ": Auftrag tippen")
        : DEFAULT_COMPOSER_PLACEHOLDER;
    }
  }

  async function syncCrewActiveChip() {
    try {
      const active = await api("/api/crew/active");
      applyCrewActiveUi(active);
    } catch (_) {
      applyCrewActiveUi(null);
    }
  }

  function syncCrewPills(agents, activeId) {
    const pills = document.querySelector("#agentPanel .agent-pills");
    if (!pills) return;
    pills.innerHTML = "";
    (agents || []).forEach((a) => {
      const sp = document.createElement("span");
      sp.className = "crew-pill" + (activeId === a.id ? " active" : "");
      sp.textContent = (a.emoji ? a.emoji + " " : "") + "@" + a.tag;
      sp.title = a.title || a.tag;
      sp.onclick = () => {
        const btn = document.querySelector('#sideNav button[data-view="crew"]');
        if (btn) btn.click();
        setTimeout(() => {
          const card = document.querySelector('.crew-card[data-id="' + a.id + '"]');
          if (card) card.click();
        }, 80);
      };
      pills.appendChild(sp);
    });
  }

  async function refreshCrew() {
    const grid = $("#crewGrid");
    const hint = $("#crewActiveHint");
    const clearBtn = $("#btnCrewClear");
    if (!grid) return;
    let data, active;
    try {
      data = await api("/api/crew");
      active = await api("/api/crew/active");
    } catch (e) {
      grid.innerHTML = '<div class="streams-note">Crew API Fehler: ' + escapeHtml(e) + "</div>";
      return;
    }
    applyCrewActiveUi(active);
    if (hint) {
      const ag = active && active.agent;
      hint.textContent = ag
        ? ("Aktiv: " + (ag.emoji || "") + " @" + ag.tag + " — " + (ag.title || ""))
        : "Kein Agent aktiv. Tippe eine Karte oder schreib @Coder …";
    }
    if (clearBtn) clearBtn.hidden = !crewActiveId;
    grid.innerHTML = "";
    const agents = (data && data.agents) || [];
    syncCrewPills(agents, crewActiveId);
    if (!agents.length) {
      grid.innerHTML = '<div class="streams-note">Keine Agenten im Roster.</div>';
      return;
    }
    agents.forEach((a) => {
      const card = document.createElement("div");
      card.className = "crew-card" + (crewActiveId === a.id ? " active" : "");
      card.style.setProperty("--crew-color", a.color || "#0e6eb8");
      card.dataset.id = a.id;
      card.dataset.tag = a.tag;
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.setAttribute("aria-pressed", crewActiveId === a.id ? "true" : "false");
      card.innerHTML =
        '<div class="crew-emoji">' + escapeHtml(a.emoji || "✦") + "</div>" +
        '<div class="crew-name">@' + escapeHtml(a.tag) + "</div>" +
        '<div class="crew-title">' + escapeHtml(a.title || "") + "</div>" +
        '<div class="crew-blurb">' + escapeHtml(a.blurb || "") + "</div>" +
        '<div class="crew-actions"><button type="button" class="primary crew-go">Los</button></div>';
      const insertTag = async () => {
        try {
          await api("/api/crew/active", { method: "POST", body: JSON.stringify({ id: a.id }) });
        } catch (_) {}
        crewActiveId = a.id;
        const inp = $("#nxInput");
        if (inp) {
          const tag = "@" + a.tag + " ";
          if (!inp.value.startsWith(tag) && !inp.value.includes("@" + a.tag)) {
            inp.value = tag + (inp.value || "").replace(/^@(Coder|Scout|Fixer|Media|Ops|Lens|Rap)\s*/i, "");
          } else if (!inp.value.trim()) {
            inp.value = tag;
          }
          inp.focus();
          try { inp.setSelectionRange(inp.value.length, inp.value.length); } catch (_) {}
        }
        refreshCrew();
      };
      const goSend = async () => {
        await insertTag();
        const inp = $("#nxInput");
        const text = (inp && inp.value || "").trim();
        if (text) {
          if (typeof send === "function") send();
        }
      };
      card.onclick = (e) => {
        if (e.target && e.target.classList && e.target.classList.contains("crew-go")) return;
        insertTag();
      };
      card.onkeydown = (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          if (e.shiftKey) goSend();
          else insertTag();
        }
      };
      card.ondblclick = (e) => { e.preventDefault(); goSend(); };
      const goBtn = card.querySelector(".crew-go");
      if (goBtn) goBtn.onclick = (e) => { e.stopPropagation(); goSend(); };
      grid.appendChild(card);
    });
  }

  async function refreshApps() {
    const grid = $("#appsGrid");
    if (!grid) return;
    const data = await api("/api/apps");
    grid.innerHTML = "";
    (data.apps || []).forEach((app) => {
      const card = document.createElement("div");
      card.className = "app-card";
      const stClass = app.status === "connected" ? "connected" : app.status === "stub" ? "stub" : "available";
      const stLabel = app.status === "connected" ? "verbunden" : app.status === "stub" ? "Stub" : "verfügbar";
      card.innerHTML =
        "<h4>" + app.name + "</h4>" +
        '<div class="cat">' + (app.category || "") + "</div>" +
        '<div class="desc">' + (app.desc || "") + "</div>" +
        '<div class="status ' + stClass + '">' + stLabel + "</div>" +
        '<div class="row"></div>';
      const row = card.querySelector(".row");
      // uiwork13: Kartenflaeche nur Auswahl/Fokus, kein API-Call. Oeffnen/Merken nur per Button (Enter auf Button geht nativ).
      card.onclick = (ev) => {
        if (ev.target && ev.target.closest && ev.target.closest("button")) return;
        grid.querySelectorAll(".app-card.selected").forEach((x) => x.classList.remove("selected"));
        card.classList.add("selected");
        const b = card.querySelector("button.primary"); if (b) b.focus();
      };
      const openBtn = document.createElement("button");
      openBtn.type = "button";
      openBtn.className = "primary";
      openBtn.textContent = app.open_mode === "embed" ? "In NEXUS" : app.open_mode === "native" ? "Starten" : app.open_mode === "internal" ? "Öffnen" : "Öffnen";
      openBtn.onclick = (ev) => { ev.stopPropagation(); openApp(app); };
      row.appendChild(openBtn);
      if (!app.connected && app.open_mode !== "stub") {
        const c = document.createElement("button");
        c.type = "button";
        c.textContent = "Merken";
        c.onclick = async () => {
          await api("/api/apps/connect", { method: "POST", body: JSON.stringify({ id: app.id }) });
          refreshApps();
        };
        row.appendChild(c);
      }
      if (app.connected) {
        const d = document.createElement("button");
        d.type = "button";
        d.textContent = "Trennen";
        d.onclick = async () => {
          await api("/api/apps/disconnect", { method: "POST", body: JSON.stringify({ id: app.id }) });
          refreshApps();
        };
        row.appendChild(d);
      }
      grid.appendChild(card);
    });
  }

  async function openApp(app) {
    if (!app) return;
    // uiwork14: Fehler bei connect/open abfangen und sichtbar melden statt unbehandelt zu werfen
    const _appFail = (what, err) => { try { addMsg("assistant", app.name + ": " + what + " fehlgeschlagen (" + ((err && err.message) || err || "unbekannt") + ")."); } catch (_) {} };
    try {
      await api("/api/apps/connect", { method: "POST", body: JSON.stringify({ id: app.id }) });
    } catch (e) { _appFail("Verbinden", e); }

    // Internal NEXUS panels
    if (app.open_mode === "internal" || app.id === "nexus_music" || app.id === "nexus_video" || app.id === "media" || app.id === "files" || app.id === "downloads") {
      if (app.id === "files") {
        showWorkspacePanel("filesPanel");
        refreshFiles("");
        const btn = document.querySelector('#sideNav button[data-view="files"]');
        if (btn) { $$("#sideNav button").forEach((x) => x.classList.remove("active")); btn.classList.add("active"); }
      } else if (app.id === "nexus_music" || app.id === "media") {
        showWorkspacePanel("musicPanel");
        refreshMusic();
        const btn = document.querySelector('#sideNav button[data-view="music"]');
        if (btn) { $$("#sideNav button").forEach((x) => x.classList.remove("active")); btn.classList.add("active"); }
      } else if (app.id === "nexus_video") {
        showWorkspacePanel("videosPanel");
        refreshVideos();
        const btn = document.querySelector('#sideNav button[data-view="videos"]');
        if (btn) { $$("#sideNav button").forEach((x) => x.classList.remove("active")); btn.classList.add("active"); }
      } else if (app.id === "downloads") {
        showWorkspacePanel("downloadsPanel");
        refreshDownloads();
        const btn = document.querySelector('#sideNav button[data-view="downloads"]');
        if (btn) { $$("#sideNav button").forEach((x) => x.classList.remove("active")); btn.classList.add("active"); }
      } else if (app.id === "nexus_crew") {
        showWorkspacePanel("crewPanel");
        refreshCrew();
        const btn = document.querySelector('#sideNav button[data-view="crew"]');
        if (btn) { $$("#sideNav button").forEach((x) => x.classList.remove("active")); btn.classList.add("active"); }
      } else if (app.id === "code" || app.id === "code_sandbox") {
        addMsg("assistant", "Code: schreib Python oder „führe aus …“ im Chat.");
      } else if (app.id === "ollama") {
        addMsg("assistant", "Ollama läuft lokal — Modelle siehst du unter Einstellungen / Health.");
      }
      refreshApps();
      return;
    }

    // Native Windows apps — always hit OS open
    if (app.open_mode === "native" || app.launch) {
      let opened = null;
      try {
        opened = await api("/api/apps/open", { method: "POST", body: JSON.stringify({ id: app.id }) });
        addMsg("assistant", (opened && opened.reply) || (app.name + " gestartet."));
      } catch (e) { _appFail("Starten", e); }
      // Panel offen lassen — nur Grid aktualisieren
      const apps = document.getElementById("appsPanel");
      if (apps && !apps.classList.contains("show")) showWorkspacePanel("appsPanel");
      refreshApps();
      return;
    }

    // Embed in NEXUS frame
    if (app.open_mode === "embed" && app.embed_url) {
      showWorkspacePanel("appsPanel");
      const wrap = $("#appFrameWrap");
      if (wrap) {
        $("#appFrameTitle").textContent = app.name;
        $("#appFrame").src = app.embed_url;
        wrap.classList.remove("hide");
      }
      try { await api("/api/apps/open", { method: "POST", body: JSON.stringify({ id: app.id }) }); } catch (e) { _appFail("Oeffnen", e); }
      addMsg("assistant", app.name + " läuft in NEXUS.");
      refreshApps();
      return;
    }

    // Link / web
    if (app.url) {
      try { await api("/api/apps/open", { method: "POST", body: JSON.stringify({ id: app.id }) }); } catch (e) { _appFail("Oeffnen", e); }
      showWorkspacePanel("appsPanel");
      const wrap = $("#appFrameWrap");
      if (wrap) {
        $("#appFrameTitle").textContent = app.name + " (Web)";
        $("#appFrame").src = app.url;
        wrap.classList.remove("hide");
      }
      try { window.open(app.url, "_blank", "noopener,noreferrer"); } catch (_) {}
      addMsg("assistant", app.name + " geöffnet.");
    }
    refreshApps();
  }

  async function startScreen() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
      addMsg("assistant", "Bildschirmfreigabe wird von diesem Browser nicht unterstützt.");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
      screen.stream = stream;
      screen.active = true;
      screen.video.srcObject = stream;
      $("#screenPreviewWrap").classList.remove("hide");
      $("#togScreen").classList.add("on");
      stream.getVideoTracks()[0].addEventListener("ended", stopScreen);
      captureScreenFrame();
      screen.timer = setInterval(captureScreenFrame, 4000);
      addMsg("assistant", "Screen-Share aktiv. Alle ~4s Frame-Capture. „Analysieren“ oder frag mich nach dem Bildschirm.");
    } catch (e) {
      addMsg("assistant", "Screen-Share abgebrochen: " + (e.message || e));
      $("#togScreen").classList.remove("on");
    }
  }

  function stopScreen() {
    screen.active = false;
    $("#togScreen").classList.remove("on");
    $("#screenPreviewWrap").classList.add("hide");
    if (screen.timer) {
      clearInterval(screen.timer);
      screen.timer = null;
    }
    if (screen.stream) {
      screen.stream.getTracks().forEach((t) => t.stop());
      screen.stream = null;
    }
    screen.video.srcObject = null;
    lastScreenDataUrl = null;
  }

  function captureScreenFrame() {
    if (!screen.active || !screen.video.videoWidth) return null;
    const c = document.createElement("canvas");
    const w = Math.min(1280, screen.video.videoWidth);
    const scale = w / screen.video.videoWidth;
    c.width = w;
    c.height = Math.round(screen.video.videoHeight * scale);
    c.getContext("2d").drawImage(screen.video, 0, 0, c.width, c.height);
    lastScreenDataUrl = c.toDataURL("image/jpeg", 0.7);
    return lastScreenDataUrl;
  }

  async function analyzeScreen() {
    const dataUrl = captureScreenFrame() || lastScreenDataUrl;
    if (!dataUrl) {
      addMsg("assistant", "Kein Screen-Frame verfügbar.");
      return;
    }
    addMsg("user", "Analysiere meinen Bildschirm");
    const typing = showTyping();
    try {
      const r = await api("/api/screen/analyze", {
        method: "POST",
        body: JSON.stringify({
          image: dataUrl,
          prompt: "Was siehst du auf dem Bildschirm? Antworte kurz auf Deutsch.",
        }),
      });
      typing.remove();
      addMsg("assistant", r.reply || r.error || "Keine Analyse", "screen/llava");
    } catch (e) {
      typing.remove();
      addMsg("assistant", "Screen-Analyse fehlgeschlagen: " + e.message);
    }
  }

  async function refreshStreams() {
    const data = await api("/api/streams");
    const list = $("#streamList");
    if (!list) return;
    list.innerHTML = "";
    (data.streams || []).forEach((s) => {
      const row = document.createElement("div");
      row.className = "stream-item" + (s.id === data.active ? " active" : "");
      row.innerHTML = "<strong>" + (s.title || s.id) + "</strong><br/><small>" + s.url + "</small>";
      // uiwork13: Zeile nur Auswahl, Abspielen nur per Button
      row.onclick = (ev) => {
        if (ev.target && ev.target.closest && ev.target.closest("button")) return;
        list.querySelectorAll(".stream-item.selected").forEach((x) => x.classList.remove("selected"));
        row.classList.add("selected");
      };
      const playBtn = document.createElement("button");
      playBtn.type = "button";
      playBtn.className = "primary stream-play";
      playBtn.textContent = "Abspielen";
      playBtn.onclick = async (ev) => {
        ev.stopPropagation();
        await api("/api/streams/active", { method: "POST", body: JSON.stringify({ id: s.id }) });
        playStream(s);
        refreshStreams();
      };
      row.appendChild(playBtn);
      list.appendChild(row);
    });
  }

  function playStream(s) {
    const video = $("#liveVideo");
    const embed = $("#liveEmbed");
    const comment = $("#streamComment");
    if (!s) return;
    comment.textContent = "";
    if (s.embed && s.embed.embed) {
      video.classList.add("hide");
      video.removeAttribute("src");
      video.load();
      embed.classList.remove("hide");
      embed.src = s.embed.embed;
      addMsg("assistant", s.embed.note || "Player in NEXUS geladen.");
      return;
    }
    embed.classList.add("hide");
    embed.removeAttribute("src");
    video.classList.remove("hide");
    video.src = s.url;
    video.play().catch(() => {
      addMsg(
        "assistant",
        "Codec/URL nicht vom Browser abspielbar. Viele Twitch/YouTube-Streams brauchen Embed oder eine direkte Media-URL (mp4/webm/m3u8). Kein DRM-Bypass."
      );
    });
  }

  async function addStream() {
    const url = ($("#streamUrl").value || "").trim();
    if (!url) return;
    const title = ($("#streamTitle").value || "").trim();
    const r = await api("/api/streams", { method: "POST", body: JSON.stringify({ url, title }) });
    addMsg("assistant", (r.reply || "") + (r.notice ? "\n" + r.notice : ""));
    if (r.stream) playStream(r.stream);
    refreshStreams();
  }

  function captureVideoFrame(videoEl) {
    if (!videoEl || !videoEl.videoWidth) return null;
    const c = document.createElement("canvas");
    const w = Math.min(960, videoEl.videoWidth);
    const scale = w / videoEl.videoWidth;
    c.width = w;
    c.height = Math.round(videoEl.videoHeight * scale);
    try {
      c.getContext("2d").drawImage(videoEl, 0, 0, c.width, c.height);
      return c.toDataURL("image/jpeg", 0.7);
    } catch (e) {
      return null;
    }
  }

  async function followOnce() {
    const video = $("#liveVideo");
    const dataUrl = captureVideoFrame(video);
    if (!dataUrl) {
      $("#streamComment").textContent =
        "Kein Frame (Embed oder CORS/Codec). KI-Mitverfolgen braucht abspielbares <video> mit direkter Media-URL.";
      return;
    }
    try {
      const r = await api("/api/screen/analyze", {
        method: "POST",
        body: JSON.stringify({
          image: dataUrl,
          prompt: "Was passiert gerade im Stream? Kurz auf Deutsch.",
        }),
      });
      const text = r.reply || r.error || "—";
      $("#streamComment").textContent = text;
      addMsg("assistant", "🎬 Stream: " + text, "stream/llava");
      const st = await api("/api/streams");
      if (st.active) {
        await api("/api/streams/comment", {
          method: "POST",
          body: JSON.stringify({ id: st.active, comment: text }),
        });
      }
    } catch (e) {
      $("#streamComment").textContent = "Fehler: " + e.message;
    }
  }

  function startFollow() {
    if (liveFollow.active) return;
    liveFollow.active = true;
    followOnce();
    liveFollow.timer = setInterval(followOnce, liveFollow.intervalMs);
    addMsg("assistant", "KI-Mitverfolgen aktiv (~alle 8s). Nur bei abspielbarem Video-Frame.");
  }

  function stopFollow() {
    liveFollow.active = false;
    if (liveFollow.timer) {
      clearInterval(liveFollow.timer);
      liveFollow.timer = null;
    }
  }

  // ---- Wire UI ----
  $("#btnSend").onclick = () => send();
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });

  $$("#suggestions button").forEach((b) => {
    b.onclick = () => send(b.dataset.q);
  });

  $("#btnNewChat").onclick = async () => {
    if (busy) return;
    await api("/api/sessions/new", { method: "POST", body: "{}" });
    await refreshSessions();
    await loadSession();
  };
  $("#btnRename").onclick = async () => {
    if (busy) return;
    const title = prompt("Neuer Titel:");
    if (!title) return;
    await api("/api/sessions/rename", {
      method: "POST",
      body: JSON.stringify({ id: activeSession, title }),
    });
    refreshSessions();
    $("#chatTitle").textContent = title;
  };
  $("#btnDelete").onclick = async () => {
    if (busy) return;
    if (!confirm("Chat löschen?")) return;
    await api("/api/sessions/delete", {
      method: "POST",
      body: JSON.stringify({ id: activeSession }),
    });
    await refreshSessions();
    await loadSession();
  };
  $("#chatSearch").oninput = () => refreshSessions();

  $("#btnToggleSide").onclick = () => {
    const app = $("#nxApp");
    if (window.matchMedia("(max-width:800px)").matches) app.classList.toggle("side-open");
    else app.classList.toggle("side-collapsed");
  };
  $("#btnToggleRight").onclick = () => $("#nxApp").classList.toggle("right-collapsed");

  $$("#sideNav button").forEach((b) => {
    b.onclick = () => {
      $$("#sideNav button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      const v = b.dataset.view;
      const more = $("#sideMore");
      if (more && v !== "chat") more.open = true;
      closeAllWorkspacePanels();
      if (v === "chat") return;
      if (v === "home") { showWorkspacePanel("homePanel"); refreshHomeHub(); return; }
      if (v === "activity") { showWorkspacePanel("activityPanel"); refreshActivity(true); return; }
      if (v === "music") { showWorkspacePanel("musicPanel"); refreshMusic(); return; }
      if (v === "streams") { showWorkspacePanel("streamsPanel"); refreshStreams(); return; }
      if (v === "videos") { showWorkspacePanel("videosPanel"); refreshVideos(); return; }
      if (v === "galerie") { showWorkspacePanel("galleryPanel"); refreshGallery(); return; }
      if (v === "maps") { showWorkspacePanel("mapsPanel"); return; }
      if (v === "apps") { showWorkspacePanel("appsPanel"); Promise.resolve(refreshApps()).catch((e)=>addMsg("assistant","Apps-Fehler: "+e)); return; }
      if (v === "crew") { showWorkspacePanel("crewPanel"); Promise.resolve(refreshCrew()).catch((e)=>addMsg("assistant","Crew-Fehler: "+e)); return; }
      if (v === "memory") { showWorkspacePanel("memoryPanel"); Promise.resolve(refreshMemory()).catch((e)=>addMsg("assistant","Memory-Fehler: "+e)); return; }
      if (v === "skills") { showWorkspacePanel("skillsPanel"); Promise.resolve(refreshSkills()).catch((e)=>addMsg("assistant","Skills-Fehler: "+e)); return; }
      if (v === "library") { showWorkspacePanel("libraryPanel"); Promise.resolve(window.nxLibraryRefresh && window.nxLibraryRefresh()).catch((e)=>addMsg("assistant","Bibliothek-Fehler: "+e)); return; }
      if (v === "voice") { showWorkspacePanel("voicePanel"); Promise.resolve(refreshVoicePanel()).catch((e)=>addMsg("assistant","Voice-Fehler: "+e)); return; }
      if (v === "perf") { openPerfPanel(); return; }
      if (v === "files") { showWorkspacePanel("filesPanel"); refreshFiles(filesCursor || ""); return; }
      if (v === "projects") { showWorkspacePanel("projectsPanel"); refreshProjects(); return; }
      if (v === "downloads") { showWorkspacePanel("downloadsPanel"); refreshDownloads(); return; }
      if (v === "settings") { showWorkspacePanel("settingsPanel"); refreshSettings(); return; }
    };
  });

  const closeMap = [
    ["btnCloseFiles","filesPanel"],
    ["btnCloseProjects","projectsPanel"],
    ["btnCloseDownloads","downloadsPanel"],
    ["btnCloseSettings","settingsPanel"],
    ["btnCloseApps","appsPanel"],
    ["btnCloseCrew","crewPanel"],
    ["btnCloseVideos","videosPanel"],
    ["btnCloseGallery","galleryPanel"],
    ["btnCloseMaps","mapsPanel"],
    ["btnCloseMusic","musicPanel"],
    ["btnCloseStreams","streamsPanel"],
    ["btnCloseActivity","activityPanel"],
    ["btnCloseHome","homePanel"],
    ["btnCloseMemory","memoryPanel"],
    ["btnCloseLibrary","libraryPanel"],
    ["btnCloseSkills","skillsPanel"],
    ["btnCloseVoice","voicePanel"],
  ];
  closeMap.forEach(([bid]) => {
    const btn = document.getElementById(bid);
    if (!btn) return;
    btn.onclick = () => {
      closeAllWorkspacePanels();
      const chatBtn = document.querySelector('#sideNav button[data-view="chat"]');
      if (chatBtn) chatBtn.click();
    };
  });

  
  const btnMem = document.getElementById("btnMemoryRefresh");
  if (btnMem) btnMem.onclick = () => refreshMemory();
  const memorySearch = document.getElementById("memorySearch");
  if (memorySearch) memorySearch.onsubmit = event => { event.preventDefault(); refreshMemory(); };
  const btnSk = document.getElementById("btnSkillsRefresh");
  if (btnSk) btnSk.onclick = () => refreshSkills();
  const btnVo = document.getElementById("btnVoiceRefresh");
  if (btnVo) btnVo.onclick = () => refreshVoicePanel();

$("#btnFilesRefresh") && ($("#btnFilesRefresh").onclick = () => refreshFiles($("#filesPath").value.trim()));
  $("#btnFilesUp") && ($("#btnFilesUp").onclick = () => {
    const parts = (filesCursor || "").replace(/\\/g, "/").split("/").filter(Boolean);
    parts.pop();
    refreshFiles(parts.join("/"));
  });
$("#btnFilesExplorer") && ($("#btnFilesExplorer").onclick = async () => {
    await api("/api/os/open_path", { method: "POST", body: JSON.stringify({ path: "G:\\meine-ki\\nexus-ai\\workspace\\files" + (filesCursor ? ("\\" + filesCursor.replace(/\//g, "\\")) : "") }) });
  });
  $("#btnProjectsRefresh") && ($("#btnProjectsRefresh").onclick = () => refreshProjects());
  $("#btnProjectCreate") && ($("#btnProjectCreate").onclick = async () => {
    const name = ($("#projectName").value || "").trim();
    if (!name) return;
    await api("/api/projects", { method: "POST", body: JSON.stringify({ name }) });
    $("#projectName").value = "";
    refreshProjects();
  });
  $("#btnDownloadsRefresh") && ($("#btnDownloadsRefresh").onclick = () => refreshDownloads());
  $("#btnDownloadGo") && ($("#btnDownloadGo").onclick = async () => {
    const url = ($("#downloadUrl").value || "").trim();
    if (!url) return;
    const r = await api("/api/downloads", { method: "POST", body: JSON.stringify({ url }) });
    $("#downloadsStatus").textContent = r.reply || JSON.stringify(r);
    refreshDownloads();
  });
  $("#btnSettingsSave") && ($("#btnSettingsSave").onclick = async () => {
    const m = $("#settingsModel") && $("#settingsModel").value;
    if ($("#modelSelect") && m !== undefined) {
      $("#modelSelect").value = m;
      $("#modelSelect").dispatchEvent(new Event("change"));
    }
    try {
      if (m) {
        await api("/api/ai/models", { method: "POST", body: JSON.stringify({ chat_model: m, never_pull: true }) });
      }
    } catch (e) { /* offline settings still apply in UI */ }
    if ($("#settingsAutoSpeak")) setAutoSpeak(!!$("#settingsAutoSpeak").checked, false);
    /* MH4D-11 2026-11-10: tote settingsDesktopBg-Reste entfernt (Checkbox seit 02.11. weg, Schalter btnDesktopBg bleibt) */
    if ($("#settingsRightPanel")) {
      const want = !!$("#settingsRightPanel").checked;
      const collapsed = $("#nxApp").classList.contains("right-collapsed");
      if (want && collapsed) $("#nxApp").classList.remove("right-collapsed");
      if (!want && !collapsed) $("#nxApp").classList.add("right-collapsed");
    }
    addMsg("assistant", "Einstellungen übernommen.");
  });
  $("#btnSettingsHealth") && ($("#btnSettingsHealth").onclick = () => refreshSettings());



  const btnClosePerf = document.getElementById("btnClosePerf");
  if (btnClosePerf) {
    btnClosePerf.onclick = () => {
      closeAllWorkspacePanels();
      const chatBtn = document.querySelector('#sideNav button[data-view="chat"]');
      if (chatBtn) chatBtn.click();
    };
  }

  function playMediaItem(cur) {
    if (!cur) return;
    const audio = $("#nxAudio");
    const video = $("#nexusVideo");
    const embed = $("#nexusVideoEmbed");
    if (cur.kind === "video") {
      if (embed) { embed.classList.add("hide"); embed.removeAttribute("src"); }
      if (video) {
        video.classList.remove("hide");
        video.src = (cur.play_url || mediaUrl(cur.path));
        video.play().catch(() => {});
      }
      if ($("#videosPanel") && !$("#videosPanel").classList.contains("show")) showWorkspacePanel("videosPanel");
      else if ($("#videosPanel")) $("#videosPanel").classList.add("show");
      if (audio) audio.pause();
    } else {
      if (video) { video.pause(); }
      audio.src = mediaUrl(cur.path);
      audio.play().catch(() => {});
    }
  }

  $$(".media-btns button").forEach((b) => {
    b.onclick = async () => {
      const a = b.dataset.media;
      const r = await api("/api/media/" + a, { method: "POST", body: "{}" });
      const cur = r.state && r.state.current;
      const audio = $("#nxAudio");
      if (cur && (a === "play" || a === "next" || a === "prev")) {
        playMediaItem(cur);
      }
      if (a === "pause" || a === "stop") {
        audio.pause();
        const video = $("#nexusVideo");
        if (video) video.pause();
        if (a === "stop") {
          audio.currentTime = 0;
          if (video) video.currentTime = 0;
        }
      }
    };
  });


  $("#btnMusicRefresh") && ($("#btnMusicRefresh").onclick = () => refreshMusic());

  $("#btnGalleryRefresh") && ($("#btnGalleryRefresh").onclick = () => refreshGallery());
  $("#btnMapsGo") && ($("#btnMapsGo").onclick = () => setDesktopMap(($("#mapsQuery") && $("#mapsQuery").value) || ""));
  $("#mapsQuery") && $("#mapsQuery").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); setDesktopMap($("#mapsQuery").value); }
  });

  $("#btnCloseMusic") && ($("#btnCloseMusic").onclick = () => { closeAllWorkspacePanels(); const c=document.querySelector('#sideNav button[data-view="chat"]'); if(c) c.click(); });
  $$("[data-mctrl]").forEach((b) => {
    b.onclick = async () => {
      const a = b.getAttribute("data-mctrl");
      const r = await api("/api/media/" + a, { method: "POST", body: "{}" });
      const cur = r.state && r.state.current;
      const player = $("#nexusAudioPlayer") || $("#nxAudio");
      if (a === "play" || a === "next" || a === "prev") {
        if (cur && player) {
          player.src = mediaUrl(cur.path);
          player.play().catch(() => {});
        }
      }
      if (a === "pause" && player) player.pause();
      if (a === "stop" && player) { player.pause(); player.currentTime = 0; }
      refreshMusic();
    };
  });
  $("#btnMusicPlayUrl") && ($("#btnMusicPlayUrl").onclick = async () => {
    const url = ($("#musicUrl").value || "").trim();
    if (!url) return;
    const player = $("#nexusAudioPlayer") || $("#nxAudio");
    if (player) { player.src = url; player.play().catch(() => {}); }
    $("#musicNowTitle").textContent = url;
    $("#musicNowMeta").textContent = "Externe URL";
  });

  $("#btnMusicDl").onclick = async () => {
    const url = $("#musicUrl").value.trim();
    if (!url) return;
    const r = await api("/api/downloads", { method: "POST", body: JSON.stringify({ url }) });
    addMsg("assistant", r.reply || JSON.stringify(r));
  };

  $("#togVoice").onclick = () => {
    if (voice.live) stopLiveVoice();
    else startLiveVoice();
  };
  $("#btnLive").onclick = () => {
    if (voice.live) stopLiveVoice();
    else startLiveVoice();
  };
  $("#togScreen").onclick = () => {
    if (screen.active) stopScreen();
    else startScreen();
  };
  $("#togMic").onclick = () => {
    $("#togMic").classList.toggle("on");
  };
  $("#btnScreenAnalyze").onclick = () => analyzeScreen();
  $("#btnScreenStop").onclick = () => stopScreen();


  $("#btnVideoPlayUrl") && ($("#btnVideoPlayUrl").onclick = () => playVideoUrl());
  $("#btnVideoRefresh") && ($("#btnVideoRefresh").onclick = () => refreshVideos());
  $("#btnCloseVideos") && ($("#btnCloseVideos").onclick = () => {
    closeAllWorkspacePanels();
    const c = document.querySelector('#sideNav button[data-view="chat"]');
    if (c) c.click();
  });
  $("#btnCloseApps") && ($("#btnCloseApps").onclick = () => {
    closeAllWorkspacePanels();
    const c = document.querySelector('#sideNav button[data-view="chat"]');
    if (c) c.click();
  });
  $("#btnCloseAppFrame") && ($("#btnCloseAppFrame").onclick = () => {
    $("#appFrameWrap").classList.add("hide");
    $("#appFrame").removeAttribute("src");
  });

  $("#btnCloseStreams") && ($("#btnCloseStreams").onclick = () => {
    closeAllWorkspacePanels();
    const c = document.querySelector('#sideNav button[data-view="chat"]');
    if (c) c.click();
  });

  $("#btnStreamAdd").onclick = () => addStream();
  $("#btnStreamPlay").onclick = async () => {
    const data = await api("/api/streams");
    const s = (data.streams || []).find((x) => x.id === data.active) || (data.streams || [])[0];
    if (s) playStream(s);
    else addMsg("assistant", "Kein Stream vorhanden. URL eingeben und Hinzufügen.");
  };
  $("#btnStreamFollow").onclick = () => startFollow();
  $("#btnStreamStopFollow").onclick = () => {
    stopFollow();
    addMsg("assistant", "KI-Mitverfolgen gestoppt.");
  };

  $("#btnMic").onclick = () => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      addMsg("assistant", "Web Speech API nicht verfügbar. Nutze Chrome/Edge auf dem PC (http://127.0.0.1:8299/).");
      return;
    }
    if (voice.micRec) {
      try { voice.micRec.stop(); } catch (_) {}
      voice.micRec = null;
      micHoldOff();
      $("#togMic").classList.remove("on");
      setVoiceChip(voice.live ? "listening" : "off");
      return;
    }
    const rec = new SR();
    rec.lang = "de-DE";
    rec.interimResults = true;
    rec.continuous = false;
    voice.micRec = rec;
    micHoldOn(); // voicefix-20261006: Live-Erkennung pausieren, nie zwei Recognizer parallel
    $("#togMic").classList.add("on");
    setVoiceChip("listening");
    rec.onresult = async (ev) => {
      let finalText = "";
      let interim = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const t = ev.results[i][0].transcript;
        if (ev.results[i].isFinal) finalText += t;
        else interim += t;
      }
      if (interim) {
        input.value = interim;
        autosize();
      }
      if (finalText.trim()) {
        const cmd = stripWakeWord(finalText.trim());
        input.value = cmd;
        autosize();
        voice.fromMic = true;
        setVoiceChip("thinking");
        await send(cmd);
      }
    };
    rec.onerror = (e) => {
      if (e.error === "not-allowed") {
        addMsg("assistant", "Mikrofon-Zugriff verweigert. Browser-Berechtigung für 127.0.0.1 erlauben.");
      } else if (e.error !== "aborted" && e.error !== "no-speech") {
        addMsg("assistant", "Spracherkennung: " + e.error);
      }
      $("#togMic").classList.remove("on");
      voice.micRec = null;
      micHoldOff();
      setVoiceChip(voice.live ? "listening" : "off");
    };
    rec.onend = () => {
      $("#togMic").classList.remove("on");
      voice.micRec = null;
      micHoldOff();
      if (!busy && !voice.speaking) setVoiceChip(voice.live ? "listening" : "off");
    };
    try {
      rec.start();
    } catch (e) {
      addMsg("assistant", "Mic start fehlgeschlagen: " + (e.message || e));
      voice.micRec = null;
      $("#togMic").classList.remove("on");
      micHoldOff();
    }
  };


  // Extras collapsible row under composer
  (function wireExtras() {
    const btn = $("#btnExtras");
    const panel = $("#composerExtras");
    if (!btn || !panel) return;
    btn.onclick = () => {
      const open = panel.hasAttribute("hidden");
      if (open) panel.removeAttribute("hidden");
      else panel.setAttribute("hidden", "");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    };
  })();

  setVoiceChip("off");
  
  // Chat scroll: keep position when reading history; page buttons + PgUp/PgDn
  (function wireChatScroll() {
    const el = threadEl();
    if (!el) return;
    el.addEventListener("scroll", () => {
      stickToBottom = nearBottom(el, 100);
      el.classList.toggle("pinned-bottom", stickToBottom);
    }, { passive: true });
    const up = $("#btnScrollUp");
    const down = $("#btnScrollDown");
    if (up) up.onclick = () => pageScroll(-1);
    if (down) down.onclick = () => pageScroll(1);
    document.addEventListener("keydown", (e) => {
      const tag = (e.target && e.target.tagName) || "";
      if (tag === "TEXTAREA" || tag === "INPUT") {
        // allow PgUp/PgDn even from input for chat history
      }
      if (e.key === "PageUp") { e.preventDefault(); pageScroll(-1); }
      if (e.key === "PageDown") { e.preventDefault(); pageScroll(1); }
      if (e.key === "Home" && e.ctrlKey) { e.preventDefault(); el.scrollTo({ top: 0, behavior: "smooth" }); stickToBottom = false; }
      if (e.key === "End" && e.ctrlKey) { e.preventDefault(); stickToBottom = true; scrollThreadToBottom(true); }
    });
  })();

  const btnCrewClear = $("#btnCrewClear");
  if (btnCrewClear) {
    btnCrewClear.onclick = async () => {
      try {
        await api("/api/crew/active", { method: "POST", body: JSON.stringify({ id: null }) });
      } catch (_) {}
      applyCrewActiveUi(null);
      const hint = $("#crewActiveHint");
      if (hint) hint.textContent = "Kein Agent aktiv. Tippe eine Karte oder schreib @Coder …";
      btnCrewClear.hidden = true;
      const grid = $("#crewPanel.show #crewGrid") || $("#crewGrid");
      if (grid && $("#crewPanel") && $("#crewPanel").classList.contains("show")) {
        Promise.resolve(refreshCrew()).catch(() => {});
      } else {
        document.querySelectorAll(".crew-card.active").forEach((c) => c.classList.remove("active"));
        syncCrewPills([], null);
      }
    };
  }


  function setActivityChip(text, busy) {
    const chip = $("#chipActivity");
    if (!chip) return;
    const short = (text || "Bereit").replace(/\s+/g, " ").trim();
    chip.textContent = short.length > 36 ? short.slice(0, 34) + "…" : short;
    chip.classList.remove("ok", "warn", "hide");
    chip.classList.add(busy ? "warn" : "ok");
  }

  async function refreshActivity(detailed) {
    try {
      const lim = detailed ? 80 : 20;
      const data = await api("/api/activity?limit=" + lim);
      if (!data || !data.success) return;
      const cur = data.current || {};
      const phase = cur.phase || "idle";
      if (partnerRequest && partnerRequest.partner === "nexus" && !partnerRequest.executing &&
          phase === "tool" && Array.isArray(cur.tools) && cur.tools.length > 0 &&
          Number(cur.ts) * 1000 >= partnerRequest.started) {
        partnerRequest.executing = true;
        partnerState("nexus", "executing", { source: "activity", ...cur });
      }
      const busy = phase !== "idle" && phase !== "done";
      const label = cur.text || (busy ? "Arbeitet…" : "Bereit");
      setActivityChip(label, busy);
      const panel = $("#activityPanel");
      if (!panel || !panel.classList.contains("show")) return;
      const curEl = $("#activityCurrent");
      if (curEl) {
        const intent = cur.intent ? " · " + escapeHtml(String(cur.intent)) : "";
        curEl.innerHTML = "<strong>" + escapeHtml(phase) + "</strong>" + intent + "<br/>" + escapeHtml(label);
        curEl.className = "activity-current" + (busy ? " busy" : " idle");
      }
      const list = $("#activityLog");
      if (!list) return;
      const items = (data.items || []).slice().reverse();
      list.innerHTML = items.map((it) => {
        const ts = it.ts ? new Date(it.ts * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
        const tools = (it.tools && it.tools.length) ? " · " + escapeHtml(it.tools.join(", ")) : "";
        return "<li class=\"activity-item phase-" + escapeHtml(it.phase || "info") + "\">" +
          "<span class=\"activity-ts\">" + escapeHtml(ts) + "</span> " +
          "<span class=\"activity-phase\">" + escapeHtml(it.phase || "") + "</span> " +
          "<span class=\"activity-text\">" + escapeHtml(it.text || "") + "</span>" + tools +
          "</li>";
      }).join("") || "<li class=\"activity-item\">Noch keine Einträge</li>";
    } catch (e) {
      setActivityChip("Activity ERR", true);
    }
  }

  function refreshHomeHub() {
    const grid = $("#homeHubGrid");
    if (!grid) return;
    const actions = [
      { action: "call", label: "Anrufen", icon: "📞", sub: "/m" },
      { action: "improve", label: "Self-Improve", icon: "♻️", sub: "Queue" },
      { action: "health", label: "Health", icon: "💚", sub: "Score" },
    ];
    const tiles = [
      { view: "chat", label: "Chat", icon: "C" },
      { view: "crew", label: "Agenten", icon: "A" },
      { view: "files", label: "Dateien", icon: "D" },
      { view: "skills", label: "Skills", icon: "S" },
      { view: "library", label: "Bibliothek", icon: "📚" },
      { view: "memory", label: "Memory", icon: "M" },
      { view: "perf", label: "System", icon: "Y" },
      { view: "voice", label: "Voice", icon: "V" },
      { view: "apps", label: "Apps", icon: "P" },
      { view: "settings", label: "Einstellungen", icon: "E" },
      { view: "activity", label: "Aktivität", icon: "*" },
      { view: "projects", label: "Projekte", icon: "J" },
      { view: "music", label: "Musik", icon: "U" },
      { view: "videos", label: "Videos", icon: "W" },
      { view: "galerie", label: "Galerie", icon: "G" },
      { view: "maps", label: "Maps", icon: "O" },
      { view: "streams", label: "Streams", icon: "T" },
      { view: "downloads", label: "Downloads", icon: "L" },
    ];
    const actionHtml = actions.map((t) =>
      "<button type=\"button\" class=\"home-tile home-tile-action\" data-hub-action=\"" + t.action + "\">" +
      "<span class=\"home-tile-icon\">" + t.icon + "</span>" +
      "<span class=\"home-tile-label\">" + escapeHtml(t.label) + "</span>" +
      "<span class=\"home-tile-sub\">" + escapeHtml(t.sub) + "</span></button>"
    ).join("");
    const tileHtml = tiles.map((t) =>
      "<button type=\"button\" class=\"home-tile\" data-hub-view=\"" + t.view + "\">" +
      "<span class=\"home-tile-icon\">" + t.icon + "</span>" +
      "<span class=\"home-tile-label\">" + escapeHtml(t.label) + "</span></button>"
    ).join("");
    grid.innerHTML = actionHtml + tileHtml;
    grid.querySelectorAll("[data-hub-view]").forEach((btn) => {
      btn.onclick = () => {
        const v = btn.getAttribute("data-hub-view");
        const nav = document.querySelector('#sideNav button[data-view="' + v + '"]');
        if (nav) nav.click();
      };
    });
    grid.querySelectorAll("[data-hub-action]").forEach((btn) => {
      btn.onclick = () => {
        const a = btn.getAttribute("data-hub-action");
        if (a === "call") {
          // Handy-Call: on LAN HTTP open HTTPS :8443 so mic/STT works
          var host = location.hostname || "127.0.0.1";
          var secure = location.protocol === "https:" || host === "localhost" || host === "127.0.0.1";
          var url = secure ? "/m" : ("https://" + host + ":8443/m");
          window.open(url, "_blank", "noopener");
          return;
        }
        if (a === "improve") {
          closeAllWorkspacePanels();
          const chatBtn = document.querySelector('#sideNav button[data-view="chat"]');
          if (chatBtn) chatBtn.click();
          const inp = $("#nxInput");
          if (inp) { inp.value = "verbessere dich"; inp.focus(); }
          const sb = $("#btnSend");
          if (sb) sb.click();
          return;
        }
        if (a === "health") {
          refreshHealth();
          const nav = document.querySelector('#sideNav button[data-view="settings"]');
          if (nav) nav.click();
          const b = $("#btnSettingsHealth");
          if (b) setTimeout(() => b.click(), 80);
        }
      };
    });
    refreshHealth().catch(() => {});
  }


  // Activity chip click
  const chipAct = $("#chipActivity");
  if (chipAct) {
    chipAct.style.cursor = "pointer";
    chipAct.onclick = () => {
      const btn = document.querySelector('#sideNav button[data-view="activity"]');
      if (btn) btn.click();
      else { showWorkspacePanel("activityPanel"); refreshActivity(true); }
    };
  }
  const btnWelcomeHome = $("#btnWelcomeHome");
  if (btnWelcomeHome) {
    btnWelcomeHome.onclick = () => {
      const btn = document.querySelector('#sideNav button[data-view="home"]');
      if (btn) btn.click();
    };
  }
  setInterval(() => {
    const panelOpen = $("#activityPanel") && $("#activityPanel").classList.contains("show");
    refreshActivity(!!panelOpen);
  }, 2000);
  refreshActivity(false);

  refreshHealth();
  refreshSessions().then(() => loadSession(activeSession));
  refreshContext();
  syncCrewActiveChip();
  setInterval(refreshHealth, 15000);
  setInterval(syncCrewActiveChip, 20000);
  input.focus();

  // nexus_voice_msg_wired — hold Mic ~0.4s = record voice message → STT→chat→TTS
  (function wireVoiceMsg() {
    const btn = document.getElementById("btnMic");
    if (!btn || btn.dataset.voiceMsg === "1") return;
    btn.dataset.voiceMsg = "1";
    let rec = null;
    let stream = null;
    let chunks = [];
    let holdTimer = null;
    let recording = false;
    let pressAt = 0;

    async function startRec() {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
          ? "audio/webm;codecs=opus"
          : (MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "");
        chunks = [];
        rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
        rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
        rec.onstop = async () => {
          try { stream.getTracks().forEach((tr) => tr.stop()); } catch (e) {}
          stream = null;
          const blob = new Blob(chunks, { type: (rec && rec.mimeType) || "audio/webm" });
          recording = false;
          btn.classList.remove("is-recording");
          if (blob.size > 200) await voiceTurnFromBlob(blob, "mic.webm");
        };
        rec.start();
        recording = true;
        btn.classList.add("is-recording");
        setVoiceChip("listening");
      } catch (e) {
        addMsg("assistant", "Mic/Voice-Msg: " + (e.message || e));
      }
    }
    function stopRec() {
      if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; }
      if (recording && rec && rec.state === "recording") {
        try { rec.stop(); } catch (e) {}
      } else if (stream) {
        try { stream.getTracks().forEach((tr) => tr.stop()); } catch (e) {}
        stream = null;
      }
    }
    let suppressClick = false;
    btn.addEventListener("pointerdown", (e) => {
      if (e.button != null && e.button !== 0) return;
      pressAt = Date.now();
      suppressClick = false;
      holdTimer = setTimeout(function () { suppressClick = true; startRec(); }, 400);
    });
    ["pointerup", "pointerleave", "pointercancel"].forEach((ev) => {
      btn.addEventListener(ev, () => {
        if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; }
        if (recording) stopRec();
      });
    });
    btn.addEventListener("click", (e) => {
      if (suppressClick) {
        e.preventDefault();
        e.stopImmediatePropagation();
        suppressClick = false;
      }
    }, true);
  })();

})();
