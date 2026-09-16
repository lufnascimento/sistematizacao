import * as THREE from "three";
import { OrbitControls } from "./vendor/three/OrbitControls.js";

const viewport = document.querySelector("#viewport");
const status = document.querySelector("#status");
const empty = document.querySelector("#empty");
const palette = ["#087749", "#b24927", "#245dc1", "#922f78"];
const runId = new URLSearchParams(location.search).get("run");
const scene = new THREE.Scene();
scene.background = new THREE.Color("#edf2ef");
let renderer;
let camera;
let controls;
let origin = [0, 0];
let span = 100;
let mode3d = false;
let terrainLoaded = false;
let terrainHash = null;
let elevationOrigin = 0;
let elevationScale = 1;
const objects = [];
const groups = [];
const radius = 6378137;
const radians = Math.PI / 180;
const project = ([lon, lat]) => [radius * lon * radians, radius * Math.log(Math.tan(Math.PI / 4 + lat * radians / 2))];
const unproject = (x, y) => [x / radius / radians, (2 * Math.atan(Math.exp(y / radius)) - Math.PI / 2) / radians];

function render() { renderer.render(scene, camera); }
function resize() {
  const width = viewport.clientWidth;
  const height = viewport.clientHeight;
  renderer.setSize(width, height);
  const aspect = width / height;
  camera.left = -span * aspect / 2;
  camera.right = span * aspect / 2;
  camera.top = span / 2;
  camera.bottom = -span / 2;
  camera.updateProjectionMatrix();
  render();
}
function fit() {
  const box = new THREE.Box3();
  for (const group of groups.filter(item => item.visible)) box.expandByObject(group);
  if (box.isEmpty()) return;
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  span = Math.max(size.y, size.x / (viewport.clientWidth / viewport.clientHeight), 10) * 1.35;
  camera.up.set(0, mode3d ? 0 : 1, mode3d ? 1 : 0);
  camera.position.set(center.x + (mode3d ? span * .5 : 0), center.y - (mode3d ? span * .7 : 0), center.z + Math.max(span, 1000));
  camera.far = Math.max(span * 20, 10000);
  controls.target.copy(center);
  camera.zoom = 1;
  controls.update();
  resize();
}
async function getJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Falha ao carregar dados (${response.status}).`);
  return response.json();
}
function showSelection(properties) {
  const list = document.querySelector("#selection");
  list.replaceChildren();
  const entries = properties.kind === "CONTOUR" ? [
    ["Curva de nivel", properties.id], ["Cota original (m)", properties.elevation_m],
    ["Uso", "Inspecao topografica"],
  ] : [
    ["Caminho", properties.id], ["Trecho", properties.reach_id], ["Receptor", properties.receiver_id],
    ["Comprimento original (m)", properties.length_m], ["Queda original (m)", properties.elevation_drop_m],
    ["Analise", properties.screening_status === "SCREENED_CLEAR" ? "Sem conflito detectado" : "Requer revisao"],
  ];
  for (const [name, value] of entries) {
    const term = document.createElement("dt"); term.textContent = name;
    const detail = document.createElement("dd"); detail.textContent = String(value ?? "Nao informado");
    list.append(term, detail);
  }
}
async function start() {
  if (!runId) throw new Error("Selecione uma rodada nos resultados.");
  const manifest = await getJSON(`/api/runs/${encodeURIComponent(runId)}/map-layers`);
  document.querySelector("#back").href = `/#/projects/${encodeURIComponent(manifest.project_id)}/results`;
  renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  viewport.prepend(renderer.domElement);
  renderer.domElement.setAttribute("aria-label", "Camadas da rodada em planta");
  camera = new THREE.OrthographicCamera(-50, 50, 50, -50, 0.1, 10000);
  camera.position.set(0, 0, 1000);
  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableRotate = false;
  controls.mouseButtons.LEFT = THREE.MOUSE.PAN;
  controls.addEventListener("change", render);
  new ResizeObserver(resize).observe(viewport);
  let featureCount = 0;
  let loaded = 0;
  // Establish the shared vertical frame before placing any source-height lines.
  const orderedLayers = [...manifest.layers].sort((a, b) => Number(b.spatial_metadata.format === "TERRAIN_INSPECTION_MESH") - Number(a.spatial_metadata.format === "TERRAIN_INSPECTION_MESH"));
  for (const [index, layer] of orderedLayers.entries()) {
    const row = document.createElement("div"); row.className = "layer";
    const label = document.createElement("label");
    const toggle = document.createElement("input"); toggle.type = "checkbox"; toggle.disabled = true;
    const swatch = document.createElement("span"); swatch.className = "swatch"; swatch.style.background = palette[index % palette.length];
    const title = document.createElement("span"); title.textContent = layer.name;
    const detail = document.createElement("small"); detail.textContent = "Conversao espacial pendente";
    label.append(toggle, swatch, title); row.append(label, detail); document.querySelector("#layers").append(row);
    if (layer.status !== "READY") continue;
    try {
      if (layer.size_bytes > 20 * 1024 * 1024) throw new Error("Camada excede 20 MB; requer carregamento progressivo.");
      const data = await getJSON(layer.source_url);
      if (layer.spatial_metadata.format === "TERRAIN_INSPECTION_MESH") {
        if (terrainLoaded) throw new Error("Apenas um MDT por rodada pode definir a referencia 3D.");
        if (data.type !== "TerrainInspectionMesh" || !Array.isArray(data.vertices) || !data.vertices.length || data.vertices.length > 66049 || !Array.isArray(data.triangles) || !data.triangles.length || data.triangles.length > 131072) throw new Error("Malha fora dos limites de exibicao.");
        if (data.vertices.some(p => !Array.isArray(p) || p.length !== 3 || !p.every(Number.isFinite) || Math.abs(p[0]) > 180 || Math.abs(p[1]) > 85) || data.triangles.some(t => !Array.isArray(t) || t.length !== 3 || t.some(i => !Number.isInteger(i) || i < 0 || i >= data.vertices.length))) throw new Error("Malha com coordenadas ou indices invalidos.");
        if (!featureCount) origin = project(data.vertices[0]);
        elevationOrigin = Math.min(...data.vertices.map(p => p[2]));
        const maximum = Math.max(...data.vertices.map(p => p[2]));
        elevationScale = 1 / Math.cos(data.vertices[0][1] * radians);
        const positions = []; const colors = [];
        for (const point of data.vertices) {
          const [x, y] = project(point);
          positions.push(x - origin[0], y - origin[1], (point[2] - elevationOrigin) * elevationScale);
          const color = new THREE.Color().setHSL(.38 - .22 * (point[2] - elevationOrigin) / Math.max(maximum - elevationOrigin, 1), .28, .48);
          colors.push(color.r, color.g, color.b);
        }
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
        geometry.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
        geometry.setIndex(data.triangles.flat()); geometry.computeVertexNormals();
        const material = new THREE.MeshLambertMaterial({ vertexColors: true, side: THREE.DoubleSide, transparent: true });
        const mesh = new THREE.Mesh(geometry, material); mesh.userData = { terrain: true };
        const group = new THREE.Group(); group.add(mesh); groups.push(group); objects.push(mesh); scene.add(group);
        const light = new THREE.DirectionalLight(0xffffff, 1.4); light.position.set(-1000, -1000, 2000); scene.add(light);
        scene.add(new THREE.AmbientLight(0xffffff, 1.4));
        loaded++; featureCount++; terrainLoaded = true; terrainHash = data.source_sha256;
        toggle.disabled = false; toggle.checked = true;
        toggle.addEventListener("change", () => { group.visible = toggle.checked; render(); });
        const opacity = document.createElement("input"); opacity.type = "range"; opacity.min = "0"; opacity.max = "1"; opacity.step = "0.05"; opacity.value = "1";
        opacity.setAttribute("aria-label", `Opacidade de ${layer.name}`);
        opacity.addEventListener("input", () => { material.opacity = Number(opacity.value); render(); });
        row.append(opacity); detail.textContent = `${data.triangles.length.toLocaleString("pt-BR")} triangulos / ${elevationOrigin.toFixed(1)} a ${maximum.toFixed(1)} m`;
        continue;
      }
      if (data.type !== "FeatureCollection" || !Array.isArray(data.features)) throw new Error("Geometria indisponivel.");
      const group = new THREE.Group();
      const sourceHeights = layer.spatial_metadata.elevation_policy === "SOURCE_CONTOUR_HEIGHTS";
      const aligned3d = sourceHeights && terrainLoaded && typeof terrainHash === "string" && data.terrain_sha256 === terrainHash;
      group.userData.planOnly = !aligned3d;
      group.userData.toggle = toggle;
      let vertices = 0;
      for (const feature of data.features) {
        const points = feature.geometry?.coordinates;
        if (feature.geometry?.type !== "LineString" || !Array.isArray(points) || points.length < 2 || points.some(point => !Array.isArray(point) || !Number.isFinite(point[0]) || !Number.isFinite(point[1]) || Math.abs(point[0]) > 180 || Math.abs(point[1]) > 85)) throw new Error("Coordenadas fora da cobertura do visualizador.");
        vertices += points.length;
        if (vertices > 200000) throw new Error("Camada requer simplificacao para exibicao.");
        if (sourceHeights && (!Array.isArray(feature.properties?.source_elevations_m) || feature.properties.source_elevations_m.length !== points.length || !feature.properties.source_elevations_m.every(Number.isFinite))) throw new Error("Cotas da curva ausentes ou invalidas.");
      }
      for (const feature of data.features) {
        const coordinates = feature.geometry.coordinates.map(project);
        if (!featureCount) origin = coordinates[0];
        const geometry = new THREE.BufferGeometry().setFromPoints(coordinates.map(([x, y], vertex) => new THREE.Vector3(x - origin[0], y - origin[1], aligned3d ? (feature.properties.source_elevations_m[vertex] - elevationOrigin) * elevationScale : 0)));
        const material = new THREE.LineBasicMaterial({ color: palette[index % palette.length], transparent: true, depthTest: false });
        const line = new THREE.Line(geometry, material); line.userData = feature.properties || {};
        line.renderOrder = 1;
        group.add(line); objects.push(line); featureCount++;
      }
      groups.push(group); scene.add(group); loaded++;
      toggle.disabled = false; toggle.checked = true;
      toggle.addEventListener("change", () => { group.visible = toggle.checked && !(mode3d && group.userData.planOnly); render(); });
      const opacity = document.createElement("input"); opacity.type = "range"; opacity.min = "0"; opacity.max = "1"; opacity.step = "0.05"; opacity.value = "1";
      opacity.setAttribute("aria-label", `Opacidade de ${layer.name}`);
      opacity.addEventListener("input", () => { group.children.forEach(line => { line.material.opacity = Number(opacity.value); }); render(); });
      row.append(opacity); detail.textContent = `${data.features.length} ${data.features.length === 1 ? "linha" : "linhas"}${aligned3d ? " / cotas do MDT original" : " / somente em planta"}`;
    } catch (error) { detail.textContent = error.message; }
  }
  status.textContent = `${loaded} ${loaded === 1 ? "camada carregada" : "camadas carregadas"} / ${featureCount} ${terrainLoaded ? featureCount === 1 ? "objeto" : "objetos" : featureCount === 1 ? "linha" : "linhas"}`;
  if (!featureCount) { empty.hidden = false; empty.textContent = "Nenhuma camada vetorial disponivel nesta rodada."; }
  fit(); resize();
  if (terrainLoaded) {
    document.querySelector("#terrain-status").textContent = "MDT simplificado / datum vertical nao informado";
    document.querySelector("#orbit").disabled = false;
  }
  for (const id of ["plan", "orbit"]) document.getElementById(id).onclick = () => {
    mode3d = id === "orbit" && terrainLoaded;
    controls.enableRotate = mode3d;
    for (const group of groups) {
      if (group.userData.toggle) {
        group.visible = group.userData.toggle.checked && !(mode3d && group.userData.planOnly);
        group.children.forEach(line => { line.material.depthTest = mode3d; });
      }
    }
    controls.mouseButtons.LEFT = mode3d ? THREE.MOUSE.ROTATE : THREE.MOUSE.PAN;
    document.querySelector("#plan").setAttribute("aria-pressed", String(!mode3d));
    document.querySelector("#orbit").setAttribute("aria-pressed", String(mode3d));
    fit();
  };
  document.querySelector("#fit").onclick = fit;
  for (const [id, factor] of [["zoom-in", 1.4], ["zoom-out", 1 / 1.4]]) document.getElementById(id).onclick = () => { camera.zoom = THREE.MathUtils.clamp(camera.zoom * factor, 0.01, 10000); camera.updateProjectionMatrix(); render(); };
  const raycaster = new THREE.Raycaster();
  let down = null;
  renderer.domElement.addEventListener("pointerdown", event => { down = [event.clientX, event.clientY]; });
  renderer.domElement.addEventListener("pointerup", event => {
    if (!down || Math.hypot(event.clientX - down[0], event.clientY - down[1]) > 5) return;
    const rect = renderer.domElement.getBoundingClientRect();
    const pointer = new THREE.Vector2((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1);
    raycaster.params.Line.threshold = span / camera.zoom / rect.height * 8;
    raycaster.setFromCamera(pointer, camera);
    const hits = raycaster.intersectObjects(objects.filter(object => object.parent.visible && object.material.opacity > 0));
    if (!mode3d) {
      const lineIndex = hits.findIndex(hit => !hit.object.userData.terrain);
      if (lineIndex > 0) hits.unshift(hits.splice(lineIndex, 1)[0]);
    }
    if (hits.length && hits[0].object.userData.terrain) {
      const list = document.querySelector("#selection"); list.replaceChildren();
      const title = document.createElement("dt"); title.textContent = "Cota interpolada da malha (m)";
      const value = document.createElement("dd"); value.textContent = (hits[0].point.z / elevationScale + elevationOrigin).toFixed(2);
      list.append(title, value);
    } else if (hits.length) showSelection(hits[0].object.userData);
    const point = hits[0]?.point || raycaster.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 0, 1), 0), new THREE.Vector3());
    if (point) { const [lon, lat] = unproject(point.x + origin[0], point.y + origin[1]); document.querySelector("#position").textContent = `${lon.toFixed(6)}, ${lat.toFixed(6)}`; }
  });
  window.addEventListener("pagehide", () => { controls.dispose(); objects.forEach(line => { line.geometry.dispose(); line.material.dispose(); }); renderer.dispose(); }, { once: true });
}
start().catch(error => { status.textContent = "Mapa indisponivel"; empty.hidden = false; empty.textContent = error.message; });
window.addEventListener("load", () => window.lucide?.createIcons());
