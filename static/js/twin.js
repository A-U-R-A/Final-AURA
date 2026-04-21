/**
 * twin.js — Three.js ISS Digital Twin
 *
 * Exposes two globals consumed by app.js:
 *   window.twinInit(container, onModuleClick)
 *   window.twinUpdate(locationStates)
 */
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

// ── Colors (match AURA CSS vars) ─────────────────────────────────────────────
const CLR = {
  nominal:    0x5fe61f,   // #5fe61f accent
  anomaly:    0xff3d40,
  module:     0xc8c4b8,   // warm grey modules
  moduleDark: 0xa09c90,
  truss:      0x686460,
  solar:      0x04080f,
  solarGlow:  0x010508,
  mast:       0x888078,
  star:       0xffffff,
  sun:        0xfff5e8,
  ambient:    0x0e0e0e,
};

// ── Module 3D positions (X=starboard, Y=zenith, Z=wake) ──────────────────────
// These map to constants.py LOCATIONS
const MODULE_POS = {
  'Node 2':        new THREE.Vector3( 0,    0,   0  ),
  'US Lab':        new THREE.Vector3( 0,    0,  12  ),
  'Columbus':      new THREE.Vector3(10,    0,  -2  ),
  'JLP & JPM':     new THREE.Vector3(-12,   0,  -5  ),
  'Node 1':        new THREE.Vector3( 0,    0,  25  ),
  'Cupola':        new THREE.Vector3(-3,   -5,  27  ),
  'Joint Airlock': new THREE.Vector3(10,    0,  25  ),
};

// ── Scene state ───────────────────────────────────────────────────────────────
// Module-level singletons — only one scene exists per page load.
let renderer, scene, camera, controls;
const indicators  = {};   // location → { mesh: Mesh, light: PointLight, anomalous: bool }
const hitMeshes   = [];   // { mesh: Mesh, location: string } — larger invisible spheres for click hit-testing
let   animClock   = 0;    // monotonically increasing value driven by the rAF loop (≈ seconds)
let   pointerDown = null; // tracks pointer start position to distinguish click vs. drag
let   pointerMoved = false;

// ── Helpers ───────────────────────────────────────────────────────────────────
function stdMat(color, metalness = 0.65, roughness = 0.35) {
  return new THREE.MeshStandardMaterial({ color, metalness, roughness });
}

/** Cylinder with its long axis along Z. */
function cylZ(radiusTop, radiusBottom, height, color) {
  const geo = new THREE.CylinderGeometry(radiusTop, radiusBottom, height, 20);
  geo.rotateX(Math.PI / 2);
  return new THREE.Mesh(geo, stdMat(color));
}

/** Cylinder with its long axis along X. */
function cylX(radius, height, color) {
  const geo = new THREE.CylinderGeometry(radius, radius, height, 20);
  geo.rotateZ(Math.PI / 2);
  return new THREE.Mesh(geo, stdMat(color));
}

function box(w, h, d, color, metalness, roughness) {
  return new THREE.Mesh(
    new THREE.BoxGeometry(w, h, d),
    stdMat(color, metalness ?? 0.65, roughness ?? 0.35)
  );
}

// ═════════════════════════════════════════════════════════════════════════════
//  Public API
// ═════════════════════════════════════════════════════════════════════════════

