// Fullscreen physics-flavored visualization layer.
// Receives { type: "spacetime", action, scene?, value? } events from the
// global window.__spacetime bridge (set up in jarvis.js).
//
// Five scenes:
//   blackhole  — Schwarzschild horizon + warped grid + accretion disk
//   lightcone  — Minkowski diagram with worldlines and causal cones
//   gravwave   — Binary inspiral with rippling strain plane
//   nbody      — Gravitational N-body simulation
//   curvature  — Rubber-sheet diagram with planets orbiting in the well

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const SCENE_LABELS = {
  blackhole: ["BLACK HOLE", "SCHWARZSCHILD GEOMETRY"],
  lightcone: ["LIGHT CONE", "MINKOWSKI SPACETIME"],
  gravwave:  ["GRAVITATIONAL WAVE", "BINARY INSPIRAL"],
  nbody:     ["N-BODY", "GRAVITATIONAL DYNAMICS"],
  curvature: ["CURVATURE", "MASS-ENERGY WARPING SPACETIME"],
};

const overlay = document.getElementById("spacetime-overlay");
const canvas = document.getElementById("spacetime-canvas");
const sceneNameEl = document.getElementById("spacetime-scene-name");
const sceneSubEl = document.getElementById("spacetime-scene-sub");
const speedEl = document.getElementById("spacetime-speed");
const statusEl = document.getElementById("spacetime-status");

// ---------- Three.js core ----------

const renderer = new THREE.WebGLRenderer({
  canvas,
  antialias: true,
  alpha: false,
  powerPreference: "high-performance",
});
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight, false);

const camera = new THREE.PerspectiveCamera(
  55, window.innerWidth / window.innerHeight, 0.05, 5000,
);
camera.position.set(0, 6, 22);

const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.minDistance = 1;
controls.maxDistance = 400;

// ---------- Persistent starfield ----------

function makeStarfield(count = 3000, radius = 800) {
  const positions = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const r = radius * Math.cbrt(Math.random());
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    positions[3*i]   = r * Math.sin(phi) * Math.cos(theta);
    positions[3*i+1] = r * Math.sin(phi) * Math.sin(theta);
    positions[3*i+2] = r * Math.cos(phi);
  }
  const geom = new THREE.BufferGeometry();
  geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  const mat = new THREE.PointsMaterial({
    color: 0xffffff, size: 0.7, sizeAttenuation: true,
    transparent: true, opacity: 0.7,
  });
  return new THREE.Points(geom, mat);
}

// Shared base scene with starfield; each "scene" adds/removes its own group.
const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0x000000, 0.002);
scene.add(makeStarfield());

const sceneGroup = new THREE.Group();
scene.add(sceneGroup);

// Lights — physics scenes use mostly emissive + lines, but add a soft fill.
scene.add(new THREE.AmbientLight(0x223344, 0.4));
const keyLight = new THREE.PointLight(0x88ccff, 1.0, 0, 0);
keyLight.position.set(20, 30, 20);
scene.add(keyLight);

// ---------- State ----------

let currentScene = null;            // name string
let sceneUpdate = () => {};          // per-scene tick(dt)
let simSpeed = 1.0;
let paused = false;
let lastFrame = performance.now();
let activeSceneObjects = [];         // tracked for disposal

function disposeScene() {
  for (const obj of activeSceneObjects) {
    sceneGroup.remove(obj);
    obj.traverse?.((child) => {
      if (child.isMesh || child.isLine || child.isPoints) {
        child.geometry?.dispose();
        const m = child.material;
        if (Array.isArray(m)) m.forEach((mm) => mm.dispose());
        else m?.dispose();
      }
    });
  }
  activeSceneObjects = [];
  sceneUpdate = () => {};
}

function addObj(obj) {
  sceneGroup.add(obj);
  activeSceneObjects.push(obj);
  return obj;
}

// =====================================================================
// Scene 1 — BLACK HOLE
// =====================================================================

