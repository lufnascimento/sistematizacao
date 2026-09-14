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
  camera.position.set(center.x, center.y, 1000);
  controls.target.set(center.x, center.y, 0);
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
  for (const [name, value] of [
    ["Caminho", properties.id], ["Trecho", properties.reach_id], ["Receptor", properties.receiver_id],
    ["Comprimento original (m)", properties.length_m], ["Queda original (m)", properties.elevation_drop_m],
    ["Analise", properties.screening_status === "SCREENED_CLEAR" ? "Sem conflito detectado" : "Requer revisao"],
  ]) {
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
  for (const [index, layer] of manifest.layers.entries()) {
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
      if (data.type !== "FeatureCollection" || !Array.isArray(data.features)) throw new Error("Geometria indisponivel.");
      const group = new THREE.Group();
      let vertices = 0;
      for (const feature of data.features) {
        const points = feature.geometry?.coordinates;
        if (feature.geometry?.type !== "LineString" || !Array.isArray(points) || points.length < 2 || points.some(point => !Array.isArray(point) || !Number.isFinite(point[0]) || !Number.isFinite(point[1]) || Math.abs(point[0]) > 180 || Math.abs(point[1]) > 85)) throw new Error("Coordenadas fora da cobertura do visualizador.");
        vertices += points.length;
        if (vertices > 200000) throw new Error("Camada requer simplificacao para exibicao.");
      }
      for (const feature of data.features) {
        const coordinates = feature.geometry.coordinates.map(project);
        if (!featureCount) origin = coordinates[0];
        const geometry = new THREE.BufferGeometry().setFromPoints(coordinates.map(([x, y]) => new THREE.Vector3(x - origin[0], y - origin[1], 0)));
        const material = new THREE.LineBasicMaterial({ color: palette[index % palette.length], transparent: true });
        const line = new THREE.Line(geometry, material); line.userData = feature.properties || {};
        group.add(line); objects.push(line); featureCount++;
      }
      groups.push(group); scene.add(group); loaded++;
      toggle.disabled = false; toggle.checked = true;
      toggle.addEventListener("change", () => { group.visible = toggle.checked; render(); });
      const opacity = document.createElement("input"); opacity.type = "range"; opacity.min = "0"; opacity.max = "1"; opacity.step = "0.05"; opacity.value = "1";
      opacity.setAttribute("aria-label", `Opacidade de ${layer.name}`);
      opacity.addEventListener("input", () => { group.children.forEach(line => { line.material.opacity = Number(opacity.value); }); render(); });
      row.append(opacity); detail.textContent = `${data.features.length} ${data.features.length === 1 ? "linha" : "linhas"}`;
    } catch (error) { detail.textContent = error.message; }
  }
  status.textContent = `${loaded} ${loaded === 1 ? "camada carregada" : "camadas carregadas"} / ${featureCount} ${featureCount === 1 ? "linha" : "linhas"}`;
  if (!featureCount) { empty.hidden = false; empty.textContent = "Nenhuma camada vetorial disponivel nesta rodada."; }
  fit(); resize();
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
    if (hits.length) showSelection(hits[0].object.userData);
    const point = raycaster.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 0, 1), 0), new THREE.Vector3());
    if (point) { const [lon, lat] = unproject(point.x + origin[0], point.y + origin[1]); document.querySelector("#position").textContent = `${lon.toFixed(6)}, ${lat.toFixed(6)}`; }
  });
  window.addEventListener("pagehide", () => { controls.dispose(); objects.forEach(line => { line.geometry.dispose(); line.material.dispose(); }); renderer.dispose(); }, { once: true });
}
start().catch(error => { status.textContent = "Mapa indisponivel"; empty.hidden = false; empty.textContent = error.message; });
window.addEventListener("load", () => window.lucide?.createIcons());
