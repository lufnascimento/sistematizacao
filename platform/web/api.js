const DEFAULT_API_BASE = "/api";
const MOCK_STORAGE_KEY = "terraflux.mock.v1";

export class ApiError extends Error {
  constructor(message, status = 0, details = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.details = details;
  }
}

const wait = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

function makeId(prefix) {
  const suffix = crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${suffix}`;
}

function nowIso() {
  return new Date().toISOString();
}

function deepCopy(value) {
  return JSON.parse(JSON.stringify(value));
}

function createMockState() {
  const projectId = "demo-fazenda-santa-fe";
  const runId = "run-e0-cf0-c1-demo";
  return {
    projects: [
      {
        id: projectId,
        code: "SF-2026-01",
        name: "Projeto demonstrativo",
        farm_name: "Unidade sintética",
        municipality: "Ribeirão Preto",
        state: "SP",
        crs: "SIRGAS 2000 / UTM 23S",
        area_ha: 412.8,
        description: "Sistematização e cenários de sulcação para renovação de canaviais.",
        status: "IN_PROGRESS",
        current_step: "RESULTS",
        progress: 82,
        asset_count: 7,
        run_count: 3,
        updated_at: nowIso(),
        created_at: "2026-08-24T11:18:00.000Z",
      },
      {
        id: "demo-unidade-rio-claro",
        code: "RC-2026-04",
        name: "Unidade Rio Claro",
        farm_name: "Unidade Rio Claro",
        municipality: "Rio Claro",
        state: "SP",
        crs: "SIRGAS 2000 / UTM 23S",
        area_ha: 186.2,
        description: "Avaliação inicial do relevo e continuidade entre talhões.",
        status: "DRAFT",
        current_step: "DATA",
        progress: 24,
        asset_count: 2,
        run_count: 0,
        updated_at: "2026-08-25T14:22:00.000Z",
        created_at: "2026-08-25T09:05:00.000Z",
      },
    ],
    assets: {
      [projectId]: [
        {
          id: "asset-laz-001",
          name: "fazenda_santa_fe.laz",
          kind: "POINT_CLOUD",
          size: 483512748,
          status: "APPROVED",
          crs: "EPSG:31983",
          coverage: 100,
          checksum: "f9b2…a31d",
          created_at: "2026-08-24T11:34:00.000Z",
        },
        {
          id: "asset-field-001",
          name: "talhoes.gpkg",
          kind: "FIELD_BOUNDARIES",
          size: 924118,
          status: "APPROVED",
          crs: "EPSG:31983",
          coverage: 100,
          checksum: "601c…3f84",
          created_at: "2026-08-24T11:38:00.000Z",
        },
        {
          id: "asset-ortho-001",
          name: "ortomosaico_cog.tif",
          kind: "ORTHOMOSAIC",
          size: 1738483942,
          status: "AVAILABLE_SCREENING",
          crs: "EPSG:31983",
          coverage: 98.7,
          checksum: "c140…dd98",
          created_at: "2026-08-24T12:02:00.000Z",
        },
        {
          id: "asset-road-001",
          name: "carreadores.shp",
          kind: "ROADS",
          size: 312248,
          status: "PARTIAL",
          crs: "EPSG:31983",
          coverage: 78,
          checksum: "041f…9c10",
          created_at: "2026-08-24T12:15:00.000Z",
        },
      ],
    },
    readiness: {
      [projectId]: {
        project_id: projectId,
        maximum_delivery_level: "E0_TRIAGEM",
        score: 72,
        evidence_summary: { available: 8, partial: 6, missing: 11, not_applicable: 1 },
        parameter_summary: { resolved: 35, pending: 116, total: 151 },
        blocking_count: 8,
        warnings_count: 5,
        packages: [
          { id: "PROJECT_SCOPE", name: "Escopo e talhões", state: "AVAILABLE", note: "Polígonos válidos e identificados" },
          { id: "TERRAIN_SOURCE", name: "Nuvem e terreno", state: "AVAILABLE_SCREENING", note: "Precisão vertical ainda não certificada" },
          { id: "POWER_INFRASTRUCTURE", name: "Rede elétrica", state: "NOT_APPLICABLE", note: "DECLARED_NONE pelo responsável" },
          { id: "SOIL_HYDROLOGY", name: "Solo e hidrologia", state: "MISSING", note: "Necessário para PCE, PCX e hidráulica" },
          { id: "FLEET_CONFIGURATION", name: "Frota e implementos", state: "PARTIAL", note: "Raio mínimo usa preset do sistema" },
          { id: "RECEIVERS", name: "Receptores e saídas", state: "MISSING", note: "Bloqueia TD, base larga e ESD" },
        ],
        scenarios: [
          { id: "E0", name: "E0 geométrico", state: "READY", delivery: "E0" },
          { id: "CF0", name: "Curva contínua", state: "READY", delivery: "E0" },
          { id: "C1", name: "Curva embutida", state: "LIMITED", delivery: "E0" },
          { id: "C2", name: "Base larga/passante", state: "BLOCKED", delivery: "E2" },
          { id: "C3", name: "ESD", state: "BLOCKED", delivery: "E2" },
          { id: "POA", name: "POA e logística", state: "BLOCKED", delivery: "E1" },
        ],
      },
    },
    configurations: {
      [projectId]: {
        preset_id: "cana_sp_equilibrio_e0",
        parameters: {
          "topography.resolution_m": 1.0,
          "topography.contour_interval_m": 1.0,
          "topography.elevation_source_preference": "POINT_CLOUD",
          "sulcation.row_spacing_m": 1.5,
          "sulcation.headland_width_m": 18.0,
          "sulcation.min_turn_radius_m": 12.0,
          "sulcation.min_shot_length_m": 50.0,
          "sulcation.nominal_speed_kmh": 5.0,
          "sulcation.allow_cross_field": true,
          "sulcation.allow_cross_property": false,
          "constraints.power_network_state": "DECLARED_NONE",
          "constraints.power_line_buffer_m": 15.0,
          "constraints.general_review_status": "NOT_REVIEWED",
          "logistics.poa_enabled": false,
        },
      },
    },
    selections: {
      [projectId]: ["TOPOGRAPHY_E0"],
    },
    requests: {
      [projectId]: {
        id: "request-demo-001",
        project_id: projectId,
        status: "COMPILED",
        hash: "bd748e46fcbc163b416b9b09904e5ce77aad1a0c19d06acd4131071847b852f1",
        products: ["TOPOGRAPHY_E0"],
        created_at: "2026-08-25T15:12:00.000Z",
      },
    },
    runs: [
      {
        id: runId,
        project_id: projectId,
        project_name: "Projeto demonstrativo",
        request_id: "request-demo-001",
        name: "Rodada E0 + CF0 + C1",
        status: "SUCCEEDED",
        stage: "Publicação concluída",
        progress: 100,
        started_at: "2026-08-25T16:00:00.000Z",
        finished_at: "2026-08-25T17:18:40.000Z",
        duration_seconds: 4720,
        artifact_count: 12,
        delivery_boundary: "C1_E0_CONCEPT_ALIGNMENT_NOT_DIMENSIONED",
        logs: [
          { timestamp: "16:00:01", level: "INFO", message: "Pedido imutável validado; iniciando ambiente geoespacial." },
          { timestamp: "16:00:12", level: "INFO", message: "MDT 1 m e dois talhões carregados em EPSG:31983." },
          { timestamp: "16:01:08", level: "INFO", message: "E0: 360 combinações avaliadas; seis representantes selecionados." },
          { timestamp: "16:03:42", level: "INFO", message: "E0: linhas e nós operacionais verificados." },
          { timestamp: "16:03:44", level: "WARNING", message: "Precisão vertical não validada: resultados limitados a triagem E0." },
          { timestamp: "17:08:21", level: "INFO", message: "CF0: 1.775 linhas publicadas; geometrias inválidas = 0." },
          { timestamp: "17:14:18", level: "INFO", message: "C1 TI: 24 alternativas, 604 eixos e 316 faixas." },
          { timestamp: "17:14:19", level: "WARNING", message: "TD não gerado: receptor hidráulico ausente." },
          { timestamp: "17:17:11", level: "INFO", message: "Verificadores concluídos: 121 testes aprovados." },
          { timestamp: "17:18:40", level: "INFO", message: "Relatórios e dossiê publicados com manifesto e checksums." },
        ],
      },
      {
        id: "run-e0-demo-previous",
        project_id: projectId,
        project_name: "Projeto demonstrativo",
        request_id: "request-demo-000",
        name: "Triagem geométrica E0",
        status: "SUCCEEDED",
        stage: "Concluída",
        progress: 100,
        started_at: "2026-08-24T13:10:00.000Z",
        finished_at: "2026-08-24T13:32:18.000Z",
        duration_seconds: 1338,
        artifact_count: 5,
        delivery_boundary: "E0_TRIAGEM",
        logs: [],
      },
    ],
    artifacts: {
      [runId]: [
        {
          id: "artifact-dossier",
          name: "Dossiê completo de sistematização",
          kind: "PDF",
          format: "PDF",
          size: 8843120,
          pages: 75,
          status: "VERIFIED",
          checksum: "2e38…0bd1",
          download_url: null,
          created_at: "2026-08-25T17:18:30.000Z",
        },
        {
          id: "artifact-c1-report",
          name: "Relatório C1 — curva embutida E0",
          kind: "PDF",
          format: "PDF",
          size: 2401834,
          pages: 12,
          status: "VERIFIED",
          checksum: "f90c…2280",
          download_url: null,
          created_at: "2026-08-25T17:16:00.000Z",
        },
        {
          id: "artifact-c1-map",
          name: "Mapa comparativo C1",
          kind: "MAP",
          format: "PNG",
          size: 3120451,
          status: "VERIFIED",
          checksum: "ef10…da03",
          preview_url: null,
          download_url: null,
          created_at: "2026-08-25T17:15:00.000Z",
        },
        {
          id: "artifact-c1-gpkg",
          name: "Camadas técnicas C1",
          kind: "VECTOR",
          format: "GPKG",
          size: 23844190,
          status: "VERIFIED",
          checksum: "b4f1…078c",
          download_url: null,
          created_at: "2026-08-25T17:15:02.000Z",
        },
        {
          id: "artifact-e0-map",
          name: "Cenários de sulcação E0",
          kind: "MAP",
          format: "PNG",
          size: 4862448,
          status: "VERIFIED",
          checksum: "ac21…a8d3",
          preview_url: null,
          download_url: null,
          created_at: "2026-08-25T16:04:00.000Z",
        },
        {
          id: "artifact-manifest",
          name: "Manifesto da execução",
          kind: "MANIFEST",
          format: "JSON",
          size: 78214,
          status: "VERIFIED",
          checksum: "afe4…99a1",
          download_url: null,
          created_at: "2026-08-25T17:18:39.000Z",
        },
      ],
    },
    scenarios: {
      [runId]: [
        {
          id: "scenario-e0-conservation",
          code: "E0A",
          name: "Conservação",
          family: "E0",
          status: "E0_SCREENING_ONLY_NOT_AUTHORIZED",
          geometry_eligible: true,
          guidance_authorized: false,
          selected_for_review: false,
          recommended: false,
          metrics: {
            average_shot_m: 468,
            p95_shot_m: 812,
            maneuvers_per_ha: 2.9,
            cross_slope_p95_pct: 7.8,
            row_length_km: 171.4,
            operational_score: 72,
            conservation_score: 91,
            harvestability_score: 69,
          },
        },
        {
          id: "scenario-e0-balanced",
          code: "E0C",
          name: "Equilíbrio",
          family: "E0",
          status: "E0_SCREENING_ONLY_NOT_AUTHORIZED",
          geometry_eligible: true,
          guidance_authorized: false,
          selected_for_review: false,
          recommended: true,
          metrics: {
            average_shot_m: 624,
            p95_shot_m: 1045,
            maneuvers_per_ha: 2.1,
            cross_slope_p95_pct: 8.9,
            row_length_km: 175.8,
            operational_score: 86,
            conservation_score: 84,
            harvestability_score: 87,
          },
        },
        {
          id: "scenario-operation",
          code: "CF0B",
          name: "Operação",
          family: "CF0",
          status: "ELIGIBLE_E0",
          recommended: false,
          metrics: {
            average_shot_m: 756,
            p95_shot_m: 1288,
            maneuvers_per_ha: 1.6,
            cross_slope_p95_pct: 10.7,
            row_length_km: 179.2,
            operational_score: 94,
            conservation_score: 68,
            harvestability_score: 83,
          },
        },
        {
          id: "scenario-c1-ti",
          code: "C1-TI-04",
          name: "C1 TI — intervalo 4 m",
          family: "C1_SCREENING",
          status: "CONCEPT_ONLY",
          recommended: false,
          metrics: {
            average_shot_m: 441,
            p95_shot_m: 772,
            maneuvers_per_ha: 3.2,
            cross_slope_p95_pct: 6.9,
            row_length_km: 168.1,
            operational_score: 69,
            conservation_score: 93,
            harvestability_score: 65,
          },
        },
      ],
    },
    scenarioSelections: {},
  };
}

class MockGateway {
  constructor() {
    const saved = window.localStorage.getItem(MOCK_STORAGE_KEY);
    try {
      this.state = saved ? JSON.parse(saved) : createMockState();
    } catch {
      this.state = createMockState();
    }
  }

  persist() {
    window.localStorage.setItem(MOCK_STORAGE_KEY, JSON.stringify(this.state));
  }

  async listProjects() {
    await wait(180);
    return deepCopy(this.state.projects);
  }

  async getProject(projectId) {
    await wait(100);
    const project = this.state.projects.find((item) => item.id === projectId);
    if (!project) throw new ApiError("Projeto não encontrado.", 404);
    return deepCopy(project);
  }

  async createProject(payload) {
    await wait(300);
    const project = {
      id: makeId("project"),
      code: payload.code || `PRJ-${String(this.state.projects.length + 1).padStart(3, "0")}`,
      name: payload.name,
      farm_name: payload.farm_name || payload.name,
      municipality: payload.municipality,
      state: payload.state,
      crs: payload.crs,
      description: payload.description || "",
      area_ha: 0,
      status: "DRAFT",
      current_step: "DATA",
      progress: 8,
      asset_count: 0,
      run_count: 0,
      created_at: nowIso(),
      updated_at: nowIso(),
    };
    this.state.projects.unshift(project);
    this.state.assets[project.id] = [];
    this.state.configurations[project.id] = {
      preset_id: "cana_sp_equilibrio_e0",
      parameters: {},
    };
    this.state.selections[project.id] = ["E0", "REPORTS"];
    this.persist();
    return deepCopy(project);
  }

  async updateProject(projectId, payload) {
    const project = this.state.projects.find((item) => item.id === projectId);
    if (!project) throw new ApiError("Projeto não encontrado.", 404);
    Object.assign(project, payload, { updated_at: nowIso() });
    this.persist();
    return deepCopy(project);
  }

  async listAssets(projectId) {
    await wait(150);
    return deepCopy(this.state.assets[projectId] || []);
  }

  async uploadAsset(projectId, file, metadata, onProgress) {
    const steps = [8, 22, 41, 63, 79, 92, 100];
    for (const progress of steps) {
      await wait(100 + Math.random() * 120);
      onProgress?.(progress);
    }
    const asset = {
      id: makeId("asset"),
      name: file.name,
      kind: metadata.kind || "OTHER",
      size: file.size,
      status: "VALIDATING",
      crs: metadata.crs || "A confirmar",
      coverage: null,
      checksum: "calculando",
      created_at: nowIso(),
    };
    this.state.assets[projectId] ||= [];
    this.state.assets[projectId].unshift(asset);
    const project = this.state.projects.find((item) => item.id === projectId);
    if (project) {
      project.asset_count = this.state.assets[projectId].length;
      project.progress = Math.max(project.progress || 0, 18);
      project.updated_at = nowIso();
    }
    this.persist();
    return deepCopy(asset);
  }

  async deleteAsset(projectId, assetId) {
    this.state.assets[projectId] = (this.state.assets[projectId] || []).filter((item) => item.id !== assetId);
    this.persist();
    return { ok: true };
  }

  async getReadiness(projectId) {
    await wait(180);
    if (this.state.readiness[projectId]) return deepCopy(this.state.readiness[projectId]);
    const assets = this.state.assets[projectId] || [];
    const hasBoundary = assets.some((item) => item.kind === "FIELD_BOUNDARIES");
    const hasTerrain = assets.some((item) => ["POINT_CLOUD", "DEM", "ORTHOMOSAIC"].includes(item.kind));
    return {
      project_id: projectId,
      maximum_delivery_level: hasBoundary && hasTerrain ? "E0_TRIAGEM" : "NOT_READY",
      score: (hasBoundary ? 22 : 0) + (hasTerrain ? 34 : 0),
      evidence_summary: { available: Number(hasBoundary) + Number(hasTerrain), partial: 0, missing: 26 - Number(hasBoundary) - Number(hasTerrain), not_applicable: 0 },
      parameter_summary: { resolved: 0, pending: 151, total: 151 },
      blocking_count: hasBoundary && hasTerrain ? 8 : 10,
      warnings_count: 0,
      packages: [
        { id: "PROJECT_SCOPE", name: "Escopo e talhões", state: hasBoundary ? "AVAILABLE" : "MISSING", note: hasBoundary ? "Arquivo recebido" : "Envie os polígonos de talhões" },
        { id: "TERRAIN_SOURCE", name: "Nuvem e terreno", state: hasTerrain ? "AVAILABLE_SCREENING" : "MISSING", note: hasTerrain ? "Fonte disponível para QA" : "Envie LAZ/LAS, MDT ou ortomosaico" },
        { id: "POWER_INFRASTRUCTURE", name: "Rede elétrica", state: "PENDING", note: "Envie a linha ou declare inexistente" },
      ],
      scenarios: [
        { id: "E0", name: "E0 geométrico", state: hasBoundary && hasTerrain ? "READY" : "BLOCKED", delivery: "E0" },
        { id: "CF0", name: "Curva contínua", state: hasBoundary && hasTerrain ? "READY" : "BLOCKED", delivery: "E0" },
        { id: "C1", name: "Curva embutida", state: "BLOCKED", delivery: "E0" },
        { id: "C2", name: "Base larga/passante", state: "BLOCKED", delivery: "E2" },
        { id: "C3", name: "ESD", state: "BLOCKED", delivery: "E2" },
        { id: "POA", name: "POA e logística", state: "BLOCKED", delivery: "E1" },
      ],
    };
  }

  async getConfiguration(projectId) {
    await wait(100);
    return deepCopy(this.state.configurations[projectId] || { preset_id: null, parameters: {} });
  }

  async saveConfiguration(projectId, payload) {
    await wait(220);
    this.state.configurations[projectId] = deepCopy(payload);
    this.persist();
    return deepCopy(payload);
  }

  async saveProductSelection(projectId, productIds) {
    await wait(100);
    this.state.selections[projectId] = [...productIds];
    this.persist();
    return { products: [...productIds] };
  }

  async getProductSelection(projectId) {
    return { products: deepCopy(this.state.selections[projectId] || ["E0", "REPORTS"]) };
  }

  async compileRequest(projectId, payload) {
    await wait(350);
    const request = {
      id: makeId("request"),
      project_id: projectId,
      status: "COMPILED",
      hash: Array.from({ length: 64 }, () => Math.floor(Math.random() * 16).toString(16)).join(""),
      products: payload.products,
      created_at: nowIso(),
    };
    this.state.requests[projectId] = request;
    this.persist();
    return deepCopy(request);
  }

  async getLatestRequest(projectId) {
    return deepCopy(this.state.requests[projectId] || null);
  }

  async startRun(requestId, payload = {}) {
    await wait(350);
    const request = Object.values(this.state.requests).find((item) => item?.id === requestId);
    if (!request) throw new ApiError("Pedido compilado não encontrado.", 404);
    const project = this.state.projects.find((item) => item.id === request.project_id);
    const run = {
      id: makeId("run"),
      project_id: request.project_id,
      project_name: project?.name || "Projeto",
      request_id: requestId,
      name: payload.name || "Nova rodada de cenários",
      status: "QUEUED",
      stage: "Aguardando worker",
      progress: 2,
      started_at: nowIso(),
      finished_at: null,
      duration_seconds: 0,
      artifact_count: 0,
      delivery_boundary: "PENDING",
      logs: [{ timestamp: new Date().toLocaleTimeString("pt-BR"), level: "INFO", message: "Execução criada e adicionada à fila." }],
    };
    this.state.runs.unshift(run);
    if (project) {
      project.run_count = (project.run_count || 0) + 1;
      project.current_step = "RUN";
      project.status = "PROCESSING";
      project.updated_at = nowIso();
    }
    this.persist();
    return deepCopy(run);
  }

  async listRuns(projectId = null) {
    await wait(160);
    const runs = projectId ? this.state.runs.filter((item) => item.project_id === projectId) : this.state.runs;
    return deepCopy(runs);
  }

  async getRun(runId) {
    await wait(100);
    const run = this.state.runs.find((item) => item.id === runId);
    if (!run) throw new ApiError("Execução não encontrada.", 404);
    if (["QUEUED", "RUNNING"].includes(run.status)) {
      if (run.progress < 8) {
        run.status = "RUNNING";
        run.stage = "Validando entradas";
      }
      run.progress = Math.min(94, run.progress + Math.ceil(Math.random() * 7));
      if (run.progress > 25) run.stage = "Gerando alternativas geométricas";
      if (run.progress > 58) run.stage = "Verificando restrições";
      if (run.progress > 82) run.stage = "Publicando artefatos";
      run.duration_seconds = Math.floor((Date.now() - new Date(run.started_at).getTime()) / 1000);
      const lastBucket = run.logs.length;
      if (lastBucket < Math.floor(run.progress / 10)) {
        run.logs.push({ timestamp: new Date().toLocaleTimeString("pt-BR"), level: "INFO", message: `${run.stage}: ${run.progress}% concluído.` });
      }
      this.persist();
    }
    return deepCopy(run);
  }

  async cancelRun(runId) {
    const run = this.state.runs.find((item) => item.id === runId);
    if (!run) throw new ApiError("Execução não encontrada.", 404);
    run.status = "CANCELLED";
    run.stage = "Cancelada pelo usuário";
    run.finished_at = nowIso();
    this.persist();
    return deepCopy(run);
  }

  async listArtifacts(runId) {
    await wait(150);
    return deepCopy(this.state.artifacts[runId] || []);
  }

  async listScenarios(runId) {
    await wait(150);
    return deepCopy(this.state.scenarios[runId] || []);
  }

  async selectScenario(runId, scenarioId, payload = {}) {
    await wait(220);
    const run = this.state.runs.find((item) => item.id === runId);
    if (!run) throw new ApiError("Execução não encontrada.", 404);
    if (run.status !== "SUCCEEDED") throw new ApiError("A seleção só fica disponível após a conclusão da rodada.", 409);
    const scenarios = this.state.scenarios[runId] || [];
    const scenario = scenarios.find((item) => item.id === scenarioId);
    if (!scenario) throw new ApiError("Cenário não encontrado.", 404);

    const selecting = payload.selected_for_review !== false;
    const family = String(scenario.family || "").toUpperCase();
    const code = String(scenario.code || "").toUpperCase();
    const selectable = (family.startsWith("E0") || code.startsWith("E0"))
      && scenario.status === "E0_SCREENING_ONLY_NOT_AUTHORIZED"
      && scenario.geometry_eligible === true
      && scenario.guidance_authorized === false;
    if (selecting && !selectable) throw new ApiError("Somente um cenário E0 elegível pode representar a revisão técnica.", 409);
    if (!selecting && scenario.selected_for_review !== true) throw new ApiError("Somente o representante ativo pode ser retirado.", 409);

    const eventAt = nowIso();
    const selectionId = makeId("selection");
    const superseded = [];
    if (selecting) {
      for (const candidate of scenarios) {
        if (candidate.id === scenarioId || candidate.selected_for_review !== true) continue;
        superseded.push(candidate.id);
        Object.assign(candidate, {
          selected_for_review: false,
          selected_at: null,
          selection_scope: null,
          selection_id: null,
          deselected_at: eventAt,
          deselected_by_selection_id: selectionId,
        });
      }
      Object.assign(scenario, {
        selected_for_review: true,
        selected_at: eventAt,
        selection_scope: payload.selection_scope || "E0_REPRESENTATIVE_FOR_TECHNICAL_REVIEW",
        selection_id: selectionId,
        deselected_at: null,
        deselected_by_selection_id: null,
        guidance_authorized: false,
      });
    } else {
      Object.assign(scenario, {
        selected_for_review: false,
        selected_at: null,
        selection_scope: null,
        selection_id: null,
        deselected_at: eventAt,
        deselected_by_selection_id: selectionId,
        guidance_authorized: false,
      });
    }
    const selection = {
      id: selectionId,
      run_id: runId,
      scenario_id: scenarioId,
      event: selecting ? "SELECTED" : "DESELECTED",
      selected_for_review: selecting,
      superseded_scenario_ids: superseded,
      guidance_authorized: false,
      created_at: eventAt,
    };
    this.state.scenarioSelections ||= {};
    this.state.scenarioSelections[runId] ||= [];
    this.state.scenarioSelections[runId].unshift(selection);
    this.persist();
    return { scenario: deepCopy(scenario), selection: deepCopy(selection) };
  }

  async reviewArtifact(artifactId, payload) {
    await wait(220);
    return { id: makeId("review"), artifact_id: artifactId, ...payload, created_at: nowIso() };
  }

  reset() {
    this.state = createMockState();
    this.persist();
  }
}

export const systemCatalog = {
  presets: [
    {
      id: "cana_sp_equilibrio_e0",
      name: "Cana SP — equilíbrio inicial",
      description: "Triagem geométrica equilibrando conservação, colheitabilidade e rendimento operacional.",
      region: "São Paulo",
      level: "Estudo preliminar",
      source: "Sistema",
    },
    {
      id: "cana_sp_conservacao_e0",
      name: "Cana SP — conservação inicial",
      description: "Prioriza menor alcance hidráulico e transversalidade ao escoamento, sem dimensionar estruturas.",
      region: "São Paulo",
      level: "Estudo preliminar",
      source: "Sistema",
    },
    {
      id: "cana_operacao_tiros_longos_e0",
      name: "Tiros longos — operação",
      description: "Amplia continuidade e comprimento de tiro dentro de todos os gates disponíveis.",
      region: "Brasil",
      level: "Estudo preliminar",
      source: "Sistema",
    },
    {
      id: "custom",
      name: "Configuração própria",
      description: "Inicia com o padrão do sistema e permite substituir parâmetros com evidência do cliente.",
      region: "Personalizado",
      level: "Variável",
      source: "Cliente",
    },
  ],
  products: [
    {
      id: "TOPOGRAPHY_E0",
      name: "Topografia e curvas de nivel",
      description: "MDT, declividade, relevo sombreado, curvas de nivel e densidade quando houver LAS/LAZ.",
      level: "E0",
      requires: ["FIELD_BOUNDARY", "ELEVATION_SOURCE"],
      available: true,
    },
    {
      id: "SULCATION_E0",
      name: "Cenários iniciais de sulcação",
      description: "Famílias axial, contorno e híbrida, com métricas topográficas e operacionais de triagem.",
      level: "E0",
      requires: ["PROJECT_SCOPE", "TERRAIN_SOURCE"],
      available: true,
    },
    {
      id: "CF0_CONTINUOUS",
      name: "Família curva contínua",
      description: "Campo axial local, suavização, raio mínimo e diagnóstico de continuidade das linhas.",
      level: "E0",
      requires: ["PROJECT_SCOPE", "TERRAIN_SOURCE", "E0"],
      available: true,
    },
    {
      id: "C1_EMBEDDED_SCREENING",
      name: "Estudo inicial de curva embutida",
      description: "Alternativas conceituais de alinhamento e faixas de trabalho, ainda sem dimensionamento hidráulico.",
      level: "E0",
      requires: ["PROJECT_SCOPE", "TERRAIN_SOURCE", "CF0"],
      available: true,
    },
    {
      id: "PCX1_RUNOFF_SCREENING",
      name: "Chuva que vira escoamento",
      description: "Mostra quanto da chuva informada pode escoar sobre o solo ao longo do evento.",
      level: "E0",
      requires: ["AREA_CONTRIBUINTE", "HIETOGRAMA", "CURVE_NUMBER_COM_FONTE"],
      available: true,
    },
    {
      id: "PCX2_HYDROGRAPH_SCREENING",
      name: "Hidrograma preliminar",
      description: "Estima como a vazao cresce e diminui ao longo do evento, com pico e tempo ate o pico.",
      level: "E0",
      requires: ["CHUVA_QUE_VIRA_ESCOAMENTO", "TEMPO_DE_RESPOSTA_DA_AREA"],
      available: true,
    },
    {
      id: "PCX3_REACH_ROUTING_SCREENING",
      name: "Propagacao preliminar na rede",
      description: "Mostra quando e com que vazao a onda chega a cada trecho e a cada saida final.",
      level: "E0",
      requires: ["HIDROGRAMA_PRELIMINAR", "REDE_DE_ESCOAMENTO"],
      available: true,
    },
    {
      id: "PCX4_SECTION_CAPACITY_SCREENING",
      name: "Verificacao preliminar de capacidade",
      description: "Compara a vazao maxima com a profundidade disponivel em cada trecho declarado.",
      level: "E0",
      requires: ["PROPAGACAO_NA_REDE", "SECOES_DOS_TRECHOS"],
      available: true,
    },
    {
      id: "PCX5_WATER_SURFACE_PROFILE_SCREENING",
      name: "Perfil preliminar da lamina",
      description: "Mostra como a profundidade pode variar ao longo de cada trecho em regime subcritico.",
      level: "E0",
      requires: ["VERIFICACAO_DE_CAPACIDADE", "COMPRIMENTOS_DOS_TRECHOS", "LAMINAS_A_JUSANTE"],
      available: true,
    },
    {
      id: "C1_DIMENSIONED",
      name: "Curva embutida dimensionada",
      description: "TI/TD, seção, superfície proposta, volumes e verificação hidráulica conservacionista.",
      level: "E2",
      requires: ["SOIL_HYDROLOGY", "DESIGN_RAIN", "RECEIVERS"],
      available: false,
    },
    {
      id: "C2_BROAD_BASE",
      name: "Base larga/passante",
      description: "Sistema transitável dimensionado para frota, conservação e condução segura de excedentes.",
      level: "E2",
      requires: ["SOIL_HYDROLOGY", "DESIGN_RAIN", "RECEIVERS", "FLEET_CONFIGURATION"],
      available: false,
    },
    {
      id: "C3_ESD",
      name: "ESD e canal escoadouro",
      description: "Organização por estruturas de drenagem superficial e receptores verificados por zona.",
      level: "E2",
      requires: ["SOIL_HYDROLOGY", "DESIGN_RAIN", "CATCHMENTS", "RECEIVERS"],
      available: false,
    },
    {
      id: "MULTIFIELD",
      name: "Continuidade entre talhões",
      description: "Compara eixo comum, guia contínua e trabalho contínuo somente através de portais autorizados.",
      level: "E1",
      requires: ["FIELD_BOUNDARIES", "INTERFERENCES", "PORTALS"],
      available: false,
    },
    {
      id: "POA_STATIC",
      name: "POA e logística",
      description: "Localização capacitada, rotas dirigíveis, janelas de transbordo e distância até caminhões.",
      level: "E1",
      requires: ["ROADS", "FLEET_CONFIGURATION", "YIELD", "LOGISTICS"],
      available: false,
    },
    {
      id: "COMPLETE_DOSSIER",
      name: "Mapas, comparativo e dossiê",
      description: "PDF pesquisável, mapas, métricas, gates, linhagem, checksums e pacotes técnicos.",
      level: "E0–E3",
      requires: [],
      available: true,
    },
  ],
  parameterGroups: [
    {
      id: "planting",
      name: "Plantio e sulcação",
      description: "Geometria básica das linhas e limites do implemento",
      parameters: [
        { id: "topography.resolution_m", name: "Resolução do MDT", type: "number", unit: "m", min: 0.2, max: 20, step: 0.1, default: 1, source: "Sistema" },
        { id: "topography.contour_interval_m", name: "Equidistância das curvas", type: "number", unit: "m", min: 0.1, max: 20, step: 0.1, default: 1, source: "Sistema" },
        { id: "topography.field_id_column", name: "Campo identificador do talhão", type: "text", unit: null, default: "", source: "Cliente" },
        { id: "rows.spacing_m", name: "Espaçamento entre linhas", type: "number", unit: "m", min: 1.2, max: 2.0, step: 0.05, default: 1.5, source: "Sistema" },
        { id: "fleet.minimum_turn_radius_m", name: "Raio mínimo de trabalho", type: "number", unit: "m", min: 4, max: 20, step: 0.5, default: 6, source: "Frota" },
        { id: "fleet.headland_width_m", name: "Largura de manobra", type: "number", unit: "m", min: 5, max: 100, step: 0.5, default: 18, source: "Frota" },
        { id: "operation.minimum_shot_length_m", name: "Tiro mínimo preferido", type: "number", unit: "m", min: 5, max: 5000, step: 5, default: 50, source: "Cliente" },
        { id: "terrain.smoothing_radius_m", name: "Raio de suavização do terreno", type: "number", unit: "m", min: 1, max: 20, step: 1, default: 5, source: "Sistema" },
      ],
    },
    {
      id: "operation",
      name: "Colheita e performance",
      description: "Velocidade, manobra e limites operacionais",
      parameters: [
        { id: "fleet.field_speed_kmh", name: "Velocidade nominal em campo", type: "number", unit: "km/h", min: 2, max: 12, step: 0.1, default: 5, source: "Sistema" },
        { id: "operation.maneuver_time_s", name: "Tempo de manobra", type: "number", unit: "s", min: 10, max: 180, step: 0.5, default: 38.5, source: "Sistema" },
        { id: "operation.maximum_lateral_slope_pct", name: "Declividade lateral máxima", type: "number", unit: "%", min: 2, max: 25, step: 0.5, default: 12, source: "Cliente" },
      ],
    },
    {
      id: "objectives",
      name: "Objetivos da otimização",
      description: "Pesos relativos; o total deve ser 100%",
      parameters: [
        { id: "objectives.soil_conservation_weight", name: "Conservação do solo", type: "number", unit: "%", min: 0, max: 100, step: 5, default: 45, source: "Sistema" },
        { id: "objectives.harvestability_weight", name: "Colheitabilidade", type: "number", unit: "%", min: 0, max: 100, step: 5, default: 35, source: "Sistema" },
        { id: "objectives.performance_weight", name: "Performance operacional", type: "number", unit: "%", min: 0, max: 100, step: 5, default: 20, source: "Sistema" },
      ],
    },
    {
      id: "constraints",
      name: "Interferências e continuidade",
      description: "Declarações que alteram quebra, travessia e conexão",
      parameters: [
        { id: "power_line.state", name: "Rede elétrica aérea", type: "select", options: [{ value: "PENDING", label: "Não revisada" }, { value: "DECLARED_NONE", label: "Declarada inexistente" }, { value: "PROVIDED", label: "Shape enviado" }], default: "PENDING", source: "Cliente" },
        { id: "connections.cross_field_enabled", name: "Avaliar conexão entre talhões", type: "boolean", default: true, source: "Cliente" },
        { id: "connections.cross_property_enabled", name: "Avaliar conexão entre propriedades", type: "boolean", default: false, source: "Cliente" },
      ],
    },
    {
      id: "hydrology-screening",
      name: "Chuva e resposta do terreno",
      description: "Configure o evento e a capacidade do solo de reter agua",
      parameters: [
        { id: "hydrology.enabled", name: "Calcular a parcela que escoa", type: "boolean", default: false, source: "Cliente" },
        { id: "hydrology.catchment_area_ha", name: "Area contribuinte", type: "number", unit: "ha", min: 0.01, max: 1000000, step: 0.01, default: 1, source: "Cliente" },
        { id: "hydrology.curve_number", name: "Resposta do solo e da cobertura (CN)", type: "number", unit: null, min: 1, max: 100, step: 0.1, default: 75, source: "Cliente" },
        { id: "hydrology.initial_abstraction_ratio", name: "Retencao inicial da chuva (avancado)", type: "number", unit: null, min: 0, max: 0.3, step: 0.01, default: 0.2, source: "Cliente" },
        { id: "hydrology.interval_minutes", name: "Duracao de cada intervalo", type: "number", unit: "min", min: 1, max: 10080, step: 1, default: 10, source: "Cliente" },
        { id: "hydrology.rainfall_series_mm", name: "Chuva por intervalo", type: "text", unit: "mm", default: "", source: "Cliente" },
        { id: "hydrology.parameter_source_id", name: "Fonte dos parametros e da chuva", type: "text", unit: null, default: "", source: "Cliente" },
        { id: "hydrology.evidence_state", name: "Estado da evidencia", type: "select", options: [{ value: "E0_ASSUMPTION", label: "Hipotese E0" }, { value: "PROJECT_EVIDENCE", label: "Evidencia do projeto" }], default: "E0_ASSUMPTION", source: "Cliente" },
        { id: "hydrology.hydrograph_enabled", name: "Calcular vazao ao longo do tempo", type: "boolean", default: false, source: "Cliente" },
        { id: "hydrology.catchment_lag_minutes", name: "Tempo de resposta da area", type: "number", unit: "min", min: 1, max: 10080, step: 1, default: 30, source: "Cliente" },
        { id: "hydrology.hydrograph_step_minutes", name: "Intervalo do grafico", type: "number", unit: "min", min: 0.1, max: 1440, step: 0.1, default: 1, source: "Sistema" },
        { id: "hydrology.triangle_base_to_peak_ratio", name: "Duracao relativa da resposta", type: "number", unit: null, min: 1.01, max: 20, step: 0.01, default: 2.67, source: "Sistema" },
        { id: "hydrology.routing_enabled", name: "Propagar a vazao pela rede", type: "boolean", default: false, source: "Cliente" },
        { id: "hydrology.routing_source_node_id", name: "No onde a agua entra na rede", type: "text", unit: null, default: "ENTRADA", source: "Cliente" },
        { id: "hydrology.routing_reaches_json", name: "Trechos e tempos de viagem", type: "json", unit: null, default: "[]", source: "Cliente" },
        { id: "hydrology.capacity_enabled", name: "Verificar capacidade dos trechos", type: "boolean", default: false, source: "Cliente" },
        { id: "hydrology.stability_reference_model", name: "Referencia para limite de velocidade", type: "select", options: [{ value: "NO_ASSUMED_LIMIT", label: "Sem limite presumido" }, { value: "NRCS_GRASS_SPARSE_0P9_REFERENCE", label: "Vegetacao esparsa - 0,9 m/s" }, { value: "NRCS_GRASS_SEEDED_0P9_REFERENCE", label: "Vegetacao semeada - 0,9 m/s" }, { value: "NRCS_GOOD_SOD_1P5_REFERENCE", label: "Cobertura densa estabelecida - 1,5 m/s" }, { value: "CUSTOM_PROJECT_LIMITS", label: "Limites proprios por trecho" }], default: "NO_ASSUMED_LIMIT", source: "Sistema" },
        { id: "hydrology.reach_sections_json", name: "Secoes e condicoes dos trechos", type: "json", unit: null, default: "[]", source: "Cliente" },
        { id: "hydrology.profile_enabled", name: "Calcular perfil da lamina nos trechos", type: "boolean", default: false, source: "Cliente" },
        { id: "hydrology.profile_step_count", name: "Divisoes de calculo por trecho", type: "number", unit: null, min: 2, max: 1000, step: 1, default: 20, source: "Sistema" },
      ],
    },
  ],
};

const ROLE_TO_KIND = {
  FIELD_BOUNDARY: "FIELD_BOUNDARIES",
  POINT_CLOUD: "POINT_CLOUD",
  ORTHOMOSAIC: "ORTHOMOSAIC",
  DTM_DEM: "DEM",
  POWER_NETWORK: "POWER_LINE",
  ROADS_CARRIERS: "ROADS",
  OBSTACLES: "OBSTACLES",
  SOIL_MAP: "SOIL",
  RAINFALL: "RAINFALL",
  HYDROGRAPHY_RECEIVERS: "HYDROGRAPHY",
  FLEET_CONFIGURATION: "FLEET",
  TECHNICAL_EVIDENCE: "OTHER",
};

const KIND_TO_ROLE = Object.fromEntries(Object.entries(ROLE_TO_KIND).map(([role, kind]) => [kind, role]));
KIND_TO_ROLE.INTERFERENCES = "OBSTACLES";

function liveProject(project) {
  return {
    ...project,
    code: project.id.slice(-10).toUpperCase(),
    municipality: null,
    state: null,
    area_ha: project.area_ha || 0,
    asset_count: project.asset_count || 0,
    run_count: project.run_count || 0,
    progress: project.status === "DRAFT" ? 20 : 60,
    current_step: "DATA",
  };
}

function liveAsset(asset) {
  return {
    ...asset,
    name: asset.original_filename,
    kind: ROLE_TO_KIND[asset.role] || asset.role,
    size: asset.size_bytes,
    checksum: asset.sha256,
    crs: "A confirmar no QA espacial",
    status: asset.spatial_qa_status === "PASSED_E0_ENGINE" ? "VERIFIED" : "PENDING",
  };
}

function liveReadiness(readiness) {
  const required = readiness.evidence.filter((item) => item.required);
  const availableStates = new Set(["AVAILABLE_PENDING_SPATIAL_QA", "NOT_APPLICABLE", "VALIDATED"]);
  const available = readiness.evidence.filter((item) => availableStates.has(item.status)).length;
  const requiredAvailable = required.filter((item) => availableStates.has(item.status)).length;
  const packageNames = {
    FIELD_BOUNDARY: "Polígonos dos talhões",
    ELEVATION_SOURCE: "Fonte altimétrica",
    ORTHOMOSAIC_CONTEXT: "Ortomosaico de contexto",
    POWER_NETWORK: "Rede elétrica aérea",
  };
  return {
    ...readiness,
    score: required.length ? Math.round((requiredAvailable / required.length) * 100) : 0,
    maximum_delivery_level: readiness.summary.minimum_inputs_present ? "E0_TRIAGEM" : "NOT_READY",
    evidence_summary: {
      available,
      missing: readiness.evidence.filter((item) => item.status.startsWith("MISSING")).length,
      partial: readiness.evidence.filter((item) => item.status === "PARTIAL").length,
    },
    packages: readiness.evidence.map((item) => ({
      id: item.id === "POWER_NETWORK" ? "POWER_INFRASTRUCTURE" : item.id,
      name: packageNames[item.id] || item.id,
      state: item.status,
      note: item.required ? "Obrigatório para o escopo atual" : "Opcional neste estágio",
    })),
    scenarios: readiness.products.map((item) => ({
      id: item.product_id,
      state: item.client_data_generation_available ? "READY" : "BLOCKED",
      delivery: "E0_TRIAGEM",
      blockers: item.blockers,
    })),
    parameter_summary: {
      resolved: readiness.summary.minimum_inputs_present ? 12 : 8,
      pending: readiness.blockers.length,
    },
  };
}

function liveConfiguration(configuration) {
  return {
    preset_id: configuration.system_preset_id,
    parameters: {
      "topography.resolution_m": configuration.topography.resolution_m,
      "topography.contour_interval_m": configuration.topography.contour_interval_m,
      "topography.field_id_column": configuration.topography.field_id_column || "",
      "rows.spacing_m": configuration.sulcation.row_spacing_m,
      "fleet.minimum_turn_radius_m": configuration.sulcation.min_turn_radius_m,
      "fleet.headland_width_m": configuration.sulcation.headland_width_m,
      "operation.minimum_shot_length_m": configuration.sulcation.min_shot_length_m,
      "terrain.smoothing_radius_m": configuration.sulcation.terrain_smoothing_radius_m,
      "fleet.field_speed_kmh": configuration.sulcation.nominal_speed_kmh,
      "operation.maneuver_time_s": configuration.sulcation.maneuver_time_s,
      "operation.maximum_lateral_slope_pct": configuration.sulcation.max_cross_slope_pct,
      "objectives.soil_conservation_weight": configuration.objectives.soil_conservation_weight,
      "objectives.harvestability_weight": configuration.objectives.harvestability_weight,
      "objectives.performance_weight": configuration.objectives.performance_weight,
      "power_line.state": configuration.constraints.power_network_state === "UNKNOWN" ? "PENDING" : configuration.constraints.power_network_state === "UPLOADED" ? "PROVIDED" : "DECLARED_NONE",
      "connections.cross_field_enabled": configuration.sulcation.allow_cross_field,
      "connections.cross_property_enabled": configuration.sulcation.allow_cross_property,
      "hydrology.enabled": configuration.hydrology_screening?.enabled || false,
      "hydrology.catchment_area_ha": configuration.hydrology_screening?.catchment_area_ha || 1,
      "hydrology.curve_number": configuration.hydrology_screening?.curve_number || 75,
      "hydrology.initial_abstraction_ratio": configuration.hydrology_screening?.initial_abstraction_ratio ?? 0.2,
      "hydrology.interval_minutes": configuration.hydrology_screening?.rainfall_intervals?.[0]?.duration_s ? configuration.hydrology_screening.rainfall_intervals[0].duration_s / 60 : 10,
      "hydrology.rainfall_series_mm": (configuration.hydrology_screening?.rainfall_intervals || []).map((item) => item.rainfall_mm).join(", "),
      "hydrology.parameter_source_id": configuration.hydrology_screening?.parameter_source_id || "",
      "hydrology.evidence_state": configuration.hydrology_screening?.parameter_evidence_state || "E0_ASSUMPTION",
      "hydrology.hydrograph_enabled": configuration.hydrology_screening?.hydrograph_enabled || false,
      "hydrology.catchment_lag_minutes": configuration.hydrology_screening?.catchment_lag_minutes || 30,
      "hydrology.hydrograph_step_minutes": configuration.hydrology_screening?.hydrograph_step_minutes || 1,
      "hydrology.triangle_base_to_peak_ratio": configuration.hydrology_screening?.triangle_base_to_peak_ratio || 2.67,
      "hydrology.routing_enabled": configuration.hydrology_screening?.routing_enabled || false,
      "hydrology.routing_source_node_id": configuration.hydrology_screening?.routing_source_node_id || "ENTRADA",
      "hydrology.routing_reaches_json": JSON.stringify(configuration.hydrology_screening?.routing_reaches || [], null, 2),
      "hydrology.capacity_enabled": configuration.hydrology_screening?.capacity_enabled || false,
      "hydrology.stability_reference_model": (() => {
        const sources = [...new Set((configuration.hydrology_screening?.reach_sections || []).map((item) => item.stability_limit_source_id).filter(Boolean))];
        return sources.length === 1 && ["NRCS_GRASS_SPARSE_0P9_REFERENCE", "NRCS_GRASS_SEEDED_0P9_REFERENCE", "NRCS_GOOD_SOD_1P5_REFERENCE"].includes(sources[0])
          ? sources[0]
          : sources.length ? "CUSTOM_PROJECT_LIMITS" : "NO_ASSUMED_LIMIT";
      })(),
      "hydrology.reach_sections_json": JSON.stringify(configuration.hydrology_screening?.reach_sections || [], null, 2),
      "hydrology.profile_enabled": configuration.hydrology_screening?.profile_enabled || false,
      "hydrology.profile_step_count": configuration.hydrology_screening?.profile_step_count || 20,
    },
    selected_product_ids: configuration.selected_product_ids,
    _backend: configuration,
  };
}

function applyLiveConfiguration(current, payload) {
  const values = payload.parameters || {};
  current.system_preset_id = payload.preset_id || current.system_preset_id;
  current.topography.resolution_m = Number(values["topography.resolution_m"] ?? current.topography.resolution_m);
  current.topography.contour_interval_m = Number(values["topography.contour_interval_m"] ?? current.topography.contour_interval_m);
  current.topography.field_id_column = String(values["topography.field_id_column"] || "").trim() || null;
  current.sulcation.row_spacing_m = Number(values["rows.spacing_m"] ?? current.sulcation.row_spacing_m);
  current.sulcation.min_turn_radius_m = Number(values["fleet.minimum_turn_radius_m"] ?? current.sulcation.min_turn_radius_m);
  current.sulcation.headland_width_m = Number(values["fleet.headland_width_m"] ?? current.sulcation.headland_width_m);
  current.sulcation.min_shot_length_m = Number(values["operation.minimum_shot_length_m"] ?? current.sulcation.min_shot_length_m);
  current.sulcation.terrain_smoothing_radius_m = Number(values["terrain.smoothing_radius_m"] ?? current.sulcation.terrain_smoothing_radius_m);
  current.sulcation.nominal_speed_kmh = Number(values["fleet.field_speed_kmh"] ?? current.sulcation.nominal_speed_kmh);
  current.sulcation.maneuver_time_s = Number(values["operation.maneuver_time_s"] ?? current.sulcation.maneuver_time_s);
  current.sulcation.max_cross_slope_pct = Number(values["operation.maximum_lateral_slope_pct"] ?? current.sulcation.max_cross_slope_pct);
  current.sulcation.allow_cross_field = Boolean(values["connections.cross_field_enabled"]);
  current.sulcation.allow_cross_property = Boolean(values["connections.cross_property_enabled"]);
  current.objectives.soil_conservation_weight = Number(values["objectives.soil_conservation_weight"] ?? current.objectives.soil_conservation_weight);
  current.objectives.harvestability_weight = Number(values["objectives.harvestability_weight"] ?? current.objectives.harvestability_weight);
  current.objectives.performance_weight = Number(values["objectives.performance_weight"] ?? current.objectives.performance_weight);
  const power = values["power_line.state"];
  current.constraints.power_network_state = power === "DECLARED_NONE" ? "DECLARED_NONE" : power === "PROVIDED" ? "UPLOADED" : "UNKNOWN";
  const hydrologyEnabled = Boolean(values["hydrology.enabled"]);
  const intervalSeconds = Number(values["hydrology.interval_minutes"] || 10) * 60;
  const rainfallValues = String(values["hydrology.rainfall_series_mm"] || "")
    .split(/[;,\s]+/)
    .filter(Boolean)
    .map(Number);
  let routingReaches = [];
  if (values["hydrology.routing_enabled"]) {
    try {
      routingReaches = JSON.parse(String(values["hydrology.routing_reaches_json"] || "[]"));
    } catch {
      throw new Error("A lista de trechos da rede nao e um JSON valido.");
    }
    if (!Array.isArray(routingReaches)) throw new Error("A rede deve ser uma lista de trechos.");
  }
  let reachSections = [];
  if (values["hydrology.capacity_enabled"]) {
    try {
      reachSections = JSON.parse(String(values["hydrology.reach_sections_json"] || "[]"));
    } catch {
      throw new Error("A lista de secoes dos trechos nao e um JSON valido.");
    }
    if (!Array.isArray(reachSections)) throw new Error("As secoes devem formar uma lista.");
    const referenceModels = {
      NRCS_GRASS_SPARSE_0P9_REFERENCE: 0.9,
      NRCS_GRASS_SEEDED_0P9_REFERENCE: 0.9,
      NRCS_GOOD_SOD_1P5_REFERENCE: 1.5,
    };
    const selectedReference = values["hydrology.stability_reference_model"];
    if (referenceModels[selectedReference]) {
      reachSections = reachSections.map((section) => section.maximum_admissible_velocity_m_s == null
        ? { ...section, maximum_admissible_velocity_m_s: referenceModels[selectedReference], stability_limit_source_id: selectedReference, stability_limit_evidence_state: "SYSTEM_REFERENCE" }
        : section);
    }
  }
  current.hydrology_screening = {
    enabled: hydrologyEnabled,
    method: "NRCS_CURVE_NUMBER_EVENT_SCREENING",
    catchment_area_ha: hydrologyEnabled ? Number(values["hydrology.catchment_area_ha"]) : null,
    curve_number: hydrologyEnabled ? Number(values["hydrology.curve_number"]) : null,
    initial_abstraction_ratio: Number(values["hydrology.initial_abstraction_ratio"] ?? 0.2),
    parameter_evidence_state: values["hydrology.evidence_state"] || "E0_ASSUMPTION",
    parameter_source_id: hydrologyEnabled ? String(values["hydrology.parameter_source_id"] || "").trim() : null,
    rainfall_intervals: hydrologyEnabled && rainfallValues.every(Number.isFinite)
      ? rainfallValues.map((rainfall_mm) => ({ duration_s: intervalSeconds, rainfall_mm }))
      : [],
    hydrograph_enabled: Boolean(values["hydrology.hydrograph_enabled"]),
    catchment_lag_minutes: Boolean(values["hydrology.hydrograph_enabled"])
      ? Number(values["hydrology.catchment_lag_minutes"])
      : null,
    hydrograph_step_minutes: Number(values["hydrology.hydrograph_step_minutes"] || 1),
    triangle_base_to_peak_ratio: Number(values["hydrology.triangle_base_to_peak_ratio"] || 2.67),
    routing_enabled: Boolean(values["hydrology.routing_enabled"]),
    routing_source_node_id: values["hydrology.routing_enabled"] ? String(values["hydrology.routing_source_node_id"] || "").trim() : null,
    routing_reaches: routingReaches,
    capacity_enabled: Boolean(values["hydrology.capacity_enabled"]),
    reach_sections: reachSections,
    profile_enabled: Boolean(values["hydrology.profile_enabled"]),
    profile_step_count: Number(values["hydrology.profile_step_count"] || 20),
  };
  return current;
}

function liveRun(run, logs = [], artifactCount = 0) {
  const started = run.started_at ? new Date(run.started_at).getTime() : null;
  const finished = run.finished_at ? new Date(run.finished_at).getTime() : Date.now();
  const fallbackStages = {
    QUEUED: "Na fila",
    RUNNING: "Processando",
    VERIFYING: "Verificando produtos",
    SUCCEEDED: "Produtos publicados",
    FAILED: "Processamento interrompido por falha",
    BLOCKED: "Processamento bloqueado por gate",
    CANCELLED: "Execução cancelada",
  };
  return {
    ...run,
    name: run.engine_id === "project_topography"
      ? "Topografia E0 do projeto"
      : run.engine_id === "project_pipeline_e0"
        ? "Cenários E0 do projeto"
        : run.engine_id === "project_hydrology_screening"
          ? "Simulacao do escoamento da chuva"
        : run.engine_id === "validate_uploads"
          ? "Validação dos dados"
          : "Rodada de produtos",
    stage: run.stage || fallbackStages[run.status] || "Estado não informado",
    duration_seconds: started ? Math.max(0, Math.round((finished - started) / 1000)) : 0,
    artifact_count: run.result_summary?.artifact_count ?? artifactCount,
    logs: logs.map((item) => ({ timestamp: item.at || item.timestamp, level: item.level, message: item.message })),
  };
}

function liveArtifact(artifact) {
  const extension = (artifact.filename.split(".").pop() || "FILE").toUpperCase();
  return {
    ...artifact,
    name: artifact.filename,
    format: extension,
    kind: artifact.media_type?.startsWith("image/") ? "MAP" : extension,
    size: artifact.size_bytes,
    checksum: artifact.sha256,
    status: "PUBLISHED",
  };
}

class ApiClient {
  constructor(baseUrl = window.__TERRAFLUX_API_BASE__ || DEFAULT_API_BASE) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.mode = "checking";
    this.mock = new MockGateway();
    this.listeners = new Set();
  }

  onModeChange(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  setMode(mode, reason = null) {
    this.mode = mode;
    for (const listener of this.listeners) listener({ mode, reason });
  }

  async request(path, options = {}) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), options.timeout || 12000);
    const headers = new Headers(options.headers || {});
    const isFormData = options.body instanceof FormData;
    if (options.body && !isFormData && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    headers.set("Accept", "application/json");

    try {
      const response = await fetch(`${this.baseUrl}${path}`, {
        ...options,
        headers,
        body: options.body && !isFormData && typeof options.body !== "string" ? JSON.stringify(options.body) : options.body,
        credentials: "same-origin",
        signal: controller.signal,
      });
      const contentType = response.headers.get("content-type") || "";
      const data = contentType.includes("application/json") ? await response.json() : await response.text();
      if (!response.ok) {
        const detail = data?.detail;
        const message = detail?.message || (typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : data?.message) || `A API respondeu com status ${response.status}.`;
        throw new ApiError(message, response.status, data);
      }
      return data;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (error.name === "AbortError") throw new ApiError("A API excedeu o tempo de resposta.", 0);
      throw new ApiError("Não foi possível conectar à API TerraFlux.", 0, error);
    } finally {
      window.clearTimeout(timeout);
    }
  }

  unwrapList(payload, keys = []) {
    if (Array.isArray(payload)) return payload;
    for (const key of keys) if (Array.isArray(payload?.[key])) return payload[key];
    if (Array.isArray(payload?.items)) return payload.items;
    return [];
  }

  async initialize() {
    try {
      try {
        await this.request("/health", { timeout: 2500 });
      } catch (healthError) {
        if (healthError.status && healthError.status !== 404) throw healthError;
        await this.request("/projects", { timeout: 3500 });
      }
      this.setMode("live");
    } catch (error) {
      if (error.status === 401 || error.status === 403) {
        this.setMode("live", error);
        throw error;
      }
      this.setMode("mock", error);
    }
    return this.mode;
  }

  async listProjects() {
    if (this.mode === "mock") return this.mock.listProjects();
    return this.unwrapList(await this.request("/projects"), ["projects"]).map(liveProject);
  }

  async getProject(projectId) {
    if (this.mode === "mock") return this.mock.getProject(projectId);
    return liveProject(await this.request(`/projects/${encodeURIComponent(projectId)}`));
  }

  async createProject(payload) {
    if (this.mode === "mock") return this.mock.createProject(payload);
    const descriptionParts = [payload.description, payload.municipality && `${payload.municipality}${payload.state ? `/${payload.state}` : ""}`].filter(Boolean);
    const created = await this.request("/projects", {
      method: "POST",
      body: {
        name: payload.name,
        farm_name: payload.farm_name || null,
        description: descriptionParts.join(" | ") || null,
        crs: payload.crs,
      },
    });
    return liveProject(created);
  }

  async updateProject(projectId, payload) {
    if (this.mode === "mock") return this.mock.updateProject(projectId, payload);
    const allowed = Object.fromEntries(Object.entries(payload).filter(([key]) => ["name", "farm_name", "client_name", "description", "crs"].includes(key)));
    return liveProject(await this.request(`/projects/${encodeURIComponent(projectId)}`, { method: "PATCH", body: allowed }));
  }

  async listAssets(projectId) {
    if (this.mode === "mock") return this.mock.listAssets(projectId);
    const payload = await this.request(`/projects/${encodeURIComponent(projectId)}/assets`);
    return this.unwrapList(payload, ["assets"]).map(liveAsset);
  }

  async uploadAsset(projectId, file, metadata = {}, onProgress = null) {
    if (this.mode === "mock") return this.mock.uploadAsset(projectId, file, metadata, onProgress);
    const formData = new FormData();
    formData.append("file", file, file.name);
    formData.append("role", KIND_TO_ROLE[metadata.kind] || metadata.kind || "TECHNICAL_EVIDENCE");
    onProgress?.(5);
    const response = await this.request(`/projects/${encodeURIComponent(projectId)}/assets`, {
      method: "POST",
      body: formData,
      timeout: 30 * 60 * 1000,
    });
    onProgress?.(100);
    return liveAsset(response);
  }

  async deleteAsset(projectId, assetId) {
    if (this.mode === "mock") return this.mock.deleteAsset(projectId, assetId);
    return this.request(`/projects/${encodeURIComponent(projectId)}/assets/${encodeURIComponent(assetId)}`, { method: "DELETE" });
  }

  async getReadiness(projectId) {
    if (this.mode === "mock") return this.mock.getReadiness(projectId);
    return liveReadiness(await this.request(`/projects/${encodeURIComponent(projectId)}/readiness`));
  }

  async getConfiguration(projectId) {
    if (this.mode === "mock") return this.mock.getConfiguration(projectId);
    try {
      return liveConfiguration(await this.request(`/projects/${encodeURIComponent(projectId)}/configuration`));
    } catch (error) {
      if (error.status !== 404) throw error;
      return { preset_id: null, parameters: {} };
    }
  }

  async saveConfiguration(projectId, payload) {
    if (this.mode === "mock") return this.mock.saveConfiguration(projectId, payload);
    const current = await this.request(`/projects/${encodeURIComponent(projectId)}/configuration`);
    const saved = await this.request(`/projects/${encodeURIComponent(projectId)}/configuration`, {
      method: "PUT",
      body: applyLiveConfiguration(current, payload),
    });
    return liveConfiguration(saved);
  }

  async getProductSelection(projectId) {
    if (this.mode === "mock") return this.mock.getProductSelection(projectId);
    try {
      const configuration = await this.request(`/projects/${encodeURIComponent(projectId)}/configuration`);
      return { products: configuration.selected_product_ids || [] };
    } catch (error) {
      if (error.status !== 404) throw error;
      return { products: ["TOPOGRAPHY_E0"] };
    }
  }

  async saveProductSelection(projectId, productIds) {
    if (this.mode === "mock") return this.mock.saveProductSelection(projectId, productIds);
    const configuration = await this.request(`/projects/${encodeURIComponent(projectId)}/configuration`);
    configuration.selected_product_ids = productIds;
    await this.request(`/projects/${encodeURIComponent(projectId)}/configuration`, {
      method: "PUT",
      body: configuration,
    });
    return { products: productIds };
  }

  async compileRequest(projectId, payload) {
    if (this.mode === "mock") return this.mock.compileRequest(projectId, payload);
    const compiled = await this.request(`/projects/${encodeURIComponent(projectId)}/requests`, {
      method: "POST",
      body: {
        name: `Rodada ${payload.products.join(" + ")}`,
        product_ids: payload.products,
        delivery_level: "E0_TRIAGEM",
      },
    });
    return { ...compiled, hash: compiled.sha256, status: "AVAILABLE" };
  }

  async getLatestRequest(projectId) {
    if (this.mode === "mock") return this.mock.getLatestRequest(projectId);
    try {
      const payload = await this.request(`/projects/${encodeURIComponent(projectId)}/requests`);
      const latest = this.unwrapList(payload, ["requests"])[0];
      return latest ? { ...latest, hash: latest.sha256, status: "AVAILABLE" } : null;
    } catch (error) {
      if (error.status !== 404) throw error;
      return null;
    }
  }

  async startRun(requestId, payload = {}) {
    if (this.mode === "mock") return this.mock.startRun(requestId, payload);
    const compiled = await this.request(`/requests/${encodeURIComponent(requestId)}`);
    const productIds = compiled.product_ids || [];
    const scenarioProductIds = new Set(["SULCATION_E0", "CF0_CONTINUOUS"]);
    const hasScenarioProduct = productIds.some((productId) => scenarioProductIds.has(productId));
    const isTopographyOnly = productIds.length === 1 && productIds[0] === "TOPOGRAPHY_E0";
    const hydrologyProducts = new Set(["PCX1_RUNOFF_SCREENING", "PCX2_HYDROGRAPH_SCREENING", "PCX3_REACH_ROUTING_SCREENING", "PCX4_SECTION_CAPACITY_SCREENING", "PCX5_WATER_SURFACE_PROFILE_SCREENING"]);
    const isHydrologyOnly = productIds.length > 0 && productIds.every((productId) => hydrologyProducts.has(productId));
    const engineId = hasScenarioProduct ? "project_pipeline_e0" : isTopographyOnly ? "project_topography" : isHydrologyOnly ? "project_hydrology_screening" : null;
    if (!engineId) {
      throw new Error("O pedido não possui uma combinação de produtos executável pelos motores atuais.");
    }
    return liveRun(await this.request(`/requests/${encodeURIComponent(requestId)}/runs`, {
      method: "POST",
      body: { engine_id: engineId, product_ids: productIds },
    }));
  }

  async listRuns(projectId = null) {
    if (this.mode === "mock") return this.mock.listRuns(projectId);
    const suffix = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
    return this.unwrapList(await this.request(`/runs${suffix}`), ["runs"]).map((item) => liveRun(item));
  }

  async getRun(runId) {
    if (this.mode === "mock") return this.mock.getRun(runId);
    const [run, logs, artifacts] = await Promise.all([
      this.request(`/runs/${encodeURIComponent(runId)}`),
      this.request(`/runs/${encodeURIComponent(runId)}/logs`),
      this.request(`/runs/${encodeURIComponent(runId)}/artifacts`),
    ]);
    return liveRun(run, this.unwrapList(logs, ["logs"]), this.unwrapList(artifacts, ["artifacts"]).length);
  }

  async cancelRun(runId) {
    if (this.mode === "mock") return this.mock.cancelRun(runId);
    return this.request(`/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" });
  }

  async listArtifacts(runId) {
    if (this.mode === "mock") return this.mock.listArtifacts(runId);
    return this.unwrapList(await this.request(`/runs/${encodeURIComponent(runId)}/artifacts`), ["artifacts"]).map(liveArtifact);
  }

  async listScenarios(runId) {
    if (this.mode === "mock") return this.mock.listScenarios(runId);
    return this.unwrapList(await this.request(`/runs/${encodeURIComponent(runId)}/scenarios`), ["scenarios"]);
  }

  async selectScenario(runId, scenarioId, payload = {}) {
    if (this.mode === "mock") return this.mock.selectScenario(runId, scenarioId, payload);
    return this.request(
      `/runs/${encodeURIComponent(runId)}/scenarios/${encodeURIComponent(scenarioId)}/selection`,
      {
        method: "POST",
        body: {
          selected_for_review: payload.selected_for_review !== false,
          selection_scope: "E0_REPRESENTATIVE_FOR_TECHNICAL_REVIEW",
          reviewer_note: payload.reviewer_note || null,
        },
      },
    );
  }

  async reviewArtifact(artifactId, payload) {
    if (this.mode === "mock") return this.mock.reviewArtifact(artifactId, payload);
    return this.request(`/artifacts/${encodeURIComponent(artifactId)}/reviews`, { method: "POST", body: payload });
  }

  resetDemo() {
    if (this.mode !== "mock") return;
    this.mock.reset();
  }
}

export const api = new ApiClient();
