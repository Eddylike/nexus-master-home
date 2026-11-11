/* =========================================================
   starfield.js — Sternenfeld mit Tiefenwirkung + Parallaxe
   ========================================================= */
(function () {
  "use strict";
  const NEXUS = window.NEXUS;
  const U = NEXUS.util;

  const Stars = {
    groups: [],
    inited: false,

    init() {
      const THREE = NEXUS.scene.THREE;
      const S = NEXUS.scene;
      const count = S.params.stars;
      const dpr = S.renderer.getPixelRatio();

      const uniforms = {
        uTime: { value: 0 },
        uPixelRatio: { value: dpr }
      };

      const vert = `
        attribute float aSize;
        attribute float aPhase;
        attribute vec3 aColor;
        uniform float uTime;
        uniform float uPixelRatio;
        varying vec3 vColor;
        varying float vPhase;
        void main() {
          vColor = aColor;
          vPhase = aPhase;
          vec4 mv = modelViewMatrix * vec4(position, 1.0);
          float tw = 0.55 + 0.45 * sin(uTime * 1.5 + aPhase * 6.2831);
          gl_PointSize = aSize * tw * uPixelRatio * (300.0 / max(-mv.z, 1.0));
          gl_Position = projectionMatrix * mv;
        }`;

      const frag = `
        varying vec3 vColor;
        varying float vPhase;
        uniform float uTime;
        void main() {
          vec2 c = gl_PointCoord - 0.5;
          float d = length(c);
          float a = smoothstep(0.5, 0.0, d);
          float tw = 0.6 + 0.4 * sin(uTime * 1.8 + vPhase * 6.2831);
          gl_FragColor = vec4(vColor, a * tw);
        }`;

      const mat = new THREE.ShaderMaterial({
        uniforms,
        vertexShader: vert,
        fragmentShader: frag,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending
      });
      this._mat = mat;
      this._uniforms = uniforms;

      const layers = [
        { rMin: 260, rMax: 700, size: [0.7, 1.4], color: [0.65, 0.85, 1.0], op: 1.0, parcel: 0.35 },
        { rMin: 140, rMax: 300, size: [1.0, 2.2], color: [0.75, 0.92, 1.0], op: 1.0, parcel: 0.6 },
        { rMin: 70,  rMax: 150, size: [1.6, 3.4], color: [1.0, 0.95, 0.85], op: 1.0, parcel: 0.9 }
      ];

      const palette = [
        [0.55, 0.95, 1.0],  // cyan
        [1.0, 0.86, 0.5],   // gold
        [0.75, 0.6, 1.0],   // violet
        [0.92, 0.96, 1.0]   // white
      ];

      let remaining = count;
      layers.forEach((L, li) => {
        const isLast = li === layers.length - 1;
        const n = isLast ? remaining : Math.floor(count * (L.parcel));
        remaining -= n;
        if (n <= 0) return;

        const pos = new Float32Array(n * 3);
        const size = new Float32Array(n);
        const phase = new Float32Array(n);
        const col = new Float32Array(n * 3);

        for (let i = 0; i < n; i++) {
          const r = U.rand(L.rMin, L.rMax);
          const th = U.rand(0, Math.PI * 2);
          const ph = Math.acos(U.rand(-1, 1));
          const x = r * Math.sin(ph) * Math.cos(th);
          const y = r * Math.cos(ph) * 0.6;
          const z = r * Math.sin(ph) * Math.sin(th);
          pos[i * 3] = x; pos[i * 3 + 1] = y; pos[i * 3 + 2] = z;

          size[i] = U.rand(L.size[0], L.size[1]);
          phase[i] = Math.random();

          const c = Math.random() < 0.86 ? L.color : U.pick(palette);
          col[i * 3] = c[0]; col[i * 3 + 1] = c[1]; col[i * 3 + 2] = c[2];
        }

        const geo = new THREE.BufferGeometry();
        geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
        geo.setAttribute("aSize", new THREE.BufferAttribute(size, 1));
        geo.setAttribute("aPhase", new THREE.BufferAttribute(phase, 1));
        geo.setAttribute("aColor", new THREE.BufferAttribute(col, 3));

        const points = new THREE.Points(geo, mat);
        points.name = "stars-" + li;
        S.scene.add(points);
        this.groups.push({ points, base: 0, layer: L, spin: U.rand(0.004, 0.012) * (li % 2 === 0 ? 1 : -1) });
      });

      S.add((dt, t, sc) => {
        this._uniforms.uTime.value = t;
        for (const g of this.groups) {
          g.points.rotation.y += g.spin * dt * (sc.reduced ? 0.15 : 1);
          // Parallaxe je Tiefe
          g.points.position.x = -sc.pointer.x * (8 + g.layer.rMin * 0.02);
          g.points.position.y = sc.pointer.y * (5 + g.layer.rMin * 0.015);
        }
      });

      this.inited = true;
      return this;
    }
  };

  NEXUS.stars = Stars;
})();