window.twinInit = function (container, onModuleClick) {
  const W = container.clientWidth  || 800;
  const H = container.clientHeight || 500;

  // Renderer
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(W, H);
  renderer.shadowMap.enabled = false;
  container.appendChild(renderer.domElement);

  // Scene
  scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x000000, 0.0012);

  // Camera
  camera = new THREE.PerspectiveCamera(42, W / H, 0.5, 2000);
  camera.position.set(55, 38, 82);
  camera.lookAt(0, 0, 12);

  // Controls
  controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(0, 0, 12);
  controls.enableDamping    = true;
  controls.dampingFactor    = 0.06;
  controls.autoRotate       = true;
  controls.autoRotateSpeed  = 0.28;
  controls.minDistance      = 18;
  controls.maxDistance      = 250;
  controls.maxPolarAngle    = Math.PI * 0.98;
  controls.update();
  controls.saveState();

  const resetBtn = document.getElementById('btn-reset-camera');
  controls.addEventListener('change', () => {
    // Check if the target has deviated from the default center (0, 0, 12)
    const currentTarget = controls.target;
    const defaultTarget = new THREE.Vector3(0, 0, 12);
    if (resetBtn) {
      if (currentTarget.distanceTo(defaultTarget) > 0.1) {
        resetBtn.style.display = 'block';
      } else {
        resetBtn.style.display = 'none';
      }
    }
  });

  // Lighting — directional sun + deep-space ambient
  const sun = new THREE.DirectionalLight(CLR.sun, 4.0);
  sun.position.set(90, 70, 50);
  scene.add(sun);

  const fill = new THREE.DirectionalLight(0x102030, 0.6);
  fill.position.set(-60, -30, -40);
  scene.add(fill);

  scene.add(new THREE.AmbientLight(CLR.ambient, 1.2));

  // Content
  _buildStarfield();
  _buildEarthGlow();
  _buildISS();

  // Resize observer
  const ro = new ResizeObserver(() => {
    const w = container.clientWidth;
    const h = container.clientHeight;
    if (!w || !h) return;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  });
  ro.observe(container);

  controls.addEventListener('start', () => {
    pointerMoved = true;
  });

  renderer.domElement.addEventListener('pointerdown', e => {
    pointerDown = { x: e.clientX, y: e.clientY };
    pointerMoved = false;
  });

  renderer.domElement.addEventListener('pointermove', e => {
    if (!pointerDown) return;
    const dx = e.clientX - pointerDown.x;
    const dy = e.clientY - pointerDown.y;
    if ((dx * dx + dy * dy) > 16) {
      pointerMoved = true;
    }
  });

  renderer.domElement.addEventListener('pointerup', () => {
    pointerDown = null;
  });

  renderer.domElement.addEventListener('pointercancel', () => {
    pointerDown = null;
    pointerMoved = false;
  });

  // Click → module selection (only true click, not drag)
  renderer.domElement.addEventListener('click', e => {
    if (pointerMoved) {
      pointerMoved = false;
      return;
    }

    const rect = renderer.domElement.getBoundingClientRect();
    const ndc  = new THREE.Vector2(
      ((e.clientX - rect.left) / rect.width)  *  2 - 1,
      ((e.clientY - rect.top)  / rect.height) * -2 + 1,
    );
    const ray = new THREE.Raycaster();
    ray.setFromCamera(ndc, camera);
    const hits = ray.intersectObjects(hitMeshes.map(h => h.mesh));
    if (hits.length && onModuleClick) {
      const entry = hitMeshes.find(h => h.mesh === hits[0].object);
      if (entry) onModuleClick(entry.location);
    }
  });

  renderer.domElement.style.cursor = 'grab';
  renderer.domElement.addEventListener('mousedown', () => {
    renderer.domElement.style.cursor = 'grabbing';
  });
  renderer.domElement.addEventListener('mouseup', () => {
    renderer.domElement.style.cursor = 'grab';
  });

  window.twinResetCamera = () => {
    controls.enableDamping = false;
    controls.update(); // Flush out the remaining velocity/momentum delta first
    controls.reset();  // Put camera back to the exact saved state (0, 0, 12 target) 
    controls.enableDamping = true;
    const resetBtn = document.getElementById('btn-reset-camera');
    if (resetBtn) resetBtn.style.display = 'none';
  };

  _animate();
};

// Called by app.js whenever locationStates changes (WS "state" or "tick" messages).
// Updates indicator sphere color and point light color for each module.
// Setting both .color and .emissive to the same hue gives the glow-orb appearance.
window.twinUpdate = function (locationStates) {
  Object.entries(locationStates).forEach(([loc, state]) => {
    const ind = indicators[loc];
    if (!ind) return;
    const anom  = !!state.active_fault;
    const color = anom ? CLR.anomaly : CLR.nominal;
    ind.anomalous = anom;
    ind.mesh.material.color.setHex(color);
    ind.mesh.material.emissive.setHex(color);
    ind.light.color.setHex(color);
  });
};

// ═════════════════════════════════════════════════════════════════════════════
//  Scene construction
// ═════════════════════════════════════════════════════════════════════════════

