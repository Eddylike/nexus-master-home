/* =========================================================
   composer.js — Direkte Bedienung innerhalb der Chamber.

   Ermöglicht:
    - Auftrag: über den vorhandenen NEXUS-Chat-/Auftragspfad (POST /api/chat).
    - Frage:   über denselben NEXUS-Pfad; die Antwort stammt von der
               Komponente, die den Kanal tatsächlich beantwortet (NEXUS und,
               bei "gemeinsam:"-Präfix, auch HermesBrain über SharedRoom).
    - Kommentar: wird ausschließlich im SharedRoom gespeichert (Session-ID,
               Zeitstempel, Sender USER) und wird NICHT als Auftrag ausgeführt.

   Keine zweite Ausführungsarchitektur, keine Demo-Nachrichten, kein
   automatisches Genehmigen. Privilegierte Aktionen laufen ausschließlich
   über die lokale Freigabeseite (/approvals).
   ========================================================= */
(function () {
  "use strict";
  const NEXUS = window.NEXUS;
  const U = NEXUS.util;

  const MODE_LABEL = {
    order: "Auftrag starten",
    ask: "Frage senden",
    comment: "Kommentar"
  };
  const MODE_HINT = {
    order: "Läuft über den NEXUS-Auftragspfad. Präfix \u201egemeinsam:\u201c aktiviert die NEXUS×HERMES-Kollaboration.",
    ask: "Die Antwort kommt von der Komponente, die den Kanal beantwortet (NEXUS bzw. bei \u201egemeinsam:\u201c auch Hermes).",
    comment: "Wird nur gespeichert (Sitzung · Zeitstempel · USER) und NICHT als Auftrag ausgeführt."
  };
  const ORDERLIKE = /\b(erstell\w*|schreib\w*|speicher\w*|erzeug\w*|entwickl\w*|implementier\w*|programmier\w*|reparier\w*|fix|baue|bauen)\b/i;

  const composer = {
    refs: {},
    sessionId: null,
    busy: false,
    mode: "order",
    target: "nexus",
    lastSent: { text: "", at: 0 },

    init() {
      this.refs = {
        text: document.getElementById("console-text"),
        send: document.getElementById("console-send"),
        modes: Array.from(document.querySelectorAll("[data-console-mode]")),
        targets: Array.from(document.querySelectorAll("[data-console-target]")),
        status: document.getElementById("console-status"),
        session: document.getElementById("console-session"),
        hint: document.getElementById("console-hint"),
        approvals: document.getElementById("console-approvals")
      };
      if (!this.refs.text || !this.refs.send) return;

      this.refs.modes.forEach((b) => b.addEventListener("click", () => this.setMode(b.dataset.consoleMode)));
      this.refs.targets.forEach((b) => b.addEventListener("click", () => this.setTarget(b.dataset.consoleTarget)));
      this.refs.send.addEventListener("click", () => this.send());
      this.refs.text.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" && !ev.shiftKey) {
          ev.preventDefault();
          this.send();
        }
      });
      if (this.refs.approvals) {
        this.refs.approvals.addEventListener("click", () => { window.location.href = "/approvals"; });
      }

      this.setMode(this.mode);
      setInterval(() => this.refreshSessionDisplay(), 2000);
      this.refreshSessionDisplay();
      return this;
    },

    setMode(mode) {
      if (!(mode in MODE_LABEL)) return;
      this.mode = mode;
      this.refs.modes.forEach((b) => b.classList.toggle("seg-on", b.dataset.consoleMode === mode));
      this.refs.send.textContent = MODE_LABEL[mode];
      const isComment = mode === "comment";
      this.refs.targets.forEach((b) => { b.hidden = isComment; });
      if (this.refs.hint) this.refs.hint.textContent = MODE_HINT[mode];
      if (this.refs.text) this.refs.text.placeholder = isComment
        ? "Kommentar zur laufenden Sitzung …"
        : (mode === "ask" ? "Frage an NEXUS / Hermes …" : "Auftrag an NEXUS …");
    },

    setTarget(target) {
      this.target = target;
      this.refs.targets.forEach((b) => b.classList.toggle("seg-on", b.dataset.consoleTarget === target));
    },

    async resolveSession() {
      const live = NEXUS.live;
      if (live && live.session) { this.sessionId = live.session; return this.sessionId; }
      try {
        const resp = await fetch("/api/shared-room/events", { cache: "no-store" });
        const data = await resp.json();
        if (data && data.session_id) { this.sessionId = data.session_id; return this.sessionId; }
      } catch (e) { /* offline transport => default session */ }
      this.sessionId = this.sessionId || null;
      return this.sessionId;
    },

    refreshSessionDisplay() {
      this.resolveSession().then(() => {
        if (!this.refs.session) return;
        this.refs.session.textContent = this.sessionId
          ? ("Sitzung: " + this.sessionId + " · bleibt bestehen")
          : "Sitzung: wartet auf Verbindung …";
      });
    },

    setStatus(text, kind) {
      if (!this.refs.status) return;
      this.refs.status.textContent = text;
      if (kind) this.refs.status.dataset.state = kind;
    },

    tooSoon(text) {
      const now = Date.now();
      if (text === this.lastSent.text && now - this.lastSent.at < 6000) return true;
      this.lastSent = { text, at: now };
      return false;
    },

    // SharedRoom-Eintrag als Human-Nutzereingabe (Sender USER).
    // Soll fehlschlagen dürfen ohne die Ausführung zu blockieren.
    postRoom(text, role) {
      if (!this.sessionId) return Promise.resolve(false);
      const payload = {
        session_id: this.sessionId,
        sender_type: "human",
        sender_name: "USER",
        role: role || "user",
        source: "human_input",
        mode: "user",
        content: text
      };
      return fetch("/api/shared-room/message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      }).then((r) => r.json()).then((d) => d && d.success === true).catch(() => false);
    },

    chatPath(text) {
      if (this.target === "hermes" && this.mode !== "comment" && ORDERLIKE.test(text)) {
        return "gemeinsam: " + text;
      }
      return text;
    },

    async send() {
      if (this.busy) { U.toast("Es läuft bereits eine Übermittlung …", "warn", 2400); return; }
      const text = (this.refs.text.value || "").trim();
      if (!text) { U.toast("Bitte zuerst etwas eingeben.", "warn", 2400); return; }
      if (this.tooSoon(text)) { U.toast("Identische Übermittlung gerade eben versendet.", "warn", 2600); return; }
      if (text.toLowerCase() === "vorstellen" && window.parent && window.parent !== window) {
        window.parent.dispatchEvent(new CustomEvent("nexus:send", { detail: "vorstellen" }));
        this.setStatus("Vorstellung an NEXUS → HERMES → AETHER übergeben.", "ok");
        this.refs.text.value = "";
        return;
      }

      this.busy = true;
      this.refs.send.disabled = true;
      this.refs.send.textContent = "…";

      await this.resolveSession();
      const submitted = this.sessionId;
      try {
        if (this.mode === "comment") {
          this.setStatus("Speichere Kommentar …");
          const saved = await this.postRoom(text, "comment");
          if (saved) {
            NEXUS.store.addMessage({ from: "human", to: "system", role: "comment", kind: "comment",
              content: "Kommentar: " + text, source: "human_input", demo: false });
            this.setStatus("Kommentar gespeichert (Sitzung · " + (this.sessionId || "default") + " · USER)", "ok");
            U.toast("Kommentar gespeichert.", "good", 2600);
          } else {
            this.setStatus("Kommentar konnte nicht gespeichert werden (Room nicht erreichbar).", "bad");
            U.toast("Kommentar nicht gespeichert.", "bad", 3200);
          }
          this.refs.text.value = "";
          return;
        }

        if (window.parent && window.parent !== window) {
          window.parent.dispatchEvent(new CustomEvent("nexus:send", { detail: { text, partner: this.target, source: "chamber" } }));
          this.setStatus("An gemeinsamen Chatpfad übergeben · " + this.target.toUpperCase(), "ok");
          return;
        }

        // Standalone-Fallback: erhalten, falls die Chamber direkt statt im Hauptchat geöffnet wird.
        const directHermes = this.target === "hermes";
        const endpoint = directHermes ? "/api/hermes/agent" : "/api/chat";
        const payload = directHermes
          ? { task: text, session_id: submitted || undefined }
          : { message: this.chatPath(text), session_id: submitted || undefined, mode: "" };
        this.setStatus(directHermes
          ? "Nachricht an HERMES …"
          : (this.mode === "ask" ? "Frage an NEXUS …" : "Auftrag gesendet · NEXUS arbeitet …"), "busy");
        const result = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        }).then((r) => r.json()).catch(() => ({ success: false, error: directHermes ? "hermes_unreachable" : "chat_unreachable" }));

        const data = (result && result.data) || {};
        const isCollab = !directHermes && (data.collaboration === true || !!data.run_id);
        this.setStatus(directHermes
          ? (result.success ? "HERMES hat geantwortet." : "HERMES meldet Fehler.")
          : (this.mode === "ask" ? "Frage beantwortet." : (result.success ? "Auftrag abgeschlossen · Status " + (result.status || "OK") : "Auftrag meldet " + (result.status || "Fehler"))), result.success ? "ok" : "bad");

        if (isCollab) {
          // Kollaborations-Verlauf kommt live über /api/shared-room/events (NEXUS/Hermes/Tasks).
          // Kein Duplikat clientseitig einblenden.
        } else if (result && typeof result.reply === "string" && result.reply.trim()) {
          NEXUS.store.addMessage({ from: directHermes ? "hermes" : "nexus", to: "human", role: "chat_result", kind: "chat_result",
            content: result.reply, source: directHermes ? "api_hermes_agent" : "api_chat", demo: false });
        }

        if (!result.success || String(result.status || "").toUpperCase() in { BLOCKED: 1, PARTIAL: 1 } ||
            /freigabe|approval|privileg|freigeben/i.test(String(result.reply || ""))) {
          if (this.refs.approvals) { this.refs.approvals.style.display = "inline-block"; }
          U.toast("Blocker/Freigabe: Prüfe die lokale Freigabeseite (/approvals).", "warn", 4200);
        }
      } catch (err) {
        this.setStatus("Übermittlung fehlgeschlagen: " + String(err.message || err), "bad");
        U.toast("Übermittlung fehlgeschlagen.", "bad", 3600);
      } finally {
        this.busy = false;
        this.refs.send.disabled = false;
        this.refs.send.textContent = MODE_LABEL[this.mode];
        this.refs.text.value = "";
      }
    }
  };

  NEXUS.composer = composer;
})();