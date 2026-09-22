import * as THREE from "three";
import { OrbitControls } from "./vendor/three/OrbitControls.js";
import { TerrainSurface } from "./terrain-surface.mjs";
import { lineProfile } from "./line-profile.mjs";

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
let terrainSurface = null;
let elevationOrigin = 0;
let elevationScale = 1;
const objects = [];
const groups = [];
const layerRows = [];
let selectedScenario = "";
let inspectionHighlight = null;
const radius = 6378137;
const radians = Math.PI / 180;
const project = ([lon, lat]) => [radius * lon * radians, radius * Math.log(Math.tan(Math.PI / 4 + lat * radians / 2))];
const unproject = (x, y) => [x / radius / radians, (2 * Math.atan(Math.exp(y / radius)) - Math.PI / 2) / radians];

function render() { renderer.render(scene, camera); }
function clearHighlight() {
  if (!inspectionHighlight) return;
  inspectionHighlight.children.forEach(line => { line.geometry.dispose(); line.material.dispose(); });
  scene.remove(inspectionHighlight); inspectionHighlight = null;
}
function highlightInterval(properties, interval) {
  clearHighlight();
  const coordinates = properties.inspection_coordinates;
  if (!Array.isArray(coordinates)) return;
  const points = coordinates.slice(interval.firstIndex, interval.lastIndex + 2).map(project)
    .map(([x, y]) => [x - origin[0], y - origin[1]]);
  if (mode3d && (!terrainSurface || properties.inspection_terrain_sha256 !== terrainHash)) return;
  const parts = mode3d ? terrainSurface.drape(points) : [points.map(([x, y]) => [x, y, 0])];
  inspectionHighlight = new THREE.Group();
  for (const part of parts) {
    const geometry = new THREE.BufferGeometry().setFromPoints(part.map(([x, y, z]) => new THREE.Vector3(x, y, z + (mode3d ? .06 * elevationScale : 0))));
    const line = new THREE.Line(geometry, new THREE.LineBasicMaterial({ color: "#c02b28", depthTest: mode3d, transparent: true }));
    line.renderOrder = 5; inspectionHighlight.add(line);
  }
  scene.add(inspectionHighlight); render();
}
function prepareDrape(group) {
  if (!terrainSurface || group.userData.drapePrepared || !group.userData.drapeFeatures) return;
  for (const feature of group.userData.drapeFeatures) {
    for (const part of terrainSurface.drape(feature.points)) {
      // Two centimetres of display-only lift avoids z-fighting, not earthwork.
      const geometry = new THREE.BufferGeometry().setFromPoints(part.map(p => new THREE.Vector3(p[0], p[1], p[2] + .02 * elevationScale)));
      const material = group.children[0].material.clone(); material.depthTest = true;
      const line = new THREE.Line(geometry, material);
      line.userData = { ...feature.properties, view: "3d", display_height: "MESH_INTERPOLATION" };
      line.renderOrder = 1; group.add(line); objects.push(line);
    }
  }
  group.userData.drapePrepared = true;
  group.userData.drapeFeatures = null;
}
function updateLayerVisibility() {
  for (const { group, row, toggle, scenario } of layerRows) {
    const selected = !scenario || scenario === selectedScenario;
    row.hidden = !selected;
    if (group) {
      group.visible = selected && toggle.checked && !(mode3d && group.userData.planOnly);
      if (mode3d && group.visible) prepareDrape(group);
      if (group.userData.toggle) group.children.forEach(line => {
        line.material.depthTest = mode3d;
        line.visible = mode3d ? line.userData.view !== "plan" : line.userData.view !== "3d";
      });
    }
  }
}
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
  clearHighlight();
  const list = document.querySelector("#selection");
  list.replaceChildren();
  const entries = properties.kind === "CONTOUR" ? [
    ["Curva de nivel", properties.id], ["Cota original (m)", properties.elevation_m],
    ["Uso", "Inspecao topografica"],
  ] : properties.kind === "FIELD_BOUNDARY" ? [
    ["Talhao", properties.field_id], ["Limite", properties.interior_ring ? "Exclusao interna" : "Externo"],
    ["Cobertura de cotas", properties.height_coverage_complete ? "Completa" : "Parcial"],
  ] : properties.kind === "SULCATION_ROW" ? [
    ["Linha", properties.row_id || properties.line_id || properties.id],
    ["Talhao", properties.field_id || properties.field_code], ["Comprimento original (m)", properties.length_m],
    ["Raio minimo (m)", properties.min_radius_m ?? properties.min_radius],
    ["Greide maximo (%)", properties.max_abs_grade_pct ?? properties.grade_max],
    ["Uso", properties.inspection_status === "DIAGNOSTIC" ? "Diagnostico; nao liberada" : "Estudo preliminar; nao liberada"],
    ["Pendencias", properties.blocker_codes || "Consultar relatorio da alternativa"],
  ] : [
    ["Caminho", properties.id], ["Trecho", properties.reach_id], ["Receptor", properties.receiver_id],
    ["Comprimento original (m)", properties.length_m], ["Queda original (m)", properties.elevation_drop_m],
    ["Analise", properties.screening_status === "SCREENED_CLEAR" ? "Sem conflito detectado" : "Requer revisao"],
  ];
  if (properties.display_height === "MESH_INTERPOLATION") entries.push(["Altura na cena", "Interpolacao visual da malha"]);
  const oldProfile = document.querySelector("#line-profile");
  oldProfile?.remove();
  if (properties.kind === "SULCATION_ROW") {
    const profile = lineProfile(properties);
    if (profile) {
      const number = value => value.toLocaleString("pt-BR", { maximumFractionDigits: 2 });
      entries.push(["Extensao amostrada (m)", number(profile.length)],
        ["Desnivel final - inicial (m)", number(profile.difference)],
        ["Maior greide entre vertices (%)", number(profile.maximumGrade)],
        ["Cotas originais (m)", `${number(profile.minimum)} a ${number(profile.maximum)}`],
        ["Perfil", "Geometria de origem; nao constitui validacao hidraulica"]);
      if (profile.alert) {
        entries.push(["Referencia de alerta da rodada (%)", number(profile.alert.reference.grade_alert_pct)],
          ["Extensao acima da referencia (m)", number(profile.alert.length)],
          ["Trechos em alerta", profile.alert.intervals.length],
          ["Resultado da triagem", profile.alert.intervals.length ? "Referencia excedida; requer revisao" : "Sem excedencia entre vertices; nao significa aprovacao"]);
      } else entries.push(["Alerta de greide", "Nao avaliado: referencia rastreavel ausente"]);
      const figure = document.createElement("figure"); figure.id = "line-profile";
      const caption = document.createElement("figcaption"); caption.textContent = "Perfil longitudinal / cotas de origem";
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", "0 0 240 120"); svg.setAttribute("role", "img");
      svg.setAttribute("aria-label", `Perfil longitudinal de ${number(profile.length)} metros, cotas de ${number(profile.minimum)} a ${number(profile.maximum)} metros`);
      const path = document.createElementNS(svg.namespaceURI, "polyline");
      const range = profile.maximum - profile.minimum;
      path.setAttribute("points", profile.stations.map((station, i) => `${10 + 220 * station / profile.length},${range ? 110 - 100 * (profile.heights[i] - profile.minimum) / range : 60}`).join(" "));
      path.setAttribute("fill", "none"); path.setAttribute("stroke", "#147547"); path.setAttribute("stroke-width", "2");
      svg.append(path);
      if (profile.alert?.intervals.length) {
        const overlay = document.createElementNS(svg.namespaceURI, "path");
        overlay.setAttribute("d", profile.alert.intervals.map(interval => profile.stations.slice(interval.firstIndex, interval.lastIndex + 2)
          .map((station, offset) => `${offset ? "L" : "M"}${10 + 220 * station / profile.length},${range ? 110 - 100 * (profile.heights[interval.firstIndex + offset] - profile.minimum) / range : 60}`).join(" ")).join(" "));
        overlay.setAttribute("fill", "none"); overlay.setAttribute("stroke", "#c02b28"); overlay.setAttribute("stroke-width", "3");
        overlay.classList.add("profile-alert"); svg.append(overlay);
      }
      const axis = document.createElement("div"); axis.className = "profile-axis";
      const start = document.createElement("span"); start.textContent = "0 m";
      const end = document.createElement("span"); end.textContent = `${number(profile.length)} m`;
      axis.append(start, end); figure.append(caption, svg, axis); list.after(figure);
      if (profile.alert?.intervals.length) {
        const label = document.createElement("label"); label.textContent = "Trecho em alerta"; label.htmlFor = "profile-interval";
        const select = document.createElement("select"); select.id = "profile-interval";
        const placeholder = document.createElement("option"); placeholder.value = ""; placeholder.textContent = "Nenhum trecho destacado"; select.append(placeholder);
        for (const [index, interval] of profile.alert.intervals.slice(0, 200).entries()) {
          const option = document.createElement("option"); option.value = String(index);
          option.textContent = `${number(interval.start)} a ${number(interval.end)} m / max. ${number(interval.maximumGrade)}%`;
          select.append(option);
        }
        select.addEventListener("change", () => {
          if (select.value === "") { clearHighlight(); render(); }
          else highlightInterval(properties, profile.alert.intervals[Number(select.value)]);
        });
        figure.append(label, select);
        if (profile.alert.intervals.length > 200) {
          const note = document.createElement("p"); note.textContent = `Lista: primeiros 200 de ${profile.alert.intervals.length} trechos. Grafico completo.`; figure.append(note);
        }
      }
    } else entries.push(["Perfil longitudinal", "Indisponivel: distancias ou cotas de origem ausentes/invalidas"]);
  }
  for (const [name, value] of entries) {
    const term = document.createElement("dt"); term.textContent = name;
    const detail = document.createElement("dd"); detail.textContent = String(value ?? "Nao informado");
    list.append(term, detail);
  }
}
async function start() {
  if (!runId) throw new Error("Selecione uma rodada nos resultados.");
  const manifest = await getJSON(`/api/runs/${encodeURIComponent(runId)}/map-layers`);
  const alternatives = new Map();
  const scenarioRecords = new Map();
  const scenarioResponse = await getJSON(`/api/runs/${encodeURIComponent(runId)}/scenarios`);
  for (const scenario of scenarioResponse.items || []) {
    if (scenario.run_id === runId && scenario.project_id === manifest.project_id) scenarioRecords.set(scenario.code, scenario);
  }
  for (const layer of manifest.layers) {
    if (layer.status === "READY" && layer.spatial_metadata.scenario_key) alternatives.set(layer.spatial_metadata.scenario_key, layer.spatial_metadata.scenario_name);
  }
  const selector = document.querySelector("#scenario");
  selector.disabled = true;
  for (const [key, name] of [["", "Somente topografia"], ...alternatives]) {
    const option = document.createElement("option"); option.value = key; option.textContent = name; selector.append(option);
  }
  const requestedScenario = new URLSearchParams(location.search).get("scenario");
  selectedScenario = requestedScenario === "" || alternatives.has(requestedScenario) ? requestedScenario : alternatives.keys().next().value || "";
  selector.value = selectedScenario;
  const updateScenarioStatus = () => {
    const scenario = scenarioRecords.get(selectedScenario);
    document.querySelector("#scenario-status").textContent = !selectedScenario ? "" :
      scenario?.status === "CF0_PARTIAL_GEOMETRIC_SCREENING" ? "Geometria parcial. Ha blocos reprovados; implantacao nao liberada." :
      scenario?.status?.includes("DIAGNOSTIC") ? "Alternativa de diagnostico; implantacao nao liberada." :
      "Estudo preliminar; hidraulica e implantacao nao liberadas.";
  };
  updateScenarioStatus();
  document.querySelector("#scenario-control").hidden = !alternatives.size;
  document.querySelector("#back").href = `/#/projects/${encodeURIComponent(manifest.project_id)}/results?run=${encodeURIComponent(runId)}`;
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
  const refreshStatus = () => { status.textContent = `${loaded} ${loaded === 1 ? "camada carregada" : "camadas carregadas"} / ${featureCount} objetos`; };
  // Establish the shared vertical frame before placing any source-height lines.
  const layerOrder = layer => layer.status !== "READY" ? 4 : layer.spatial_metadata.format === "TERRAIN_INSPECTION_MESH" ? 0 : layer.spatial_metadata.scenario_key ? 3 : 1;
  const orderedLayers = [...manifest.layers].sort((a, b) => layerOrder(a) - layerOrder(b));
  for (const [index, layer] of orderedLayers.entries()) {
    status.textContent = `Carregando camadas: ${loaded}/${manifest.ready_count}`;
    const color = layer.spatial_metadata.color || { SOURCE_TERRAIN_SAMPLE: "#b38b00", SOURCE_ROW_HEIGHTS: "#147547" }[layer.spatial_metadata.elevation_policy] || palette[index % palette.length];
    const row = document.createElement("div"); row.className = "layer";
    const label = document.createElement("label");
    const toggle = document.createElement("input"); toggle.type = "checkbox"; toggle.disabled = true;
    const swatch = document.createElement("span"); swatch.className = "swatch"; swatch.style.background = color;
    const title = document.createElement("span"); title.textContent = layer.name;
    const detail = document.createElement("small"); detail.textContent = "Conversao espacial pendente";
    label.append(toggle, swatch, title); row.append(label, detail);
    document.querySelector(layer.status === "READY" ? "#layers" : "#native-layers").append(row);
    if (layer.status !== "READY") document.querySelector("#technical-layers").hidden = false;
    const layerRow = { group: null, row, toggle, scenario: layer.spatial_metadata.scenario_key };
    layerRows.push(layerRow);
    row.hidden = Boolean(layerRow.scenario && layerRow.scenario !== selectedScenario);
    if (layer.status !== "READY") continue;
    toggle.checked = layer.default_visible !== false;
    toggle.disabled = false;
    detail.textContent = "Nao carregada";
    const load = async () => {
      if (layerRow.group) return;
      if (layerRow.loading) return layerRow.loading;
      row.setAttribute("aria-busy", "true"); toggle.disabled = true;
      layerRow.loading = (async () => {
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
        terrainSurface = new TerrainSurface(geometry.attributes.position.array, geometry.index.array);
        const material = new THREE.MeshLambertMaterial({ vertexColors: true, side: THREE.DoubleSide, transparent: true });
        const mesh = new THREE.Mesh(geometry, material); mesh.userData = { terrain: true };
        const group = new THREE.Group(); group.add(mesh); groups.push(group); objects.push(mesh); scene.add(group);
        layerRow.group = group;
        const light = new THREE.DirectionalLight(0xffffff, 1.4); light.position.set(-1000, -1000, 2000); scene.add(light);
        scene.add(new THREE.AmbientLight(0xffffff, 1.4));
        loaded++; featureCount++; terrainLoaded = true; terrainHash = data.source_sha256;
        const opacity = document.createElement("input"); opacity.type = "range"; opacity.min = "0"; opacity.max = "1"; opacity.step = "0.05"; opacity.value = "1";
        opacity.setAttribute("aria-label", `Opacidade de ${layer.name}`);
        opacity.addEventListener("input", () => { material.opacity = Number(opacity.value); render(); });
        row.append(opacity); detail.textContent = `${data.triangles.length.toLocaleString("pt-BR")} triangulos / ${elevationOrigin.toFixed(1)} a ${maximum.toFixed(1)} m`;
        return;
      }
      if (data.type !== "FeatureCollection" || !Array.isArray(data.features)) throw new Error("Geometria indisponivel.");
      const group = new THREE.Group();
      const sampledHeights = layer.spatial_metadata.elevation_policy === "SOURCE_TERRAIN_SAMPLE";
      const sourceHeights = sampledHeights || ["SOURCE_CONTOUR_HEIGHTS", "SOURCE_ROW_HEIGHTS"].includes(layer.spatial_metadata.elevation_policy);
      const aligned3d = sourceHeights && terrainLoaded && typeof terrainHash === "string" && data.terrain_sha256 === terrainHash;
      group.userData.planOnly = !aligned3d;
      group.userData.toggle = toggle;
      if (aligned3d) group.userData.drapeFeatures = [];
      let vertices = 0;
      for (const feature of data.features) {
        const points = feature.geometry?.coordinates;
        if (feature.geometry?.type !== "LineString" || !Array.isArray(points) || points.length < 2 || points.some(point => !Array.isArray(point) || !Number.isFinite(point[0]) || !Number.isFinite(point[1]) || Math.abs(point[0]) > 180 || Math.abs(point[1]) > 85)) throw new Error("Coordenadas fora da cobertura do visualizador.");
        vertices += points.length;
        if (vertices > 200000) throw new Error("Camada requer simplificacao para exibicao.");
        if (sourceHeights && (!Array.isArray(feature.properties?.source_elevations_m) || feature.properties.source_elevations_m.length !== points.length || !feature.properties.source_elevations_m.every(z => Number.isFinite(z) || (sampledHeights && z === null)))) throw new Error("Cotas da linha ausentes ou invalidas.");
      }
      for (const feature of data.features) {
        const coordinates = feature.geometry.coordinates.map(project);
        if (!featureCount) origin = coordinates[0];
        const addLine = (points, heights, view = "both") => {
          const geometry = new THREE.BufferGeometry().setFromPoints(points.map(([x, y], vertex) => new THREE.Vector3(x - origin[0], y - origin[1], heights ? (heights[vertex] - elevationOrigin) * elevationScale : 0)));
          const material = new THREE.LineBasicMaterial({ color, transparent: true, depthTest: false });
          const line = new THREE.Line(geometry, material); line.userData = { ...feature.properties, inspection_coordinates: feature.geometry.coordinates, inspection_terrain_sha256: data.terrain_sha256, view };
          line.visible = view !== "3d"; line.renderOrder = 1;
          group.add(line); objects.push(line);
        };
        if (aligned3d) {
          addLine(coordinates, null, "plan");
          group.userData.drapeFeatures.push({ points: coordinates.map(([x, y]) => [x - origin[0], y - origin[1]]), properties: { ...feature.properties, inspection_coordinates: feature.geometry.coordinates, inspection_terrain_sha256: data.terrain_sha256 } });
        } else addLine(coordinates, null);
        featureCount++;
      }
      groups.push(group); scene.add(group); loaded++;
      layerRow.group = group;
      const opacity = document.createElement("input"); opacity.type = "range"; opacity.min = "0"; opacity.max = "1"; opacity.step = "0.05"; opacity.value = "1";
      opacity.setAttribute("aria-label", `Opacidade de ${layer.name}`);
      opacity.addEventListener("input", () => { group.children.forEach(line => { line.material.opacity = Number(opacity.value); }); render(); });
      row.append(opacity); detail.textContent = `${data.features.length} ${data.features.length === 1 ? "linha" : "linhas"}${aligned3d ? " / altura visual na malha" : " / somente em planta"}`;
    } catch (error) { detail.textContent = error.message; toggle.checked = false; }
      })();
      try { await layerRow.loading; }
      finally {
        layerRow.loading = null; toggle.disabled = false; row.setAttribute("aria-busy", "false");
        updateLayerVisibility(); refreshStatus(); render();
      }
    };
    layerRow.load = load;
    toggle.addEventListener("change", async () => {
      clearHighlight(); document.querySelector("#line-profile")?.remove(); document.querySelector("#selection").replaceChildren();
      if (toggle.checked) await load();
      updateLayerVisibility(); render();
    });
    if (toggle.checked && !row.hidden) await load();
  }
  refreshStatus();
  if (!featureCount) { empty.hidden = false; empty.textContent = "Nenhuma camada vetorial disponivel nesta rodada."; }
  updateLayerVisibility(); fit(); resize();
  selector.disabled = false;
  selector.addEventListener("change", async () => {
    selector.disabled = true;
    selectedScenario = selector.value;
    updateScenarioStatus();
    const url = new URL(location.href);
    url.searchParams.set("scenario", selectedScenario); history.replaceState(null, "", url);
    document.querySelector("#selection").replaceChildren();
    clearHighlight();
    document.querySelector("#line-profile")?.remove();
    updateLayerVisibility(); render();
    try {
      for (const layerRow of layerRows) {
        if (layerRow.scenario === selectedScenario && layerRow.toggle.checked && layerRow.load) await layerRow.load();
      }
    } finally { selector.disabled = false; }
    updateLayerVisibility(); render();
  });
  if (terrainLoaded) {
    document.querySelector("#terrain-status").textContent = "MDT simplificado / datum vertical nao informado";
    document.querySelector("#orbit").disabled = false;
  }
  for (const id of ["plan", "orbit"]) document.getElementById(id).onclick = () => {
    clearHighlight();
    const intervalSelector = document.querySelector("#profile-interval");
    if (intervalSelector) intervalSelector.value = "";
    mode3d = id === "orbit" && terrainLoaded;
    controls.enableRotate = mode3d;
    controls.mouseButtons.LEFT = mode3d ? THREE.MOUSE.ROTATE : THREE.MOUSE.PAN;
    document.querySelector("#plan").setAttribute("aria-pressed", String(!mode3d));
    document.querySelector("#orbit").setAttribute("aria-pressed", String(mode3d));
    updateLayerVisibility();
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
    const hits = raycaster.intersectObjects(objects.filter(object => object.visible && object.parent.visible && object.material.opacity > 0));
    if (!mode3d) {
      const lineIndex = hits.findIndex(hit => !hit.object.userData.terrain);
      if (lineIndex > 0) hits.unshift(hits.splice(lineIndex, 1)[0]);
    }
    if (hits.length && hits[0].object.userData.terrain) {
      clearHighlight();
      document.querySelector("#line-profile")?.remove();
      const list = document.querySelector("#selection"); list.replaceChildren();
      const title = document.createElement("dt"); title.textContent = "Cota interpolada da malha (m)";
      const value = document.createElement("dd"); value.textContent = (hits[0].point.z / elevationScale + elevationOrigin).toFixed(2);
      list.append(title, value);
    } else if (hits.length) showSelection(hits[0].object.userData);
    const point = hits[0]?.point || raycaster.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 0, 1), 0), new THREE.Vector3());
    if (point) { const [lon, lat] = unproject(point.x + origin[0], point.y + origin[1]); document.querySelector("#position").textContent = `${lon.toFixed(6)}, ${lat.toFixed(6)}`; }
  });
  window.addEventListener("pagehide", () => { clearHighlight(); controls.dispose(); objects.forEach(line => { line.geometry.dispose(); line.material.dispose(); }); renderer.dispose(); }, { once: true });
}
start().catch(error => { status.textContent = "Mapa indisponivel"; empty.hidden = false; empty.textContent = error.message; });
window.addEventListener("load", () => window.lucide?.createIcons());