function loadBlackhole() {
  camera.position.set(0, 5, 22);
  controls.target.set(0, 0, 0);

  // Event horizon: solid black sphere with a thin orange rim glow.
  const horizon = new THREE.Mesh(
    new THREE.SphereGeometry(2.0, 64, 64),
    new THREE.MeshBasicMaterial({ color: 0x000000 }),
  );
  addObj(horizon);

  // Photon ring — thin annulus just outside horizon, glowing.
  const ringGeo = new THREE.RingGeometry(2.6, 2.8, 128);
  const ringMat = new THREE.MeshBasicMaterial({
    color: 0xffaa55, side: THREE.DoubleSide,
    transparent: true, opacity: 0.85,
  });
  const photonRing = new THREE.Mesh(ringGeo, ringMat);
  photonRing.rotation.x = -Math.PI / 2;
  addObj(photonRing);

  // Accretion disk — series of nested rings with color gradient.
  const disk = new THREE.Group();
  for (let i = 0; i < 60; i++) {
    const r = 3.0 + i * 0.18;
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(r, r + 0.14, 128),
      new THREE.MeshBasicMaterial({
        color: new THREE.Color().setHSL(0.08 - i * 0.0015, 0.9, 0.45 - i * 0.005),
        side: THREE.DoubleSide,
        transparent: true,
        opacity: Math.max(0.05, 0.7 - i * 0.01),
      }),
    );
    ring.rotation.x = -Math.PI / 2;
    ring.userData.r = r;
    ring.userData.phase = Math.random() * Math.PI * 2;
    disk.add(ring);
  }
  addObj(disk);

  // Warped spatial grid: a flat plane wireframe with a "well" near horizon.
  const N = 100;
  const halfSize = 30;
  const planeGeo = new THREE.PlaneGeometry(halfSize * 2, halfSize * 2, N, N);
  const pos = planeGeo.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i);
    const y = pos.getY(i);
    const r = Math.sqrt(x * x + y * y);
    // Embedding diagram for Schwarzschild: z = 2 sqrt(r_s (r - r_s)) outside horizon
    const rs = 2.0;
    let z = 0;
    if (r > rs) z = -2.0 * Math.sqrt(rs * (r - rs));
    else z = -2.0 * Math.sqrt(rs * 0.001);
    pos.setZ(i, z);
  }
  planeGeo.computeVertexNormals();
  const planeMat = new THREE.MeshBasicMaterial({
    color: 0x0088cc, wireframe: true, transparent: true, opacity: 0.35,
  });
  const grid = new THREE.Mesh(planeGeo, planeMat);
  grid.rotation.x = -Math.PI / 2;
  grid.position.y = -3;
  addObj(grid);

  let t = 0;
  sceneUpdate = (dt) => {
    t += dt;
    // Keplerian-like disk rotation (faster near horizon).
    disk.children.forEach((ring) => {
      const r = ring.userData.r;
      const omega = 1.2 / Math.pow(r, 1.5);
      ring.userData.phase += omega * dt * simSpeed;
      ring.rotation.z = ring.userData.phase;
    });
    photonRing.rotation.z = t * 0.4 * simSpeed;
  };
}

// =====================================================================
// Scene 2 — LIGHT CONE (Minkowski diagram)
// =====================================================================

