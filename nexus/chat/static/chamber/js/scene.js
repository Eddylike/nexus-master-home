/* =========================================================
   scene.js — Three.js Kern: Renderer, Kamera, Frame-Loop, Qualität
   ========================================================= */
(function () {
  "use strict";
  const NEXUS = window.NEXUS;
  const U = NEXUS.util;

  const QUALITY = {
    high:   { dpr: 1.9, stars: 9000, particles: 2200, rings: 3, seg: 96 },
    medium: { dpr: 1.4, stars: 5200, particles: 1200, rings: 3, seg: 72 },
    low:    { dpr: 1.0, stars: 2600, particles: 600,  rings: 2, seg: 48 }
  };

  const Scene = {
    THREE: window.THREE,
    inited: false,
    quality: "high",
    reduced: false,
    scene: null,
    camera: null,
    renderer: null,
    canvas: null,
    clock: null,
    elapsed: 0,
    _updaters: [],
    pointer: { x: 0, y: 0, tx: 0, ty: 0 },
    params: QUALITY.high,
    active: true,

    init(canvas, opts = {}) {
      const THREE = this.THREE;
      if (!THREE) { console.error("Three.js nicht geladen."); return; }

      this.canvas = canvas;
      this.reduced = U.reducedMotion();
      this.quality = opts.quality || U.guessQuality();
      this.params = QUALITY[this.quality] || QUALITY.high;

      this.renderer = new THREE.WebGLRenderer({ canvas, antialias: this.quality !== "low", alpha: true, powerPreference: "high-performance" });
      this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, this.params.dpr));
      this.renderer.setClearColor(0x000000, 0);

      this.scene = new THREE.Scene();
      this.scene.fog = new THREE.FogExp2(0x05070f, 0.0022);

      this.camera = new THREE.PerspectiveCamera(52, 1, 0.1, 4000);
      this.camera.position.set(0, 34, 118);

      this.clock = new THREE.Clock();

      if (this.reduced) this.quality = this.quality === "high" ? "medium" : this.quality;

      this._bindEvents();
      this.resize();
      this.inited = true;
      this._loop();
    },

    setQuality(q) {
      if (!QUALITY[q]) return;
      this.quality = q;
      this.params = QUALITY[q];
      this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, this.params.dpr));
      this.resize();
      NEXUS.bus.emit("quality", q);
    },

    add(updater) { this._updaters.push(updater); },

    _bindEvents() {
      window.addEventListener("resize", () => this.resize(), { passive: true });
      window.addEventListener("pointermove", (e) => {
        this.pointer.tx = (e.clientX / window.innerWidth) * 2 - 1;
        this.pointer.ty = (e.clientY / window.innerHeight) * 2 - 1;
      }, { passive: true });
    },

    resize() {
      if (!this.renderer) return;
      const w = window.innerWidth, h = window.innerHeight;
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
      this.renderer.setSize(w, h, false);
    },

    _loop() {
      const THREE = this.THREE;
      requestAnimationFrame(() => this._loop());
      if (!this.active) return;

      const dt = Math.min(this.clock.getDelta(), 0.05);
      this.elapsed += dt;

      // Pointer-Trägheit
      this.pointer.x = U.lerp(this.pointer.x, this.pointer.tx, 0.05);
      this.pointer.y = U.lerp(this.pointer.y, this.pointer.ty, 0.05);

      for (let i = 0; i < this._updaters.length; i++) {
        try { this._updaters[i](dt, this.elapsed, this); } catch (e) { console.error("[updater]", e); }
      }

      this.renderer.render(this.scene, this.camera);
    },

    setActive(v) { this.active = !!v; }
  };

  NEXUS.scene = Scene;
})();