function _buildISS() {
  const root = new THREE.Group();
  scene.add(root);

  // ── S0/Z1 connector: Node 2 zenith → Truss ─────────────────────────────
  const connector = box(2.2, 5, 2.2, CLR.truss);
  connector.position.set(0, 2.5, 0);
  root.add(connector);

  // ── Main Integrated Truss Structure ────────────────────────────────────
  const truss = box(112, 2.2, 3, CLR.truss, 0.5, 0.5);
  truss.position.set(0, 5, 0);
  root.add(truss);

  // Small cross-braces along truss
  for (let x = -48; x <= 48; x += 12) {
    const brace = box(0.4, 1.8, 2.8, CLR.truss, 0.5, 0.6);
    brace.position.set(x, 5, 0);
    root.add(brace);
  }

  // ── Solar Array Wings — 4 pairs (8 wings) ──────────────────────────────
  const solarPanelMat = new THREE.MeshStandardMaterial({
    color:     CLR.solar,
    metalness: 0.85,
    roughness: 0.12,
    emissive:  CLR.solarGlow,
    emissiveIntensity: 1,
  });
  const mastMat = stdMat(CLR.mast, 0.7, 0.3);

  // Wing cell pattern (subtle grid lines via wireframe overlay)
  const panelGeo    = new THREE.BoxGeometry(0.18, 13, 36);
  const panelWireGeo = new THREE.EdgesGeometry(new THREE.BoxGeometry(0.18, 13, 36));
  const wireMat     = new THREE.LineBasicMaterial({ color: 0x0a1428, linewidth: 1 });

  [-46, -20, 20, 46].forEach(x => {
    // Mast
    const mast = new THREE.Mesh(new THREE.BoxGeometry(0.5, 20, 0.5), mastMat);
    mast.position.set(x, 5, 0);
    root.add(mast);

    // Panel zenith
    const pZ = new THREE.Mesh(panelGeo, solarPanelMat);
    pZ.position.set(x, 14.5, 0);
    root.add(pZ);
    const wZ = new THREE.LineSegments(panelWireGeo, wireMat);
    wZ.position.copy(pZ.position);
    root.add(wZ);

    // Panel nadir
    const pN = new THREE.Mesh(panelGeo, solarPanelMat);
    pN.position.set(x, -4.5, 0);
    root.add(pN);
    const wN = new THREE.LineSegments(panelWireGeo, wireMat);
    wN.position.copy(pN.position);
    root.add(wN);
  });

  // ── US On-orbit Segment ────────────────────────────────────────────────

  // Node 2 — Harmony (hexagonal node)
  const node2 = cylZ(2.5, 2.5, 6, CLR.module);
  node2.position.set(0, 0, 0);
  root.add(node2);
  // end-caps
  _addEndCaps(root, new THREE.Vector3(0, 0, 0), 2.5, 6, CLR.module);

  // US Lab — Destiny (largest habitat module)
  const lab = cylZ(3.3, 3.3, 9, CLR.module);
  lab.position.set(0, 0, 12);
  root.add(lab);
  _addEndCaps(root, new THREE.Vector3(0, 0, 12), 3.3, 9, CLR.module);

  // Connecting tunnel Node2 → Lab
  const tun1 = cylZ(1.8, 1.8, 3, CLR.moduleDark);
  tun1.position.set(0, 0, 7.5);
  root.add(tun1);

  // Node 1 — Unity
  const node1 = cylZ(2.5, 2.5, 6, CLR.module);
  node1.position.set(0, 0, 25);
  root.add(node1);
  _addEndCaps(root, new THREE.Vector3(0, 0, 25), 2.5, 6, CLR.module);

  // Connecting tunnel Lab → Node1
  const tun2 = cylZ(1.8, 1.8, 4, CLR.moduleDark);
  tun2.position.set(0, 0, 19.5);
  root.add(tun2);

  // Columbus (ESA) — starboard of Node 2
  const columbus = cylX(2.3, 9, 0xc0cce0);
  columbus.position.set(10, 0, -2);
  root.add(columbus);
  _addEndCapsX(root, new THREE.Vector3(10, 0, -2), 2.3, 9, 0xc0cce0);

  // Connecting tunnel Node2 → Columbus
  const tunCo = cylX(1.6, 3.5, CLR.moduleDark);
  tunCo.position.set(4.5, 0, -2);
  root.add(tunCo);

  // JLP & JPM (Kibo) — port of Node 2
  const kibo = box(14, 4.2, 4.5, 0xb8c4d8);
  kibo.position.set(-12, 0, -5);
  root.add(kibo);
  // Kibo exposed facility (EF) — flat porch on top
  const kiboEF = box(6, 0.8, 4, 0xa0b0c4);
  kiboEF.position.set(-14, 2.5, -5);
  root.add(kiboEF);

  // Connecting tunnel Node2 → JLP
  const tunKi = cylX(1.6, 4.5, CLR.moduleDark);
  tunKi.position.set(-5.5, 0, -4);
  root.add(tunKi);

  // Quest Joint Airlock — starboard of Node 1
  const airlock = cylX(2.0, 7, 0xb0bcd0);
  airlock.position.set(9.5, 0, 25);
  root.add(airlock);
  const airlockCrew = cylX(1.6, 4, 0xa8b4c8);
  airlockCrew.position.set(14.5, 0, 25);
  root.add(airlockCrew);

  // Cupola — nadir/port of Node 1 area
  const cupolaBase = new THREE.Mesh(
    new THREE.CylinderGeometry(2.0, 2.2, 1.8, 12),
    stdMat(0xb0bcd0)
  );
  cupolaBase.position.set(-2, -4.5, 27);
  root.add(cupolaBase);
  const cupolaDome = new THREE.Mesh(
    new THREE.SphereGeometry(2.0, 14, 10, 0, Math.PI * 2, 0, Math.PI / 2),
    new THREE.MeshStandardMaterial({ color: 0x8090a8, metalness: 0.3, roughness: 0.15 })
  );
  cupolaDome.rotation.x = Math.PI;   // bowl opening faces down (nadir)
  cupolaDome.position.set(-2, -5.5, 27);
  root.add(cupolaDome);

  // ── Russian Segment (simplified for visual completeness) ───────────────
  const fgb = cylZ(2.1, 2.1, 10, 0xa8b8c8);
  fgb.position.set(0, 0, 35);
  root.add(fgb);

  const sm = cylZ(2.2, 2.2, 13, 0xa0b0c0);
  sm.position.set(0, 0, 50);
  root.add(sm);

  // Connecting tunnel Node1 → FGB
  const tunRus = cylZ(1.6, 1.6, 4, CLR.moduleDark);
  tunRus.position.set(0, 0, 30.5);
  root.add(tunRus);

  // Russian solar arrays (smaller, on SM)
  const rusSolarMat = new THREE.MeshStandardMaterial({
    color: CLR.solar, metalness: 0.85, roughness: 0.15,
    emissive: CLR.solarGlow, emissiveIntensity: 1,
  });
  [-5, 5].forEach(x => {
    const rMast = new THREE.Mesh(new THREE.BoxGeometry(0.4, 16, 0.4), mastMat);
    rMast.position.set(x, 0, 50);
    root.add(rMast);
    const rPanZ = new THREE.Mesh(new THREE.BoxGeometry(0.15, 10, 22), rusSolarMat);
    rPanZ.position.set(x, 9, 50);
    root.add(rPanZ);
    const rPanN = rPanZ.clone();
    rPanN.position.set(x, -9, 50);
    root.add(rPanN);
  });

  // ── Module Indicators & Raycasting Hit Spheres ─────────────────────────
  Object.entries(MODULE_POS).forEach(([location, pos]) => {
    // Glowing indicator sphere
    const glow = new THREE.Mesh(
      new THREE.SphereGeometry(0.85, 16, 16),
      new THREE.MeshStandardMaterial({
        color:             CLR.nominal,
        emissive:          CLR.nominal,
        emissiveIntensity: 1.2,
        metalness:         0,
        roughness:         0.2,
      })
    );
    // Place indicator at module surface (offset toward camera-zenith)
    glow.position.set(pos.x, pos.y + 4.2, pos.z);
    scene.add(glow);

    // Soft point light for glow halo
    const light = new THREE.PointLight(CLR.nominal, 2.0, 18);
    light.position.copy(glow.position);
    scene.add(light);

    indicators[location] = { mesh: glow, light, anomalous: false };

    // Invisible larger sphere for click hit testing
    const hit = new THREE.Mesh(
      new THREE.SphereGeometry(5, 8, 8),
      new THREE.MeshBasicMaterial({ visible: false })
    );
    hit.position.copy(pos);
    scene.add(hit);
    hitMeshes.push({ mesh: hit, location });
  });
}