function loadLightcone() {
  camera.position.set(8, 8, 20);
  controls.target.set(0, 4, 0);

  // Light cones (open up = future, open down = past) from 3 events.
  const events = [
    { x: 0, y: 0, z: 0, color: 0xffffff },
    { x: -6, y: 0, z: 4, color: 0x88ddff },
    { x: 5, y: 0, z: -3, color: 0xff8866 },
  ];

  for (const ev of events) {
    // Future cone — open upward (y is "time")
    const future = new THREE.Mesh(
      new THREE.ConeGeometry(8, 8, 48, 1, true),
      new THREE.MeshBasicMaterial({
        color: ev.color, side: THREE.DoubleSide,
        transparent: true, opacity: 0.12, wireframe: false,
      }),
    );
    future.position.set(ev.x, 4, ev.z);
    addObj(future);

    // Wire outline for the cone
    const wire = new THREE.Mesh(
      new THREE.ConeGeometry(8, 8, 48, 1, true),
      new THREE.MeshBasicMaterial({ color: ev.color, wireframe: true, opacity: 0.35, transparent: true }),
    );
    wire.position.set(ev.x, 4, ev.z);
    addObj(wire);

    // Past cone — pointing downward
    const past = new THREE.Mesh(
      new THREE.ConeGeometry(8, 8, 48, 1, true),
      new THREE.MeshBasicMaterial({
        color: ev.color, side: THREE.DoubleSide,
        transparent: true, opacity: 0.08,
      }),
    );
    past.position.set(ev.x, -4, ev.z);
    past.rotation.z = Math.PI;
    addObj(past);

    // Event marker
    const marker = new THREE.Mesh(
      new THREE.SphereGeometry(0.18, 16, 16),
      new THREE.MeshBasicMaterial({ color: ev.color }),
    );
    marker.position.set(ev.x, 0, ev.z);
    addObj(marker);
  }

  // Worldlines — animated trails moving up the time axis
  const worldlines = [];
  const colors = [0xffaa00, 0xaaffaa, 0xffaaff];
  for (let i = 0; i < 3; i++) {
    const segments = 200;
    const geom = new THREE.BufferGeometry();
    const positions = new Float32Array(segments * 3);
    geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    const line = new THREE.Line(geom, new THREE.LineBasicMaterial({
      color: colors[i], transparent: true, opacity: 0.85,
    }));
    addObj(line);
    worldlines.push({
      line, positions, segments,
      t0: i * 1.2,
      x0: (i - 1) * 3,
      z0: (i - 1) * 2,
      vx: 0.4 * (i - 1),
      vz: 0.3 * Math.sin(i),
    });
  }

  // Time-axis hash marks
  const axisGeo = new THREE.BufferGeometry();
  const axisPts = [];
  for (let t = -4; t <= 12; t += 1) {
    axisPts.push(-0.5, t, 0, 0.5, t, 0);
  }
  axisGeo.setAttribute("position", new THREE.Float32BufferAttribute(axisPts, 3));
  const axis = new THREE.LineSegments(axisGeo, new THREE.LineBasicMaterial({
    color: 0x444466, transparent: true, opacity: 0.6,
  }));
  addObj(axis);

  let t = 0;
  sceneUpdate = (dt) => {
    t += dt * simSpeed * 0.6;
    for (const w of worldlines) {
      for (let i = 0; i < w.segments; i++) {
        const tau = w.t0 + (i / w.segments) * (t - w.t0);
        if (tau < w.t0) {
          w.positions[3*i] = w.x0;
          w.positions[3*i+1] = w.t0 - 4;
          w.positions[3*i+2] = w.z0;
        } else {
          // Worldline must stay timelike: |dx/dt| < 1 (speed of light = 1)
          w.positions[3*i] = w.x0 + w.vx * (tau - w.t0) + 0.3 * Math.sin(tau);
          w.positions[3*i+1] = tau - 4;
          w.positions[3*i+2] = w.z0 + w.vz * (tau - w.t0);
        }
      }
      w.line.geometry.attributes.position.needsUpdate = true;
    }
  };
}

// =====================================================================
// Scene 3 — GRAVITATIONAL WAVE (binary inspiral)
// =====================================================================

