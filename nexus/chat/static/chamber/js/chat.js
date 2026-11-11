/* =========================================================
   chat.js — Schwebende Chat-Hologramme + Besprechungsansicht + Diskussion
   ========================================================= */
(function () {
  "use strict";
  const NEXUS = window.NEXUS;
  const U = NEXUS.util;

  const FROM_NAME = {
    nexus: "NEXUS", hermes: "HERMES", system: "NEXUS OS",
    human: "EDDY", local_ai: "LOCAL AI", chatgpt: "CHATGPT"
  };
  const DISC_STATUS = {
    idle: "Bereit", active: "Diskussion", converge: "Konvergiert",
    diverge: "Divergiert", blocked: "Blockiert"
  };

  const Chat = {
    stream: null, meetingBody: null, meeting: null, meetingTopic: null,
    discussionTopic: null, roundTrack: null, voteGrid: null,
    chipDisc: null, chipRound: null,

    init() {
      this.stream = document.getElementById("chat-stream");
      this.meetingBody = document.getElementById("meeting-body");
      this.meeting = document.getElementById("meeting");
      this.meetingTopic = document.getElementById("meeting-topic");
      this.discussionTopic = document.getElementById("discussion-topic");
      this.roundTrack = document.getElementById("round-track");
      this.voteGrid = document.getElementById("vote-grid");
      this.chipDisc = document.getElementById("chip-discussion");
      this.chipRound = document.getElementById("chip-round");

      document.getElementById("btn-meeting").addEventListener("click", () => this.toggleMeeting());
      document.getElementById("btn-meeting-close").addEventListener("click", () => this.hideMeeting());

      NEXUS.bus.on("message", (m) => {
        this._renderMessage(this.stream, m, 40);
        if (this.meetingBody) this._renderMessage(this.meetingBody, m, 160);
      });
      NEXUS.bus.on("discussion", (d) => this._renderDiscussion(d));
      NEXUS.bus.on("reset", () => {
        if (this.stream) this.stream.innerHTML = "";
        if (this.meetingBody) this.meetingBody.innerHTML = "";
        this._renderDiscussion(NEXUS.store.get().discussion);
      });

      this._renderDiscussion(NEXUS.store.get().discussion);
      return this;
    },

    _msgNode(m) {
      const node = U.el("div", "msg");
      node.dataset.from = m.from || "system";
      node.dataset.kind = m.kind || m.role || "message";

      const head = U.el("div", "msg-head");
      head.appendChild(U.el("span", "msg-from", U.escape(FROM_NAME[m.from] || m.from || "SYSTEM")));
      if (m.to) head.appendChild(U.el("span", "mini", "→ " + U.escape(FROM_NAME[m.to] || m.to)));
      head.appendChild(U.el("span", "msg-time", U.timeShort(m.ts)));
      if (m.demo) head.appendChild(U.el("span", "msg-demo", "DEMO"));
      node.appendChild(head);

      node.appendChild(U.el("div", "msg-body", U.escape(m.content || "")));
      return node;
    },

    _renderMessage(container, m, limit) {
      if (!container) return;
      container.appendChild(this._msgNode(m));
      while (container.children.length > limit) container.removeChild(container.firstChild);
      container.scrollTop = container.scrollHeight;
    },

    _renderDiscussion(d) {
      if (!d) return;
      if (this.discussionTopic) this.discussionTopic.textContent = d.topic || "—";
      if (this.meetingTopic) this.meetingTopic.textContent = d.topic && d.topic !== "—" ? d.topic : "Keine aktive Diskussion";

      if (this.chipDisc) {
        this.chipDisc.textContent = DISC_STATUS[d.status] || d.status || "Bereit";
        this.chipDisc.dataset.state = d.status === "active" ? "active" : (d.status === "converge" ? "converge" : (d.status === "blocked" ? "blocked" : "idle"));
      }
      if (this.chipRound) this.chipRound.textContent = `Runde ${d.round || 0}/${d.maxRounds || 6}`;

      // Runden-Track
      if (this.roundTrack) {
        this.roundTrack.innerHTML = "";
        const max = d.maxRounds || 6;
        for (let i = 1; i <= max; i++) {
          const dot = U.el("span", "round-dot");
          if (i < d.round) dot.classList.add("done");
          else if (i === d.round && d.status === "active") dot.classList.add("active");
          else if (i <= d.round) dot.classList.add("done");
          this.roundTrack.appendChild(dot);
        }
      }

      // Abstimmungen
      if (this.voteGrid) {
        this.voteGrid.innerHTML = "";
        const votes = d.votes || [];
        if (!votes.length) {
          this.voteGrid.appendChild(U.el("div", "mini", "Keine Abstimmung."));
        } else {
          votes.forEach((v) => {
            const cell = U.el("div", "vote");
            cell.dataset.choice = v.choice;
            cell.appendChild(U.el("small", null, U.escape(FROM_NAME[v.agent] || v.agent)));
            cell.appendChild(U.el("b", null, v.choice === "yes" ? "ZUSTIMMUNG" : v.choice === "no" ? "ABLEHNUNG" : "HALT"));
            this.voteGrid.appendChild(cell);
          });
        }
      }
    },

    toggleMeeting() { this.meeting.hidden ? this.showMeeting() : this.hideMeeting(); },
    showMeeting() {
      this.meeting.hidden = false;
      NEXUS.store.patch({ view: "meeting" });
      if (this.meetingBody) this.meetingBody.scrollTop = this.meetingBody.scrollHeight;
    },
    hideMeeting() {
      this.meeting.hidden = true;
      NEXUS.store.patch({ view: "command" });
    }
  };

  NEXUS.chat = Chat;
})();