/** Adds hemisphere end-caps to a Z-axis cylinder. */
function _addEndCaps(parent, center, radius, height, color) {
  const hemiGeo = new THREE.SphereGeometry(radius, 16, 8, 0, Math.PI * 2, 0, Math.PI / 2);
  const mat = stdMat(color);

  const capFwd = new THREE.Mesh(hemiGeo, mat);
  capFwd.rotation.x = Math.PI / 2;
  capFwd.position.set(center.x, center.y, center.z - height / 2);
  parent.add(capFwd);

  const capAft = new THREE.Mesh(hemiGeo, mat);
  capAft.rotation.x = -Math.PI / 2;
  capAft.position.set(center.x, center.y, center.z + height / 2);
  parent.add(capAft);
}

/** Adds hemisphere end-caps to an X-axis cylinder. */
function _addEndCapsX(parent, center, radius, height, color) {
  const hemiGeo = new THREE.SphereGeometry(radius, 16, 8, 0, Math.PI * 2, 0, Math.PI / 2);
  const mat = stdMat(color);

  const capPos = new THREE.Mesh(hemiGeo, mat);
  capPos.rotation.z = -Math.PI / 2;
  capPos.position.set(center.x + height / 2, center.y, center.z);
  parent.add(capPos);

  const capNeg = new THREE.Mesh(hemiGeo, mat);
  capNeg.rotation.z = Math.PI / 2;
  capNeg.position.set(center.x - height / 2, center.y, center.z);
  parent.add(capNeg);
}