function loadGravwave() {
  camera.position.set(0, 12, 24);
  controls.target.set(0, 0, 0);

  // Two orbiting masses
  const m1 = new THREE.Mesh(
    new THREE.SphereGeometry(0.6, 32, 32),
    new THREE.MeshBasicMaterial({ color: 0x00ddff }),
  );
  const m2 = new THREE.Mesh(
    new THREE.SphereGeometry(0.6, 32, 32),
    new THREE.MeshBasicMaterial({ color: 0xff8855 }),
  );
  addObj(m1);
  addObj(m2);

  // Ripple plane — height-field that shows strain
  const N = 160;
  const halfSize = 22;
  const planeGeo = new THREE.PlaneGeometry(halfSize * 2, halfSize * 2, N, N);
  const planeMat = new THREE.MeshBasicMaterial({
    color: 0x4488ff, wireframe: true, transparent: true, opacity: 0.45,
  });
  const plane = new THREE.Mesh(planeGeo, planeMat);
  plane.rotation.x = -Math.PI / 2;
  addObj(plane);

  let t = 0;
  let r = 5.0;            // orbital separation
  const inspiralRate = 0.05;
  const omegaBase = 0.7;
  sceneUpdate = (dt) => {
    t += dt * simSpeed;
    // Inspiral
    r = Math.max(0.6, 5.0 - inspiralRate * t);
    const omega = omegaBase * Math.pow(5.0 / r, 1.5); // Kepler-ish speedup
    const a = omega * t;
    m1.position.set(r * Math.cos(a), 0.2, r * Math.sin(a));
    m2.position.set(-r * Math.cos(a), 0.2, -r * Math.sin(a));

    // Update strain plane: outgoing wave with two polarizations.
    const pos = planeGeo.attributes.position;
    for (let i = 0; i < pos.count; i++) {
      const x = pos.getX(i);
      const y = pos.getY(i);
      const rr = Math.sqrt(x * x + y * y);
      const phase = 2 * a - rr * 0.6;
      // Quadrupolar h+ pattern: cos(2*phi) cos(phase) / r
      const phi = Math.atan2(y, x);
      const env = 1.0 / (1 + rr * 0.25);
      const hPlus = Math.cos(2 * phi) * Math.cos(phase) * env;
      pos.setZ(i, hPlus * 1.5);
    }
    pos.needsUpdate = true;
  };
}

// =====================================================================
// Scene 4 — N-BODY (gravitational simulation)
// =====================================================================

function loadNbody() {
  camera.position.set(0, 30, 60);
  controls.target.set(0, 0, 0);

  const N = 220;
  const positions = new Float32Array(N * 3);
  const velocities = new Float32Array(N * 3);
  const colors = new Float32Array(N * 3);
  const masses = new Float32Array(N);

  for (let i = 0; i < N; i++) {
    // Sample within a disk
    const r = 8 + Math.random() * 15;
    const theta = Math.random() * Math.PI * 2;
    positions[3*i]   = r * Math.cos(theta);
    positions[3*i+1] = (Math.random() - 0.5) * 2;
    positions[3*i+2] = r * Math.sin(theta);
    // Roughly circular orbit
    const v = Math.sqrt(8.0 / r);
    velocities[3*i]   = -v * Math.sin(theta) + (Math.random() - 0.5) * 0.2;
    velocities[3*i+1] = (Math.random() - 0.5) * 0.05;
    velocities[3*i+2] =  v * Math.cos(theta) + (Math.random() - 0.5) * 0.2;
    masses[i] = 0.5 + Math.random();
    const hue = 0.55 + (Math.random() - 0.5) * 0.2;
    const c = new THREE.Color().setHSL(hue, 0.7, 0.65);
    colors[3*i]   = c.r;
    colors[3*i+1] = c.g;
    colors[3*i+2] = c.b;
  }

  const geom = new THREE.BufferGeometry();
  geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geom.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  const mat = new THREE.PointsMaterial({
    size: 0.45,
    vertexColors: true,
    transparent: true,
    opacity: 0.95,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });
  const pts = new THREE.Points(geom, mat);
  addObj(pts);

  // Central attractor mass at origin
  const center = new THREE.Mesh(
    new THREE.SphereGeometry(0.6, 32, 32),
    new THREE.MeshBasicMaterial({ color: 0xffeebb }),
  );
  addObj(center);

  const G = 6.0;        // Made-up units
  const M_central = 80;
  const softening = 0.5;

  sceneUpdate = (dt) => {
    const h = Math.min(0.05, dt) * simSpeed;
    // Compute accel from central mass only (O(N), much cheaper than full N²)
    for (let i = 0; i < N; i++) {
      const x = positions[3*i];
      const y = positions[3*i+1];
      const z = positions[3*i+2];
      const r2 = x*x + y*y + z*z + softening * softening;
      const invR3 = 1.0 / (r2 * Math.sqrt(r2));
      const ax = -G * M_central * x * invR3;
      const ay = -G * M_central * y * invR3;
      const az = -G * M_central * z * invR3;
      velocities[3*i]   += ax * h;
      velocities[3*i+1] += ay * h;
      velocities[3*i+2] += az * h;
      positions[3*i]   += velocities[3*i]   * h;
      positions[3*i+1] += velocities[3*i+1] * h;
      positions[3*i+2] += velocities[3*i+2] * h;
    }
    geom.attributes.position.needsUpdate = true;
  };
}

