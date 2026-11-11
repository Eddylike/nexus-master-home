/* =========================================================
   camera.js — Startssequenz, Kamerafahrten, Auto-Orbit
   ========================================================= */
(function () {
  "use strict";
  const NEXUS = window.NEXUS;
  const U = NEXUS.util;

  const easeInOut = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);

  const RIDES = ["wide", "orbit-high", "close-earth", "nexus", "hermes", "top"];

  const Cam = {
    mode: "intro",
    pos: null, look: null,
    dPos: null, dLook: null,
    introKey: [],
    introT: 0, introDur: 0,
    rideIdx: 0, rideTimer: 0,
    _tmp: null,

    init() {
      const THREE = NEXUS.scene.THREE;
      this.pos = new THREE.Vector3().copy(NEXUS.scene.camera.position);
      this.look = new THREE.Vector3(0, 0, 0);
      this.dPos = new THREE.Vector3(0, 260, 520);
      this.dLook = new THREE.Vector3(0, 0, 0);
      this._tmp = new THREE.Vector3();

      this._buildIntro();
      NEXUS.scene.add((dt, t, sc) => this._update(dt, t, sc));
      return this;
    },

    _buildIntro() {
      const V = NEXUS.scene.THREE.Vector3;
      const kf = [
        { t: 0.0, pos: [0, 300, 620], look: [0, 0, 0] },
        { t: 0.30, pos: [0, 120, 210], look: [0, 0, 0] },
        { t: 0.55, pos: [10, -14, 52], look: [0, 0, 0] },
        { t: 0.78, pos: [78, 44, 46], look: [0, 0, 0] },
        { t: 1.0, pos: [0, 60, 150], look: [0, 0, 0] }
      ];
      this.introKey = kf;
      this.introDur = NEXUS.scene.reduced ? 3.2 : 10.0;
    },

    playIntro() {
      this.mode = "intro";
      this.introT = 0;
      NEXUS.bus.emit("camera:mode", "intro");
    },

    cycleRide() {
      this.mode = "ride";
      this.rideIdx = (this.rideIdx + 1) % RIDES.length;
      this.rideTimer = 0;
      NEXUS.bus.emit("camera:mode", "ride");
      NEXUS.util.toast("Kamerafahrt: " + RIDES[this.rideIdx], "info", 1600);
    },

    setIdle() { this.mode = "idle"; NEXUS.bus.emit("camera:mode", "idle"); },

    _viewFor(name) {
      const V = NEXUS.scene.THREE.Vector3;
      const earthR = NEXUS.earth.radius;
      switch (name) {
        case "wide": return { pos: new V(0, 62, 152), look: new V(0, 0, 0) };
        case "orbit-high": return { pos: new V(96, 96, 62), look: new V(0, 0, 0) };
        case "close-earth": return { pos: new V(0, 16, earthR + 30), look: new V(0, 0, 0) };
        case "nexus": return { agent: "nexus" };
        case "hermes": return { agent: "hermes" };
        case "top": return { pos: new V(0, 168, 0.01), look: new V(0, 0, 0) };
        default: return { pos: new V(0, 62, 152), look: new V(0, 0, 0) };
      }
    },

    _applyAgentView(id, outPos, outLook) {
      const p = NEXUS.agents.getPosition(id);
      // Kamera etwas außerhalb, radial nach außen versetzt
      const dir = this._tmp.copy(p).normalize();
      outLook.copy(p);
      outPos.copy(p).add(dir.multiplyScalar(18)).add(new (NEXUS.scene.THREE.Vector3)(0, 6, 0));
    },

    _update(dt, t, sc) {
      const k = sc.reduced ? 4 : 2.2;
      const s = 1 - Math.exp(-k * dt);

      if (this.mode === "intro") {
        this.introT += dt / this.introDur;
        const p = U.clamp(this.introT, 0, 1);
        // Segment finden
        let a = this.introKey[0], b = this.introKey[this.introKey.length - 1];
        for (let i = 0; i < this.introKey.length - 1; i++) {
          if (p >= this.introKey[i].t && p <= this.introKey[i + 1].t) { a = this.introKey[i]; b = this.introKey[i + 1]; break; }
        }
        const span = Math.max(1e-4, b.t - a.t);
        const local = easeInOut(U.clamp((p - a.t) / span, 0, 1));
        this.dPos.set(U.lerp(a.pos[0], b.pos[0], local), U.lerp(a.pos[1], b.pos[1], local), U.lerp(a.pos[2], b.pos[2], local));
        this.dLook.set(0, 0, 0);
        this.pos.lerp(this.dPos, s * 1.6);
        this.look.lerp(this.dLook, s * 1.6);
        if (p >= 1) this.setIdle();
      } else if (this.mode === "ride") {
        this.rideTimer += dt;
        if (this.rideTimer > 7) this.cycleRide();
        const v = this._viewFor(RIDES[this.rideIdx]);
        if (v.agent) this._applyAgentView(v.agent, this.dPos, this.dLook);
        else { this.dPos.copy(v.pos); this.dLook.copy(v.look); }
        this.pos.lerp(this.dPos, s);
        this.look.lerp(this.dLook, s);
      } else {
        // idle: langsamer Orbit + Parallaxe
        const ang = t * 0.03 * (sc.reduced ? 0.25 : 1);
        const rad = 152;
        const px = Math.sin(ang) * rad + sc.pointer.x * 18;
        const pz = Math.cos(ang) * rad;
        const py = 62 + Math.sin(t * 0.18) * 8 - sc.pointer.y * 10;
        this.dPos.set(px, py, pz);
        this.dLook.set(0, 0, 0);
        this.pos.lerp(this.dPos, s);
        this.look.lerp(this.dLook, s);
      }

      NEXUS.scene.camera.position.copy(this.pos);
      NEXUS.scene.camera.lookAt(this.look);
    }
  };

  NEXUS.camera = Cam;
  NEXUS.CAMERA_RIDES = RIDES;
})();
