/* =========================================================
   agents.js — Holografische KI-Kerne: NEXUS, HERMES, NEXUS OS
   ========================================================= */
(function () {
  "use strict";
  const NEXUS = window.NEXUS;
  const U = NEXUS.util;

  const DEF = {
    nexus:  { color: 0x35e6ff, radius: 6.2, orbit: 60, y: 8,  speed: 0.16,  phase: 0,            label: "NEXUS",     role: "MASTER HOME" },
    hermes: { color: 0xffcf6b, radius: 5.6, orbit: 60, y: -6, speed: 0.16,  phase: Math.PI,      label: "HERMES",    role: "EXECUTOR BRIDGE" },
    system: { color: 0xb06bff, radius: 4.0, orbit: 40, y: -26, speed: -0.10, phase: Math.PI / 2, label: "NEXUS OS",  role: "SYSTEM" }
  };

  const Agents = {
    cores: {},

    _makeLabel(text, sub, color) {
      const cv = document.createElement("canvas");
      cv.width = 512; cv.height = 128;
      const ctx = cv.getContext("2d");
      ctx.clearRect(0, 0, 512, 128);
      ctx.font = "700 46px 'Segoe UI', sans-serif";
      ctx.fillStyle = "#" + new THREE.Color(color).getHexString();
      ctx.textAlign = "center";
      ctx.shadowColor = "#" + new THREE.Color(color).getHexString();
      ctx.shadowBlur = 22;
      ctx.fillText(text, 256, 56);
      ctx.shadowBlur = 0;
      ctx.font = "400 24px 'Segoe UI', sans-serif";
      ctx.fillStyle = "rgba(220,240,255,0.75)";
      ctx.fillText(sub, 256, 96);
      const tex = new THREE.CanvasTexture(cv);
      const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false, depthTest: false });
      const sp = new THREE.Sprite(mat);
      sp.scale.set(16, 4, 1);
      return sp;
    },

    _makeCore(cfg) {
      const THREE = NEXUS.scene.THREE;
      const g = new THREE.Group();
      g.name = "core-" + cfg.label;

      const r = cfg.radius;

      // Kern-Wireframe
      const wire = new THREE.Mesh(
        new THREE.IcosahedronGeometry(r, 1),
        new THREE.MeshBasicMaterial({ color: cfg.color, wireframe: true, transparent: true, opacity: 0.85, blending: THREE.AdditiveBlending, depthWrite: false })
      );
      g.add(wire);

      // Innerer Glühkern
      const inner = new THREE.Mesh(
        new THREE.IcosahedronGeometry(r * 0.55, 0),
        new THREE.MeshBasicMaterial({ color: cfg.color, transparent: true, opacity: 0.9, blending: THREE.AdditiveBlending, depthWrite: false })
      );
      g.add(inner);

      // Äußere Hülle
      const shell = new THREE.Mesh(
        new THREE.SphereGeometry(r * 1.7, 32, 32),
        new THREE.MeshBasicMaterial({ color: cfg.color, transparent: true, opacity: 0.10, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.BackSide })
      );
      g.add(shell);

      // Rotierende Energie-Ringe
      const rings = [];
      for (let i = 0; i < 2; i++) {
        const ring = new THREE.Mesh(
          new THREE.TorusGeometry(r * (1.35 + i * 0.25), 0.08, 8, 64),
          new THREE.MeshBasicMaterial({ color: i === 0 ? cfg.color : 0xffffff, transparent: true, opacity: 0.6 - i * 0.2, blending: THREE.AdditiveBlending, depthWrite: false })
        );
        ring.rotation.x = U.rand(0, Math.PI);
        ring.rotation.z = U.rand(0, Math.PI);
        g.add(ring);
        rings.push(ring);
      }

      // Licht
      const light = new THREE.PointLight(cfg.color, 1.2, 220, 2);
      g.add(light);

      // Label
      const label = this._makeLabel(cfg.label, cfg.role, cfg.color);
      label.position.set(0, r + 7, 0);
      g.add(label);

      // Statusring (Äquator)
      const statusRing = new THREE.Mesh(
        new THREE.TorusGeometry(r * 2.05, 0.06, 6, 80),
        new THREE.MeshBasicMaterial({ color: cfg.color, transparent: true, opacity: 0.35, blending: THREE.AdditiveBlending, depthWrite: false })
      );
      statusRing.rotation.x = Math.PI / 2;
      g.add(statusRing);

      return { group: g, wire, inner, shell, rings, light, label, statusRing, cfg, pulse: 0, active: false, load: 0.1 };
    },

    init() {
      const THREE = NEXUS.scene.THREE;
      const S = NEXUS.scene;

      Object.keys(DEF).forEach((id) => {
        const cfg = DEF[id];
        const core = this._makeCore(cfg);
        const holder = new THREE.Group();
        holder.add(core.group);
        S.scene.add(holder);
        core.holder = holder;
        // Startposition
        const a = cfg.phase;
        holder.position.set(Math.cos(a) * cfg.orbit, cfg.y, Math.sin(a) * cfg.orbit);
        this.cores[id] = core;
      });

      S.add((dt, t, sc) => {
        const s = sc.reduced ? 0.3 : 1;
        Object.keys(this.cores).forEach((id) => {
          const c = this.cores[id];
          const cfg = c.cfg;
          // Orbitalbewegung
          const a = cfg.phase + t * cfg.speed * s;
          c.holder.position.x = Math.cos(a) * cfg.orbit;
          c.holder.position.z = Math.sin(a) * cfg.orbit;
          c.holder.position.y = cfg.y + Math.sin(t * 0.6 + cfg.phase) * 3;

          // Eigenrotation
          c.group.rotation.y += dt * 0.35 * s;
          c.group.rotation.x += dt * 0.12 * s;
          c.rings[0].rotation.z += dt * 0.9 * s;
          c.rings[1].rotation.x -= dt * 0.7 * s;
          c.statusRing.rotation.z += dt * 0.5 * s;

          // Puls (Ruhe + Aktivität + Auslöser)
          c.pulse = Math.max(0, c.pulse - dt * 1.6);
          const breathe = 0.5 + 0.5 * Math.sin(t * 2.0 + cfg.phase);
          const energy = c.active ? 1.0 : 0.45;
          const baseScale = 1 + (breathe * 0.05 + c.pulse * 0.35) * energy;
          c.wire.scale.setScalar(baseScale);
          c.inner.scale.setScalar(1 + (breathe * 0.08 + c.pulse * 0.5) * energy);

          const targetOp = (c.active ? 0.96 : 0.7) + c.pulse * 0.3;
          c.wire.material.opacity = U.lerp(c.wire.material.opacity, targetOp, 0.1);
          c.shell.material.opacity = 0.06 + c.load * 0.18 + c.pulse * 0.25;
          c.light.intensity = U.lerp(c.light.intensity, (c.active ? 2.0 : 1.0) + c.pulse * 1.5, 0.08);
          c.statusRing.material.opacity = 0.18 + c.load * 0.5 + c.pulse * 0.4;

          // Label immer zur Kamera
          c.label.quaternion.copy(sc.camera.quaternion);
        });
      });

      return this;
    },

    setStatus(id, patch) {
      const c = this.cores[id];
      if (!c) return;
      if (patch.active != null) c.active = !!patch.active;
      if (patch.load != null) c.load = U.clamp(patch.load, 0, 1);
      if (patch.status) {
        c.active = ["active", "running", "thinking", "working"].includes(patch.status);
      }
    },

    pulse(id, strength = 1) {
      const c = this.cores[id];
      if (!c) return;
      c.pulse = Math.min(1.6, (c.pulse || 0) + strength);
    },

    getPosition(id) {
      const c = this.cores[id];
      if (!c) return new THREE.Vector3();
      const v = new THREE.Vector3();
      c.group.getWorldPosition(v);
      return v;
    },

    setConvergenceEnergy(v) {
      // Gemeinsamer Puls bei Convergence
      ["nexus", "hermes"].forEach((id) => { if (this.cores[id]) this.cores[id].pulse = Math.max(this.cores[id].pulse, v * 1.2); });
    }
  };

  NEXUS.agents = Agents;
})();