// =====================================================================
// Scene 5 — CURVATURE (rubber-sheet diagram)
// =====================================================================

function loadCurvature() {
  camera.position.set(0, 16, 26);
  controls.target.set(0, -2, 0);

  // Heavy central mass on the sheet
  const sun = new THREE.Mesh(
    new THREE.SphereGeometry(1.0, 32, 32),
    new THREE.MeshBasicMaterial({ color: 0xffeecc }),
  );
  addObj(sun);

  // Warped sheet
  const N = 120;
  const halfSize = 18;
  const sheetGeo = new THREE.PlaneGeometry(halfSize * 2, halfSize * 2, N, N);
  const sheetPos = sheetGeo.attributes.position;
  const masses = [
    { x: 0, z: 0, M: 8.0 },
    { x: 6, z: -4, M: 1.6 },
    { x: -7, z: 5, M: 1.2 },
  ];
  function depthAt(x, z) {
    let d = 0;
    for (const m of masses) {
      const dx = x - m.x;
      const dz = z - m.z;
      d -= m.M / (Math.sqrt(dx * dx + dz * dz) + 0.6);
    }
    return d;
  }
  for (let i = 0; i < sheetPos.count; i++) {
    const x = sheetPos.getX(i);
    const y = sheetPos.getY(i);
    sheetPos.setZ(i, depthAt(x, y));
  }
  const sheet = new THREE.Mesh(
    sheetGeo,
    new THREE.MeshBasicMaterial({
      color: 0x55aaff,
      wireframe: true,
      transparent: true,
      opacity: 0.55,
    }),
  );
  sheet.rotation.x = -Math.PI / 2;
  addObj(sheet);

  // Position sun and small masses at their wells
  sun.position.set(0, depthAt(0, 0), 0);
  for (let i = 1; i < masses.length; i++) {
    const m = masses[i];
    const mass = new THREE.Mesh(
      new THREE.SphereGeometry(0.35, 24, 24),
      new THREE.MeshBasicMaterial({ color: 0xff9966 }),
    );
    mass.position.set(m.x, depthAt(m.x, m.z), m.z);
    addObj(mass);
  }

  // Orbiting planets — circular orbits on the warped sheet
  const planets = [];
  for (let i = 0; i < 4; i++) {
    const planet = new THREE.Mesh(
      new THREE.SphereGeometry(0.22, 24, 24),
      new THREE.MeshBasicMaterial({ color: [0x88ffaa, 0xffaaff, 0xaaccff, 0xffff88][i] }),
    );
    addObj(planet);
    planets.push({
      mesh: planet,
      r: 3 + i * 2.5,
      omega: 0.7 / Math.pow(3 + i * 2.5, 0.7),
      phase: Math.random() * Math.PI * 2,
    });
  }

  sceneUpdate = (dt) => {
    for (const p of planets) {
      p.phase += p.omega * dt * simSpeed;
      const x = p.r * Math.cos(p.phase);
      const z = p.r * Math.sin(p.phase);
      p.mesh.position.set(x, depthAt(x, z) + 0.2, z);
    }
  };
}

