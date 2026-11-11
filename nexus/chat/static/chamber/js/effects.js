/* =========================================================
   effects.js — Partikel, Convergence-Pulse, Energieausbrüche
   ========================================================= */
(function () {
  "use strict";
  const NEXUS = window.NEXUS;
  const U = NEXUS.util;

  const Effects = {
    ambient: null,
    shocks: [],

    init() {
      const THREE = NEXUS.scene.THREE;
      const S = NEXUS.scene;

      // ---- Ambient-Partikel (Schale um die Erde) ----
      const n = S.params.particles;
      const pos = new Float32Array(n * 3);
      const spd = new Float32Array(n);
      const col = new Float32Array(n * 3);
      const pal = [new THREE.Color(0x35e6ff), new THREE.Color(0xffcf6b), new THREE.Color(0xb06bff)];
      for (let i = 0; i < n; i++) {
        const r = U.rand(30, 120);
        const th = U.rand(0, Math.PI * 2);
        const ph = Math.acos(U.rand(-1, 1));
        pos[i * 3] = r * Math.sin(ph) * Math.cos(th);
        pos[i * 3 + 1] = r * Math.cos(ph) * 0.7;
        pos[i * 3 + 2] = r * Math.sin(ph) * Math.sin(th);
        spd[i] = U.rand(0.1, 0.5);
        const c = Math.random() < 0.7 ? pal[0] : U.pick(pal);
        col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b;
      }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
      const mat = new THREE.PointsMaterial({ size: 0.9, map: this._disc(), vertexColors: true, transparent: true, opacity: 0.75, blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true });
      const pts = new THREE.Points(geo, mat);
      S.scene.add(pts);
      this.ambient = { pts, base: pos.slice(), spd };

      // ---- Shockwave-Pool (Convergence-Puls) ----
      for (let i = 0; i < 5; i++) {
        const ring = new THREE.Mesh(
          new THREE.TorusGeometry(1, 0.35, 8, 120),
          new THREE.MeshBasicMaterial({ color: 0x58f0a6, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false })
        );
        ring.rotation.x = Math.PI / 2;
        ring.visible = false;
        S.scene.add(ring);
        this.shocks.push({ ring, active: false, t: 0, life: 1.8, color: 0x58f0a6, max: 120 });
      }

      S.add((dt, t, sc) => this._update(dt, t, sc));

      // Convergence-Puls bei Entscheidung
      NEXUS.bus.on("convergence", (v) => { if (v > 0.85) NEXUS.effects.shockwave(0x58f0a6, v); });
      return this;
    },

    _disc() {
      const cv = document.createElement("canvas");
      cv.width = cv.height = 64;
      const ctx = cv.getContext("2d");
      const g = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
      g.addColorStop(0, "rgba(255,255,255,1)");
      g.addColorStop(0.4, "rgba(180,230,255,0.6)");
      g.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, 64, 64);
      const tex = new THREE.CanvasTexture(cv);
      return tex;
    },

    shockwave(color, strength = 1) {
      const s = this.shocks.find((x) => !x.active);
      if (!s) return;
      s.active = true;
      s.t = 0;
      s.color = color;
      s.max = 90 + strength * 90;
      s.ring.visible = true;
      s.ring.material.color.setHex(color);
      s.ring.scale.setScalar(1);
      s.ring.material.opacity = 0.9;
    },

    _update(dt, t, sc) {
      // Ambient-Driftschimmer
      if (this.ambient) {
        const a = this.ambient;
        a.pts.rotation.y += dt * 0.012 * (sc.reduced ? 0.2 : 1);
        a.pts.material.opacity = 0.55 + 0.2 * Math.sin(t * 1.5);
      }
      // Shockwaves
      const speed = sc.reduced ? 0.5 : 1;
      this.shocks.forEach((s) => {
        if (!s.active) return;
        s.t += dt * speed;
        const k = s.t / s.life;
        if (k >= 1) { s.active = false; s.ring.visible = false; return; }
        const r = U.lerp(28, s.max, k);
        s.ring.scale.setScalar(r);
        s.ring.material.opacity = (1 - k) * 0.85;
        s.ring.rotation.z += dt * 0.6 * speed;
      });
    }
  };

  NEXUS.effects = Effects;
})();
