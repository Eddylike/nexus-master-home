/* =========================================================
   beams.js — Dynamische Lichtstrahlen + Energieimpulse
   ========================================================= */
(function () {
  "use strict";
  const NEXUS = window.NEXUS;
  const U = NEXUS.util;

  const SEG = 40;

  function quadratic(a, c, b, t, out) {
    const it = 1 - t;
    out.x = it * it * a.x + 2 * it * t * c.x + t * t * b.x;
    out.y = it * it * a.y + 2 * it * t * c.y + t * t * b.y;
    out.z = it * it * a.z + 2 * it * t * c.z + t * t * b.z;
    return out;
  }

  const Beams = {
    links: {},
    pulses: [],
    _v0: null, _v1: null, _v2: null,

    _makeLink(colorA, colorB, width) {
      const THREE = NEXUS.scene.THREE;
      const geo = new THREE.BufferGeometry();
      const pos = new Float32Array((SEG + 1) * 3);
      const col = new Float32Array((SEG + 1) * 3);
      const ca = new THREE.Color(colorA), cb = new THREE.Color(colorB);
      for (let i = 0; i <= SEG; i++) {
        const t = i / SEG;
        const c = ca.clone().lerp(cb, t);
        col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b;
      }
      geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
      const mat = new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.6, blending: THREE.AdditiveBlending, depthWrite: false });
      const line = new THREE.Line(geo, mat);
      NEXUS.scene.scene.add(line);
      return { line, geo, mat, boost: 0, base: 0.5, visible: true };
    },

    init() {
      const THREE = NEXUS.scene.THREE;
      this._v0 = new THREE.Vector3(); this._v1 = new THREE.Vector3(); this._v2 = new THREE.Vector3();

      this.links.main = this._makeLink(0x35e6ff, 0xffcf6b, 2);
      this.links.nexusEarth = this._makeLink(0x35e6ff, 0x1b7e93, 1);
      this.links.hermesEarth = this._makeLink(0xffcf6b, 0xa37f33, 1);

      // Impuls-Pool
      const poolN = NEXUS.scene.quality === "low" ? 6 : 14;
      for (let i = 0; i < poolN; i++) {
        const m = new THREE.Mesh(
          new THREE.SphereGeometry(0.55, 10, 10),
          new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 1, blending: THREE.AdditiveBlending, depthWrite: false })
        );
        m.visible = false;
        NEXUS.scene.scene.add(m);
        this.pulses.push({ mesh: m, active: false, t: 0, speed: 0.6, dir: 1, link: "main", color: 0xffffff, born: 0, life: 1.6 });
      }

      NEXUS.scene.add((dt, t, sc) => this._update(dt, t, sc));
      return this;
    },

    _positions() {
      return {
        nexus: NEXUS.agents.getPosition("nexus"),
        hermes: NEXUS.agents.getPosition("hermes"),
        system: NEXUS.agents.getPosition("system"),
        center: this._center()
      };
    },

    _center() {
      // Erdmittelpunkt (Origin)
      return this._store || (this._store = new (NEXUS.scene.THREE.Vector3)(0, 0, 0));
    },

    _writeLink(link, a, b, ctrl) {
      const pos = link.geo.attributes.position.array;
      const tmp = this._v2;
      for (let i = 0; i <= SEG; i++) {
        const tt = i / SEG;
        quadratic(a, ctrl, b, tt, tmp);
        pos[i * 3] = tmp.x; pos[i * 3 + 1] = tmp.y; pos[i * 3 + 2] = tmp.z;
      }
      link.geo.attributes.position.needsUpdate = true;
      link.geo.computeBoundingSphere();
    },

    _update(dt, t, sc) {
      const P = this._positions();
      const V = NEXUS.scene.THREE.Vector3;

      // Hauptlink NEXUS <-> HERMES (Bogen über der Erde)
      const ctrl = this._v0.set(0, 52, 0);
      this._writeLink(this.links.main, P.nexus, P.hermes, ctrl);

      // Agent -> Erde (gerade)
      this._writeLink(this.links.nexusEarth, P.nexus, P.center, this._v1.set(P.nexus.x * 0.5, P.nexus.y * 0.5 + 6, P.nexus.z * 0.5));
      this._writeLink(this.links.hermesEarth, P.hermes, P.center, this._v1.set(P.hermes.x * 0.5, P.hermes.y * 0.5 + 6, P.hermes.z * 0.5));

      // Grundintensität je Kern-Aktivität
      const nActive = NEXUS.store.get().agents.nexus.active ? 1 : 0.5;
      const hActive = NEXUS.store.get().agents.hermes.active ? 1 : 0.5;
      this.links.nexusEarth.base = 0.22 * nActive + 0.12;
      this.links.hermesEarth.base = 0.22 * hActive + 0.12;
      this.links.main.base = 0.35 + NEXUS.store.get().convergence * 0.5;

      Object.keys(this.links).forEach((k) => {
        const l = this.links[k];
        l.boost = Math.max(0, l.boost - dt * 1.4);
        const flick = 0.85 + 0.15 * Math.sin(t * 8 + k.length);
        l.mat.opacity = U.clamp((l.base + l.boost) * flick, 0, 1);
      });

      // Impulse
      const s = sc.reduced ? 0.4 : 1;
      this.pulses.forEach((p) => {
        if (!p.active) return;
        p.t += dt * p.speed * p.dir * s;
        if (p.t > 1) p.t = 1;
        if (p.t < 0) p.t = 0;
        const from = p.dir > 0 ? P.nexus : P.hermes;
        const to = p.dir > 0 ? P.hermes : P.nexus;
        const ctrl2 = this._v0.set(0, 52, 0);
        const pos = quadratic(from, ctrl2, to, U.clamp(p.t, 0, 1), this._v1);
        p.mesh.position.copy(pos);
        p.mesh.material.color.setHex(p.color);
        const fade = Math.sin(U.clamp(p.t, 0, 1) * Math.PI);
        p.mesh.material.opacity = U.clamp(fade * 1.4, 0, 1);
        p.mesh.scale.setScalar(0.7 + fade * 1.6);
        if (p.t >= 1 || p.t <= 0) { p.active = false; p.mesh.visible = false; }
      });
    },

    transmission(fromId, toId) {
      const p = this.pulses.find((x) => !x.active) || this.pulses[0];
      if (!p) return;
      p.active = true;
      p.mesh.visible = true;
      p.t = fromId === "nexus" ? 0 : 1;
      p.dir = fromId === "nexus" ? 1 : -1;
      p.speed = U.rand(0.7, 1.1);
      p.color = fromId === "hermes" ? 0xffcf6b : 0x35e6ff;
      // Alle Impulse bei Besprechung gerichtet zum jeweils anderen Kern
      p.link = "main";
      this.links.main.boost = Math.min(1, this.links.main.boost + 0.5);
      NEXUS.agents.pulse(fromId, 0.5);
    },

    beamBurst(which) {
      if (this.links[which]) this.links[which].boost = 1;
    }
  };

  NEXUS.beams = Beams;
})();