// =====================================================================
// Scene dispatch
// =====================================================================

const SCENE_LOADERS = {
  blackhole: loadBlackhole,
  lightcone: loadLightcone,
  gravwave: loadGravwave,
  nbody: loadNbody,
  curvature: loadCurvature,
};

function setScene(name) {
  if (!(name in SCENE_LOADERS)) return;
  disposeScene();
  currentScene = name;
  SCENE_LOADERS[name]();
  const [label, sub] = SCENE_LABELS[name];
  sceneNameEl.textContent = label;
  sceneSubEl.textContent = sub;
}

// =====================================================================
// Public API exposed to jarvis.js
// =====================================================================

function show() {
  overlay.classList.remove("hidden");
}
function hide() {
  overlay.classList.add("hidden");
}

function setSpeed(v) {
  simSpeed = Math.max(0, v);
  speedEl.textContent = `${simSpeed.toFixed(1)}×`;
}
function setPaused(p) {
  paused = p;
  statusEl.textContent = p ? "PAUSED" : "RUNNING";
}

function rotateCamera(degrees) {
  controls.update();
  const rad = (degrees * Math.PI) / 180;
  const offset = camera.position.clone().sub(controls.target);
  const axis = camera.up.clone().normalize();
  offset.applyAxisAngle(axis, rad);
  camera.position.copy(controls.target).add(offset);
  controls.update();
}

function zoomCamera(factor) {
  if (factor <= 0) return;
  const offset = camera.position.clone().sub(controls.target);
  offset.multiplyScalar(1 / factor);
  const dist = offset.length();
  if (dist < controls.minDistance) offset.setLength(controls.minDistance);
  if (dist > controls.maxDistance) offset.setLength(controls.maxDistance);
  camera.position.copy(controls.target).add(offset);
  controls.update();
}

function resetView() {
  if (currentScene) setScene(currentScene);
  setSpeed(1.0);
  setPaused(false);
}

window.__spacetime = {
  handle(msg) {
    switch (msg.action) {
      case "open":
        setScene(msg.scene || "blackhole");
        setSpeed(1.0);
        setPaused(false);
        show();
        break;
      case "close":
        hide();
        // Drop GPU resources when not visible.
        setTimeout(() => {
          if (overlay.classList.contains("hidden")) {
            disposeScene();
            currentScene = null;
          }
        }, 700);
        break;
      case "set_scene":
        if (msg.scene) setScene(msg.scene);
        break;
      case "speed":
        if (typeof msg.value === "number") setSpeed(msg.value);
        break;
      case "pause":
        setPaused(true);
        break;
      case "resume":
        setPaused(false);
        break;
      case "rotate":
        if (typeof msg.value === "number") rotateCamera(msg.value);
        break;
      case "zoom":
        if (typeof msg.value === "number") zoomCamera(msg.value);
        break;
      case "reset":
        resetView();
        break;
    }
  },
};

// ESC closes the overlay
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !overlay.classList.contains("hidden")) {
    hide();
    setTimeout(() => {
      if (overlay.classList.contains("hidden")) {
        disposeScene();
        currentScene = null;
      }
    }, 700);
  }
});

// Resize handling
window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight, false);
});

// ---------- Render loop ----------

function animate(now) {
  const dt = Math.min(0.1, (now - lastFrame) / 1000);
  lastFrame = now;
  if (!paused && currentScene) sceneUpdate(dt);
  controls.update();
  renderer.render(scene, camera);
  requestAnimationFrame(animate);
}
requestAnimationFrame(animate);