function _buildStarfield() {
  const N   = 4000;
  const pos = new Float32Array(N * 3);
  for (let i = 0; i < N * 3; i++) {
    pos[i] = (Math.random() - 0.5) * 2000;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  scene.add(new THREE.Points(geo, new THREE.PointsMaterial({
    color:          CLR.star,
    size:           0.55,
    sizeAttenuation: true,
  })));
}

function _buildEarthGlow() {
  // A large sphere far below — gives subtle blue-green ambient glow of Earth
  const earth = new THREE.Mesh(
    new THREE.SphereGeometry(320, 32, 32),
    new THREE.MeshStandardMaterial({
      color:             0x0a1e3a,
      emissive:          0x071428,
      emissiveIntensity: 0.6,
      metalness:         0,
      roughness:         1,
      side:              THREE.BackSide,
      depthWrite:        false,
    })
  );
  earth.position.set(0, -520, 30);
  scene.add(earth);

  // Earth-reflected light
  const earthLight = new THREE.PointLight(0x0a2040, 1.8, 600);
  earthLight.position.set(0, -180, 30);
  scene.add(earthLight);
}

// ── Animation loop ────────────────────────────────────────────────────────────
// Runs at the display refresh rate via requestAnimationFrame.
// animClock advances by ≈0.016 per frame (~60 fps). The sin() oscillators use
// different frequencies: 3.5 rad/s = fast fault pulse, 1.2 rad/s = slow nominal breathe.
function _animate() {
  requestAnimationFrame(_animate);
  animClock += 0.016;

  Object.values(indicators).forEach(({ mesh, light, anomalous }) => {
    if (anomalous) {
      // Fast red pulse to draw operator attention to faulted module
      const p = 0.5 + 0.5 * Math.sin(animClock * 3.5);
      mesh.material.emissiveIntensity = 0.6 + 1.4 * p;
      light.intensity = 2.5 + 3.5 * p;
    } else {
      // Slow green breathe for nominal modules — subtle, not distracting
      const b = 0.5 + 0.5 * Math.sin(animClock * 1.2);
      mesh.material.emissiveIntensity = 0.8 + 0.4 * b;
      light.intensity = 1.6 + 0.8 * b;
    }
  });

  controls.update();   // required for damping and autoRotate to work
  renderer.render(scene, camera);
}
