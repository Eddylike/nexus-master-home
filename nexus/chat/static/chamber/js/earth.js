/* =========================================================
   earth.js — Holografische 3D-Erde + orbitale Energieringe
   ========================================================= */
(function () {
  "use strict";
  const NEXUS = window.NEXUS;
  const U = NEXUS.util;

  const Earth = {
    group: null,
    sphere: null,
    rings: [],
    radius: 26,

    // Prozedurale Textur (keine externen Assets / keine Cloud)
    _makeTexture(size) {
      const w = size, h = size / 2;
      const cv = document.createElement("canvas");
      cv.width = w; cv.height = h;
      const ctx = cv.getContext("2d");

      const g = ctx.createLinearGradient(0, 0, 0, h);
      g.addColorStop(0, "#04121f");
      g.addColorStop(0.5, "#06283f");
      g.addColorStop(1, "#04121f");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, w, h);

      // Kontinente als weiche Blobs (holografisch)
      const blobs = 16;
      for (let i = 0; i < blobs; i++) {
        const cx = U.rand(0, w);
        const cy = U.rand(h * 0.12, h * 0.88);
        const r = U.rand(w * 0.03, w * 0.11);
        const grd = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
        const hot = Math.random() < 0.3;
        grd.addColorStop(0, hot ? "rgba(90,240,166,0.55)" : "rgba(53,230,255,0.5)");
        grd.addColorStop(0.6, "rgba(40,150,190,0.22)");
        grd.addColorStop(1, "rgba(0,0,0,0)");
        ctx.fillStyle = grd;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.fill();
      }

      // Gradnetz
      ctx.strokeStyle = "rgba(53,230,255,0.14)";
      ctx.lineWidth = 1;
      for (let lon = 0; lon <= w; lon += w / 24) {
        ctx.beginPath(); ctx.moveTo(lon, 0); ctx.lineTo(lon, h); ctx.stroke();
      }
      for (let lat = 0; lat <= h; lat += h / 12) {
        ctx.beginPath(); ctx.moveTo(0, lat); ctx.lineTo(w, lat); ctx.stroke();
      }

      // Stadtlichter (Gold)
      ctx.fillStyle = "rgba(255,207,107,0.9)";
      for (let i = 0; i < 260; i++) {
        const x = Math.random() * w, y = U.rand(h * 0.1, h * 0.9);
        ctx.globalAlpha = U.rand(0.25, 0.9);
        ctx.fillRect(x, y, 1.4, 1.4);
      }
      ctx.globalAlpha = 1;

      const tex = new THREE.CanvasTexture(cv);
      tex.wrapS = THREE.RepeatWrapping;
      tex.anisotropy = NEXUS.scene.renderer.capabilities.getMaxAnisotropy ? Math.min(4, NEXUS.scene.renderer.capabilities.getMaxAnisotropy()) : 1;
      return tex;
    },

    _makeAtmosphere(radius) {
      const geo = new THREE.SphereGeometry(radius * 1.09, 64, 64);
      const mat = new THREE.ShaderMaterial({
        transparent: true,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        side: THREE.BackSide,
        uniforms: { uColor: { value: new THREE.Color(0x35e6ff) }, uPower: { value: 3.0 }, uInt: { value: 0.9 } },
        vertexShader: `
          varying vec3 vN;
          varying vec3 vView;
          void main() {
            vN = normalize(normalMatrix * normal);
            vec4 mv = modelViewMatrix * vec4(position, 1.0);
            vView = normalize(-mv.xyz);
            gl_Position = projectionMatrix * mv;
          }`,
        fragmentShader: `
          varying vec3 vN;
          varying vec3 vView;
          uniform vec3 uColor;
          uniform float uPower;
          uniform float uInt;
          void main() {
            float f = pow(1.0 - abs(dot(vN, vView)), uPower);
            gl_FragColor = vec4(uColor, f * uInt);
          }`
      });
      return new THREE.Mesh(geo, mat);
    },

    init() {
      const THREE = NEXUS.scene.THREE;
      const S = NEXUS.scene;
      const R = this.radius;

      const group = new THREE.Group();
      group.name = "earth";
      this.group = group;
      S.scene.add(group);

      // Kern-Erde
      const tex = this._makeTexture(1024);
      this._tex = tex;
      const sphere = new THREE.Mesh(
        new THREE.SphereGeometry(R, S.params.seg, S.params.seg),
        new THREE.MeshStandardMaterial({
          map: tex,
          emissive: new THREE.Color(0x0a3550),
          emissiveMap: tex,
          emissiveIntensity: 0.55,
          roughness: 0.85,
          metalness: 0.1
        })
      );
      sphere.rotation.y = Math.PI * 1.1;
      group.add(sphere);
      this.sphere = sphere;

      // Holografisches Drahtgitter
      const wire = new THREE.Mesh(
        new THREE.SphereGeometry(R * 1.002, 40, 28),
        new THREE.MeshBasicMaterial({ color: 0x35e6ff, wireframe: true, transparent: true, opacity: 0.09, blending: THREE.AdditiveBlending, depthWrite: false })
      );
      group.add(wire);

      // Atmosphäre
      group.add(this._makeAtmosphere(R));

      // Licht
      const amb = new THREE.AmbientLight(0x224466, 0.9);
      S.scene.add(amb);
      const key = new THREE.PointLight(0x66ddff, 1.6, 600, 2);
      key.position.set(120, 90, 90);
      S.scene.add(key);
      const warm = new THREE.PointLight(0xffcf6b, 0.7, 500, 2);
      warm.position.set(-140, -40, -80);
      S.scene.add(warm);

      // Orbitale Energieringe
      const colors = [0x35e6ff, 0xb06bff, 0xffcf6b];
      const nRings = S.params.rings;
      for (let i = 0; i < nRings; i++) {
        const rr = R * (1.35 + i * 0.28);
        const tube = 0.10 + i * 0.05;
        const ring = new THREE.Mesh(
          new THREE.TorusGeometry(rr, tube, 12, S.params.seg * 1.4),
          new THREE.MeshBasicMaterial({ color: colors[i % colors.length], transparent: true, opacity: 0.55 - i * 0.08, blending: THREE.AdditiveBlending, depthWrite: false })
        );
        ring.rotation.x = Math.PI / 2 + U.rand(-0.5, 0.5);
        ring.rotation.z = U.rand(0, Math.PI);
        group.add(ring);

        // Ring-Glitzer
        const pn = 120 - i * 20;
        const pos = new Float32Array(pn * 3);
        for (let k = 0; k < pn; k++) {
          const a = (k / pn) * Math.PI * 2;
          pos[k * 3] = Math.cos(a) * rr;
          pos[k * 3 + 1] = U.rand(-0.4, 0.4);
          pos[k * 3 + 2] = Math.sin(a) * rr;
        }
        const pgeo = new THREE.BufferGeometry();
        pgeo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
        const pts = new THREE.Points(pgeo, new THREE.PointsMaterial({ color: colors[i % colors.length], size: 0.7, transparent: true, opacity: 0.9, blending: THREE.AdditiveBlending, depthWrite: false }));
        const rgroup = new THREE.Group();
        rgroup.add(ring);
        rgroup.add(pts);
        group.add(rgroup);
        this.rings.push({ rgroup, ring, pts, speed: (0.08 + i * 0.05) * (i % 2 ? -1 : 1), axis: ring.rotation.z });
      }

      // Scan-Ring (äquatorial, schneller Impuls)
      const scan = new THREE.Mesh(
        new THREE.TorusGeometry(R * 1.18, 0.05, 8, 120),
        new THREE.MeshBasicMaterial({ color: 0x35e6ff, transparent: true, opacity: 0.8, blending: THREE.AdditiveBlending, depthWrite: false })
      );
      scan.rotation.x = Math.PI / 2;
      group.add(scan);
      this.scan = scan;

      S.add((dt, t, sc) => {
        const s = sc.reduced ? 0.25 : 1;
        sphere.rotation.y += dt * 0.045 * s;
        wire.rotation.y -= dt * 0.02 * s;
        this.rings.forEach((r) => { r.rgroup.rotation.y += dt * r.speed * s; });
        // Scan-Höhe oszilliert
        const k = (Math.sin(t * 0.5) + 1) / 2;
        scan.position.y = U.lerp(-R, R, k);
        scan.scale.setScalar(U.lerp(0.86, 1.0, Math.abs(Math.sin(t * 0.5))));
        scan.material.opacity = 0.25 + 0.55 * Math.sin(t * 2.2) ** 2;
      });

      return this;
    }
  };

  NEXUS.earth = Earth;
})();
