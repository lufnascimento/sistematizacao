import { api, ApiError, systemCatalog } from "./api.js";

const main = document.querySelector("#main-content");
const breadcrumb = document.querySelector("#breadcrumb");
const primaryNav = document.querySelector("#primary-nav");
const sidebar = document.querySelector("#sidebar");
const sidebarScrim = document.querySelector("#sidebar-scrim");
const menuToggle = document.querySelector("#menu-toggle");
const refreshButton = document.querySelector("#refresh-button");
const environmentStatus = document.querySelector("#environment-status");
const connectionBanner = document.querySelector("#connection-banner");
const dialog = document.querySelector("#app-dialog");
const dialogTitle = document.querySelector("#dialog-title");
const dialogEyebrow = document.querySelector("#dialog-eyebrow");
const dialogBody = document.querySelector("#dialog-body");
const toastRegion = document.querySelector("#toast-region");

const state = {
  route: null,
  projects: [],
  currentProject: null,
  currentRun: null,
  uploadQueue: [],
  compareIds: [],
  selectedScenarioId: null,
  runPollTimer: null,
};

const steps = [
  { id: "overview", label: "Visão geral", note: "Prontidão" },
  { id: "data", label: "Dados", note: "Upload e QA" },
  { id: "configure", label: "Configurar", note: "Presets e regras" },
  { id: "products", label: "Produtos", note: "Escopo da rodada" },
  { id: "run", label: "Executar", note: "Pedido e processamento" },
  { id: "results", label: "Resultados", note: "Cenários e arquivos" },
];

const statusMap = {
  DRAFT: ["Rascunho", ""],
  IN_PROGRESS: ["Em preparação", "is-info"],
  PROCESSING: ["Processando", "is-info"],
  RESULTS_AVAILABLE: ["Resultados disponíveis", "is-success"],
  SUCCEEDED: ["Concluída", "is-success"],
  READY: ["Pronto", "is-success"],
  AVAILABLE: ["Disponível", "is-success"],
  APPROVED: ["Aprovado", "is-success"],
  VERIFIED: ["Verificado", "is-success"],
  ELIGIBLE_E0: ["Elegível E0", "is-success"],
  PUBLISHED: ["Publicado", "is-success"],
  E0_SCREENING_ONLY_NOT_AUTHORIZED: ["Triagem E0 elegível", "is-info"],
  E0_INFEASIBLE_GEOMETRY_DIAGNOSTIC_ONLY: ["Diagnóstico geométrico", "is-danger"],
  CF0_GEOMETRIC_PASS_HYDRAULIC_UNCONFIRMED: ["CF0 geométrico", "is-warning"],
  CF0_PARTIAL_GEOMETRIC_SCREENING: ["CF0 parcial", "is-warning"],
  CF0_NO_FEASIBLE_FAMILY: ["Sem família CF0 viável", "is-danger"],
  AVAILABLE_SCREENING: ["Triagem", "is-info"],
  LIMITED: ["Limitado", "is-warning"],
  PARTIAL: ["Parcial", "is-warning"],
  VALIDATING: ["Validando", "is-info"],
  QUEUED: ["Na fila", "is-info"],
  RUNNING: ["Executando", "is-info"],
  VERIFYING: ["Verificando", "is-info"],
  CONCEPT_ONLY: ["Conceitual", "is-warning"],
  BLOCKED: ["Bloqueado", "is-danger"],
  FAILED: ["Falhou", "is-danger"],
  CANCELLED: ["Cancelada", "is-danger"],
  MISSING: ["Ausente", "is-danger"],
  PENDING: ["Pendente", "is-warning"],
  NOT_APPLICABLE: ["Não aplicável", ""],
  NOT_READY: ["Não pronto", "is-danger"],
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function safeUrl(value) {
  const url = String(value || "");
  if (/^(https?:\/\/|\/|\.\.\/|\.\/)/i.test(url)) return escapeHtml(url);
  return "#";
}

function icon(name, className = "") {
  return `<i data-lucide="${escapeHtml(name)}"${className ? ` class="${escapeHtml(className)}"` : ""}></i>`;
}

function hydrateIcons(root = document) {
  window.setTimeout(() => window.lucide?.createIcons({ attrs: { "aria-hidden": "true" }, nameAttr: "data-lucide", root }), 0);
}

function badge(status, overrideLabel = null) {
  const normalized = String(status || "PENDING").toUpperCase();
  const [label, className] = statusMap[normalized] || [normalized.replaceAll("_", " "), ""];
  return `<span class="badge ${className}">${escapeHtml(overrideLabel || label)}</span>`;
}

function statusLabel(status) {
  const normalized = String(status || "PENDING").toUpperCase();
  return statusMap[normalized]?.[0] || normalized.replaceAll("_", " ");
}

function scenarioIsGeometryEligible(scenario) {
  if (typeof scenario?.geometry_eligible === "boolean") return scenario.geometry_eligible;
  const status = String(scenario?.status || "").toUpperCase();
  if (["CF0_NO_FEASIBLE_FAMILY", "CF0_PARTIAL_GEOMETRIC_SCREENING", "CONCEPT_ONLY", "BLOCKED"].includes(status)) return false;
  if (status.includes("INFEASIBLE") || status.includes("DIAGNOSTIC_ONLY")) return false;
  return true;
}

function scenarioIsRecommended(scenario) {
  return scenarioIsGeometryEligible(scenario) && scenario?.recommended === true;
}

function scenarioCanRepresentTechnicalReview(scenario) {
  const family = String(scenario?.family || "").toUpperCase();
  const code = String(scenario?.code || "").toUpperCase();
  const status = String(scenario?.status || "").toUpperCase();
  return (family.startsWith("E0") || code.startsWith("E0"))
    && status === "E0_SCREENING_ONLY_NOT_AUTHORIZED"
    && scenarioIsGeometryEligible(scenario)
    && scenario?.guidance_authorized === false;
}

function normalizedSearchText(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("pt-BR");
}

function findPackageArtifact(artifacts) {
  const downloadable = artifacts.filter((item) => item.download_url);
  return downloadable.find((item) => [item.artifact_type, item.product_id].includes("COMPLETE_DOSSIER") && item.format === "PDF")
    || downloadable.find((item) => item.format === "PDF" && normalizedSearchText(item.name).includes("dossie"))
    || downloadable.find((item) => item.format === "PDF")
    || downloadable.find((item) => item.name?.endsWith("manifest.json"));
}

function formatDate(value, withTime = false) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return escapeHtml(value);
  return new Intl.DateTimeFormat("pt-BR", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(date);
}

function formatBytes(bytes) {
  const number = Number(bytes);
  if (!Number.isFinite(number) || number <= 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(number) / Math.log(1024)), units.length - 1);
  return `${(number / 1024 ** index).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
}

function formatDuration(seconds) {
  const total = Number(seconds || 0);
  if (total < 60) return `${total}s`;
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  return hours ? `${hours}h ${minutes}min` : `${minutes}min`;
}

function clamp(number, min = 0, max = 100) {
  return Math.min(max, Math.max(min, Number(number) || 0));
}

function showToast(title, message, type = "success", duration = 4200) {
  const toast = document.createElement("div");
  toast.className = `toast ${type === "error" ? "is-error" : type === "warning" ? "is-warning" : ""}`;
  toast.innerHTML = `${icon(type === "error" ? "circle-x" : type === "warning" ? "triangle-alert" : "circle-check")}<div><strong>${escapeHtml(title)}</strong><span>${escapeHtml(message)}</span></div>`;
  toastRegion.append(toast);
  hydrateIcons(toast);
  window.setTimeout(() => toast.remove(), duration);
}

function showError(error, retry = null) {
  const message = error instanceof ApiError ? error.message : "Ocorreu um erro inesperado.";
  main.innerHTML = `
    <div class="panel error-state">
      <span class="empty-icon">${icon("circle-alert")}</span>
      <h3>Não foi possível carregar esta etapa</h3>
      <p>${escapeHtml(message)}</p>
      ${retry ? `<button class="button is-primary" id="retry-view" type="button">${icon("refresh-cw")} Tentar novamente</button>` : ""}
    </div>`;
  if (retry) document.querySelector("#retry-view")?.addEventListener("click", retry);
  hydrateIcons(main);
}

function setLoading(label = "Carregando dados...") {
  main.innerHTML = `<div class="page-loading"><span class="spinner" aria-hidden="true"></span><p>${escapeHtml(label)}</p></div>`;
}

function setBreadcrumb(parts) {
  breadcrumb.innerHTML = parts
    .map((part, index) => `${index ? icon("chevron-right") : ""}<${index === parts.length - 1 ? "strong" : "span"}>${escapeHtml(part)}</${index === parts.length - 1 ? "strong" : "span"}>`)
    .join("");
  hydrateIcons(breadcrumb);
}

function setActiveNav(routeName) {
  document.querySelectorAll("[data-route]").forEach((button) => button.classList.toggle("is-active", button.dataset.route === routeName));
}

function updateConnectionUi({ mode, reason = null }) {
  if (mode === "live") {
    environmentStatus.innerHTML = `<span class="status-dot is-online"></span><span><strong>API conectada</strong><small>Dados persistentes</small></span>`;
    connectionBanner.hidden = true;
  } else if (mode === "mock") {
    environmentStatus.innerHTML = `<span class="status-dot is-demo"></span><span><strong>Modo demonstrativo</strong><small>API indisponível</small></span>`;
    connectionBanner.hidden = false;
    connectionBanner.title = reason?.message || "A API não respondeu. Alterações ficam apenas neste navegador.";
  } else {
    environmentStatus.innerHTML = `<span class="status-dot is-checking"></span><span><strong>Verificando API</strong><small>Aguarde um instante</small></span>`;
  }
  hydrateIcons(environmentStatus);
}

function openDialog({ title, eyebrow = "TerraFlux", html, onOpen = null }) {
  dialogTitle.textContent = title;
  dialogEyebrow.textContent = eyebrow;
  dialogBody.innerHTML = html;
  dialog.showModal();
  hydrateIcons(dialog);
  onOpen?.(dialogBody);
}

function closeDialog() {
  if (dialog.open) dialog.close();
  dialogBody.innerHTML = "";
}

function navigate(path) {
  const next = path.startsWith("#") ? path : `#${path.startsWith("/") ? path : `/${path}`}`;
  if (window.location.hash === next) route();
  else window.location.hash = next;
}

function parseRoute() {
  const clean = (window.location.hash || "#/projects").replace(/^#\/?/, "");
  const [pathname] = clean.split("?");
  const parts = pathname.split("/").filter(Boolean).map(decodeURIComponent);
  if (parts[0] === "projects" && parts[1]) return { name: "project", projectId: parts[1], step: steps.some((item) => item.id === parts[2]) ? parts[2] : "overview" };
  if (parts[0] === "runs" && parts[1]) return { name: "run-detail", runId: parts[1] };
  if (parts[0] === "runs") return { name: "runs" };
  if (parts[0] === "library") return { name: "library" };
  if (parts[0] === "settings") return { name: "settings" };
  return { name: "projects" };
}

function closeMobileMenu() {
  sidebar.classList.remove("is-open");
  sidebarScrim.hidden = true;
  menuToggle.setAttribute("aria-expanded", "false");
}

function stepper(projectId, activeStep) {
  const activeIndex = steps.findIndex((item) => item.id === activeStep);
  return `<div class="stepper" aria-label="Etapas do projeto">
    ${steps.map((step, index) => `
      <button class="step-button ${index === activeIndex ? "is-active" : ""} ${index < activeIndex ? "is-complete" : ""}" data-step="${step.id}" type="button">
        <span class="step-index">${index < activeIndex ? icon("check") : index + 1}</span>
        <span class="step-copy"><strong>${step.label}</strong><small>${step.note}</small></span>
      </button>`).join("")}
  </div>`;
}

function bindStepper(projectId) {
  document.querySelectorAll("[data-step]").forEach((button) => button.addEventListener("click", () => navigate(`/projects/${projectId}/${button.dataset.step}`)));
}

function projectHead(project, activeStep, actions = "") {
  return `
    <div class="page-head">
      <div>
        <span class="eyebrow">${escapeHtml(project.code || project.id)}</span>
        <h1>${escapeHtml(project.name)}</h1>
        <p>${escapeHtml(project.municipality || "Município não informado")}${project.state ? `, ${escapeHtml(project.state)}` : ""} · ${escapeHtml(project.crs || "CRS a confirmar")}</p>
      </div>
      <div class="page-actions">${actions}</div>
    </div>
    ${stepper(project.id, activeStep)}`;
}

async function renderProjects() {
  setActiveNav("projects");
  setBreadcrumb(["Projetos"]);
  setLoading("Carregando projetos...");
  try {
    state.projects = await api.listProjects();
    main.innerHTML = `
      <div class="page-head">
        <div>
          <span class="eyebrow">Portfólio</span>
          <h1>Projetos de sistematização</h1>
          <p>Organize fontes, parâmetros, cenários, execuções e decisões técnicas por fazenda.</p>
        </div>
        <div class="page-actions">
          <button class="button is-primary" id="new-project" type="button">${icon("plus")} Novo projeto</button>
        </div>
      </div>
      <div class="stats-grid">
        ${statBlock("Projetos ativos", state.projects.filter((item) => !["ARCHIVED", "COMPLETED"].includes(item.status)).length, "folder-kanban", `${state.projects.length} no portfólio`)}
        ${statBlock("Área em análise", `${state.projects.reduce((sum, item) => sum + Number(item.area_ha || 0), 0).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} ha`, "land-plot", "Área declarada nos projetos")}
        ${statBlock("Rodadas", state.projects.reduce((sum, item) => sum + Number(item.run_count || 0), 0), "activity", "Histórico rastreável")}
        ${statBlock("Entrega máxima", "E0", "shield-check", "Sem promoção automática de nível")}
      </div>
      <section class="section">
        <div class="toolbar">
          <label class="search-field">
            ${icon("search")}
            <span class="sr-only">Buscar projetos</span>
            <input id="project-search" type="search" placeholder="Buscar por fazenda, código ou município" autocomplete="off" />
          </label>
          <div class="segmented-control" aria-label="Filtro de projeto">
            <button class="is-active" data-project-filter="all" type="button">Todos</button>
            <button data-project-filter="active" type="button">Ativos</button>
            <button data-project-filter="draft" type="button">Rascunhos</button>
          </div>
        </div>
        <div id="project-list">${renderProjectCards(state.projects)}</div>
      </section>`;

    document.querySelector("#new-project")?.addEventListener("click", showCreateProjectDialog);
    const search = document.querySelector("#project-search");
    let currentFilter = "all";
    const applyFilter = () => {
      const term = search.value.trim().toLocaleLowerCase("pt-BR");
      const filtered = state.projects.filter((project) => {
        const matchesTerm = [project.name, project.code, project.municipality].some((value) => String(value || "").toLocaleLowerCase("pt-BR").includes(term));
        const matchesStatus = currentFilter === "all" || (currentFilter === "draft" ? project.status === "DRAFT" : project.status !== "DRAFT");
        return matchesTerm && matchesStatus;
      });
      document.querySelector("#project-list").innerHTML = renderProjectCards(filtered);
      bindProjectCards();
      hydrateIcons(document.querySelector("#project-list"));
    };
    search?.addEventListener("input", applyFilter);
    document.querySelectorAll("[data-project-filter]").forEach((button) => button.addEventListener("click", () => {
      currentFilter = button.dataset.projectFilter;
      document.querySelectorAll("[data-project-filter]").forEach((item) => item.classList.toggle("is-active", item === button));
      applyFilter();
    }));
    bindProjectCards();
    hydrateIcons(main);
  } catch (error) {
    showError(error, renderProjects);
  }
}

function statBlock(label, value, iconName, note) {
  return `<div class="stat-block"><div class="stat-top"><span>${escapeHtml(label)}</span>${icon(iconName)}</div><strong class="stat-value">${escapeHtml(value)}</strong><small class="stat-note">${escapeHtml(note)}</small></div>`;
}

function renderProjectCards(projects) {
  if (!projects.length) return `<div class="panel empty-state"><span class="empty-icon">${icon("folder-plus")}</span><h3>Nenhum projeto encontrado</h3><p>Ajuste a busca ou crie um projeto para começar.</p><button class="button is-primary" id="empty-new-project" type="button">${icon("plus")} Criar projeto</button></div>`;
  return `<div class="project-grid">${projects.map((project) => `
    <article class="project-card" data-project-id="${escapeHtml(project.id)}" tabindex="0" role="button" aria-label="Abrir ${escapeHtml(project.name)}">
      <div class="card-top">
        <span class="project-code">${escapeHtml(project.code || project.id)}</span>
        ${badge(project.status)}
      </div>
      <h3>${escapeHtml(project.name)}</h3>
      <p>${escapeHtml(project.description || "Projeto sem descrição.")}</p>
      <div class="metadata-row">
        <span>${icon("map-pin")} ${escapeHtml(project.municipality || "Local a informar")}</span>
        <span>${icon("scan-line")} ${Number(project.area_ha || 0).toLocaleString("pt-BR")} ha</span>
        <span>${icon("paperclip")} ${Number(project.asset_count || 0)} arquivos</span>
      </div>
      <div class="project-progress">
        <div class="progress-line"><span>Preparação</span><strong>${clamp(project.progress)}%</strong></div>
        <div class="progress-track"><span style="width:${clamp(project.progress)}%"></span></div>
      </div>
    </article>`).join("")}</div>`;
}

function bindProjectCards() {
  document.querySelector("#empty-new-project")?.addEventListener("click", showCreateProjectDialog);
  document.querySelectorAll("[data-project-id]").forEach((card) => {
    const open = () => navigate(`/projects/${card.dataset.projectId}/overview`);
    card.addEventListener("click", open);
    card.addEventListener("keydown", (event) => {
      if (["Enter", " "].includes(event.key)) {
        event.preventDefault();
        open();
      }
    });
  });
}

function showCreateProjectDialog() {
  openDialog({
    title: "Criar projeto",
    eyebrow: "Novo escopo",
    html: `
      <form id="create-project-form">
        <div class="callout is-info">${icon("info")}<div><strong>Um projeto preserva toda a linhagem.</strong>Arquivos, configurações e rodadas posteriores serão versionados dentro deste escopo.</div></div>
        <div class="form-grid" style="margin-top:16px">
          <div class="form-field is-full"><label for="project-name">Nome do projeto <span class="required-mark">*</span></label><input class="input" id="project-name" name="name" required maxlength="100" placeholder="Ex.: Unidade demonstrativa - renovação 2027" /></div>
          <div class="form-field"><label for="project-code">Código</label><input class="input" id="project-code" name="code" maxlength="30" placeholder="Ex.: SF-2027-01" /></div>
          <div class="form-field"><label for="farm-name">Fazenda / unidade</label><input class="input" id="farm-name" name="farm_name" maxlength="100" /></div>
          <div class="form-field"><label for="municipality">Município <span class="required-mark">*</span></label><input class="input" id="municipality" name="municipality" required maxlength="100" /></div>
          <div class="form-field"><label for="state">UF <span class="required-mark">*</span></label><select class="select" id="state" name="state" required><option value="">Selecione</option>${["SP", "MG", "GO", "PR", "MS", "MT", "AL", "PE", "PB"].map((uf) => `<option>${uf}</option>`).join("")}</select></div>
          <div class="form-field is-full"><label for="crs">Sistema de referência <span class="required-mark">*</span></label><input class="input mono" id="crs" name="crs" required value="EPSG:31983" pattern="EPSG:[0-9]{4,6}" /><p class="field-help">Informe o EPSG projetado em metros correspondente à zona da fazenda.</p></div>
          <div class="form-field is-full"><label for="description">Objetivo</label><textarea class="textarea" id="description" name="description" placeholder="Área, safra, operação e decisão que este projeto deve apoiar."></textarea></div>
        </div>
        <div class="form-actions"><button class="button" type="button" data-close-dialog>Cancelar</button><button class="button is-primary" type="submit">${icon("folder-plus")} Criar e adicionar dados</button></div>
      </form>`,
    onOpen: (root) => {
      root.querySelector("[data-close-dialog]")?.addEventListener("click", closeDialog);
      root.querySelector("#create-project-form")?.addEventListener("submit", async (event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const submit = form.querySelector("[type=submit]");
        submit.disabled = true;
        submit.innerHTML = `<span class="spinner is-small"></span> Criando...`;
        try {
          const payload = Object.fromEntries(new FormData(form).entries());
          const project = await api.createProject(payload);
          closeDialog();
          showToast("Projeto criado", `${project.name} está pronto para receber os dados.`);
          navigate(`/projects/${project.id}/data`);
        } catch (error) {
          submit.disabled = false;
          submit.innerHTML = `${icon("folder-plus")} Criar e adicionar dados`;
          hydrateIcons(submit);
          showToast("Não foi possível criar", error.message, "error");
        }
      });
      root.querySelector("#project-name")?.focus();
    },
  });
}

async function renderProject(projectId, activeStep) {
  setActiveNav("projects");
  setLoading("Abrindo o projeto...");
  try {
    const project = await api.getProject(projectId);
    state.currentProject = project;
    setBreadcrumb(["Projetos", project.name, steps.find((item) => item.id === activeStep)?.label || "Visão geral"]);
    if (activeStep === "data") await renderDataStep(project);
    else if (activeStep === "configure") await renderConfigureStep(project);
    else if (activeStep === "products") await renderProductsStep(project);
    else if (activeStep === "run") await renderRunStep(project);
    else if (activeStep === "results") await renderResultsStep(project);
    else await renderOverviewStep(project);
    bindStepper(project.id);
    hydrateIcons(main);
  } catch (error) {
    showError(error, () => renderProject(projectId, activeStep));
  }
}

async function renderOverviewStep(project) {
  const [readiness, assets, runs] = await Promise.all([api.getReadiness(project.id), api.listAssets(project.id), api.listRuns(project.id)]);
  const lastRun = runs[0];
  const available = Number(readiness.evidence_summary?.available || 0);
  const totalPackages = Object.values(readiness.evidence_summary || {}).reduce((sum, value) => sum + Number(value || 0), 0);
  main.innerHTML = `
    ${projectHead(project, "overview", `<button class="button" id="edit-project" type="button">${icon("pencil")} Editar</button><button class="button is-primary" data-go-step="data" type="button">${icon("arrow-right")} Continuar</button>`)}
    <div class="stats-grid">
      ${statBlock("Prontidão", `${clamp(readiness.score)}%`, "gauge", `${available} de ${totalPackages || 26} pacotes disponíveis`)}
      ${statBlock("Entrega liberada", readiness.maximum_delivery_level || "Não pronta", "shield-check", "Limite calculado pelas evidências")}
      ${statBlock("Dados recebidos", assets.length, "database", `${assets.filter((item) => ["APPROVED", "VERIFIED"].includes(item.status)).length} aprovados`)}
      ${statBlock("Rodadas", runs.length, "activity", lastRun ? `Última em ${formatDate(lastRun.started_at)}` : "Nenhuma execução")}
    </div>
    <div class="content-grid section">
      <div>
        <section class="panel">
          <div class="panel-header"><div><h2>Prontidão dos insumos</h2><p>Disponibilidade não substitui validação de qualidade ou responsabilidade técnica.</p></div><button class="button is-small" data-go-step="data" type="button">Revisar dados</button></div>
          <div class="panel-body"><div class="readiness-list">${(readiness.packages || []).map(readinessItem).join("") || emptyCompact("Nenhum pacote avaliado")}</div></div>
        </section>
        <section class="panel">
          <div class="panel-header"><div><h2>Produtos e gates</h2><p>Estado atual de cada família prevista.</p></div><button class="button is-small" data-go-step="products" type="button">Escolher produtos</button></div>
          <div class="panel-body"><div class="readiness-list">${(readiness.scenarios || []).map((item) => readinessItem({ ...item, note: `Limite de entrega ${item.delivery || "—"}` })).join("") || emptyCompact("Nenhum cenário avaliado")}</div></div>
        </section>
      </div>
      <aside class="content-aside">
        <section class="panel">
          <div class="panel-header"><h3>Próxima ação</h3></div>
          <div class="panel-body">
            ${assets.length < 2 ? `<div class="callout is-warning">${icon("upload-cloud")}<div><strong>Complete os dados mínimos.</strong>Envie os talhões e uma fonte de terreno para liberar E0.</div></div><button class="button is-primary" style="width:100%;margin-top:12px" data-go-step="data" type="button">Adicionar arquivos</button>` : `<div class="callout is-success">${icon("circle-check")}<div><strong>Triagem E0 disponível.</strong>Revise presets e parâmetros antes de compilar uma nova rodada.</div></div><button class="button is-primary" style="width:100%;margin-top:12px" data-go-step="configure" type="button">Configurar rodada</button>`}
          </div>
        </section>
        <section class="panel">
          <div class="panel-header"><h3>Última execução</h3></div>
          <div class="panel-body">${lastRun ? `<ul class="summary-list"><li><span>Rodada</span><strong>${escapeHtml(lastRun.name)}</strong></li><li><span>Estado</span>${badge(lastRun.status)}</li><li><span>Progresso</span><strong>${clamp(lastRun.progress)}%</strong></li><li><span>Artefatos</span><strong>${Number(lastRun.artifact_count || 0)}</strong></li></ul><button class="button" style="width:100%;margin-top:12px" data-run-id="${escapeHtml(lastRun.id)}" type="button">${icon("activity")} Abrir execução</button>` : `<div class="empty-state" style="min-height:180px;padding:10px"><span class="empty-icon">${icon("activity")}</span><h3>Nenhuma rodada</h3><p>Compile e execute o primeiro pedido.</p></div>`}</div>
        </section>
      </aside>
    </div>`;
  bindGoSteps(project.id);
  document.querySelector("[data-run-id]")?.addEventListener("click", (event) => navigate(`/runs/${event.currentTarget.dataset.runId}`));
  document.querySelector("#edit-project")?.addEventListener("click", () => showEditProjectDialog(project));
}

function readinessItem(item) {
  return `<div class="readiness-row"><div class="readiness-copy"><strong>${escapeHtml(item.name || item.id)}</strong><small>${escapeHtml(item.note || item.id)}</small></div>${badge(item.state || item.status)}</div>`;
}

function emptyCompact(label) {
  return `<div class="readiness-row"><div class="readiness-copy"><strong>${escapeHtml(label)}</strong><small>A API ainda não retornou itens.</small></div></div>`;
}

function bindGoSteps(projectId) {
  document.querySelectorAll("[data-go-step]").forEach((button) => button.addEventListener("click", () => navigate(`/projects/${projectId}/${button.dataset.goStep}`)));
}

function showEditProjectDialog(project) {
  openDialog({
    title: "Editar identificação",
    eyebrow: project.code || "Projeto",
    html: `<form id="edit-project-form"><div class="form-grid"><div class="form-field is-full"><label>Nome</label><input class="input" name="name" required value="${escapeHtml(project.name)}" /></div><div class="form-field"><label>Município</label><input class="input" name="municipality" value="${escapeHtml(project.municipality || "")}" /></div><div class="form-field"><label>UF</label><input class="input" name="state" maxlength="2" value="${escapeHtml(project.state || "")}" /></div><div class="form-field is-full"><label>Objetivo</label><textarea class="textarea" name="description">${escapeHtml(project.description || "")}</textarea></div></div><div class="form-actions"><button class="button" data-close-dialog type="button">Cancelar</button><button class="button is-primary" type="submit">${icon("save")} Salvar</button></div></form>`,
    onOpen: (root) => {
      root.querySelector("[data-close-dialog]")?.addEventListener("click", closeDialog);
      root.querySelector("form")?.addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
          await api.updateProject(project.id, Object.fromEntries(new FormData(event.currentTarget).entries()));
          closeDialog();
          showToast("Identificação atualizada", "A alteração foi registrada no projeto.");
          await renderProject(project.id, "overview");
        } catch (error) {
          showToast("Falha ao atualizar", error.message, "error");
        }
      });
    },
  });
}

async function renderDataStep(project) {
  const [assets, readiness] = await Promise.all([api.listAssets(project.id), api.getReadiness(project.id)]);
  main.innerHTML = `
    ${projectHead(project, "data", `<button class="button" id="refresh-assets" type="button">${icon("refresh-cw")} Atualizar QA</button><button class="button is-primary" data-go-step="configure" type="button">Continuar ${icon("arrow-right")}</button>`)}
    <div class="content-grid is-wide-aside">
      <div>
        <section class="panel">
          <div class="panel-header"><div><h2>Adicionar dados do cliente</h2><p>Originais são mantidos imutáveis; a plataforma gera derivados de trabalho.</p></div></div>
          <div class="panel-body">
            <div class="form-grid" style="margin-bottom:13px">
              <div class="form-field"><label for="asset-kind">Tipo do dado</label><select class="select" id="asset-kind">${assetKindOptions()}</select></div>
              <div class="form-field"><label for="asset-crs">CRS informado</label><input class="input" id="asset-crs" value="EPSG:31983" placeholder="Ex.: EPSG:31983" /></div>
            </div>
            <label class="drop-zone" id="drop-zone" for="file-input">
              ${icon("cloud-upload")}
              <strong>Arraste arquivos ou clique para selecionar</strong>
              <p>LAS, LAZ, TIF/COG, GPKG, GeoJSON, KML, DXF, CSV ou SHP compactado em ZIP.</p>
              <input class="sr-only" id="file-input" type="file" multiple accept=".las,.laz,.copc,.tif,.tiff,.gpkg,.geojson,.json,.kml,.kmz,.dxf,.csv,.zip" />
            </label>
            <div id="upload-queue"></div>
          </div>
        </section>
        <section class="panel">
          <div class="panel-header"><div><h2>Arquivos do projeto</h2><p>${assets.length} item(ns) no catálogo de dados.</p></div></div>
          <div class="panel-body" id="asset-list">${renderAssetList(assets)}</div>
        </section>
      </div>
      <aside class="content-aside">
        <section class="panel">
          <div class="panel-header"><h3>Mínimo para E0</h3></div>
          <div class="panel-body"><div class="checklist">
            ${minimumDataCheck("Polígonos de talhões", assets.some((item) => item.kind === "FIELD_BOUNDARIES"), "GPKG, SHP ZIP ou GeoJSON")}
            ${minimumDataCheck("Fonte de elevação", assets.some((item) => ["POINT_CLOUD", "DEM"].includes(item.kind)), "LAZ/LAS ou MDE/MDT; ortomosaico é somente contexto")}
            ${minimumDataCheck("CRS e unidade", assets.some((item) => item.crs && !String(item.crs).includes("confirmar")), "Mesmo referencial em todos os dados")}
            ${minimumDataCheck("Rede elétrica", readiness.packages?.find((item) => item.id === "POWER_INFRASTRUCTURE")?.state === "NOT_APPLICABLE" || assets.some((item) => item.kind === "POWER_LINE"), "Shape ou declaração de inexistência")}
          </div></div>
        </section>
        <section class="panel">
          <div class="panel-header"><h3>Declarações</h3></div>
          <div class="panel-body">
            <div class="toggle-row"><div><strong style="font-size:10px">Não há rede elétrica aérea</strong><p class="field-help">A declaração é auditável e remove apenas este bloqueio.</p></div><label class="toggle"><input id="declare-no-power" type="checkbox" ${readiness.packages?.find((item) => item.id === "POWER_INFRASTRUCTURE")?.state === "NOT_APPLICABLE" ? "checked" : ""}><span></span></label></div>
            <div class="callout is-warning" style="margin-top:13px">${icon("triangle-alert")}<div>Se houver postes ou condutores, envie uma linha pelo centro dos postes. A V1 trata o eixo como barreira absoluta e quebra a operação.</div></div>
          </div>
        </section>
      </aside>
    </div>`;
  bindGoSteps(project.id);
  bindUpload(project.id);
  bindAssetActions(project.id);
  document.querySelector("#refresh-assets")?.addEventListener("click", () => renderProject(project.id, "data"));
  document.querySelector("#declare-no-power")?.addEventListener("change", async (event) => {
    const input = event.currentTarget;
    const declaredNone = input.checked;
    try {
      const config = await api.getConfiguration(project.id);
      config.parameters ||= {};
      config.parameters["power_line.state"] = declaredNone ? "DECLARED_NONE" : "PENDING";
      await api.saveConfiguration(project.id, config);
      showToast("Declaração registrada", declaredNone ? "Rede elétrica marcada como inexistente para este projeto." : "A revisão de rede elétrica voltou a ficar pendente.", "warning");
    } catch (error) {
      input.checked = !declaredNone;
      showToast("Falha ao registrar", error.message, "error");
    }
  });
}

function assetKindOptions() {
  const options = [
    ["POINT_CLOUD", "Nuvem de pontos LAS/LAZ"],
    ["FIELD_BOUNDARIES", "Talhões / perímetro"],
    ["ORTHOMOSAIC", "Ortomosaico"],
    ["DEM", "MDT / MDE"],
    ["ROADS", "Carreadores e vias"],
    ["POWER_LINE", "Rede elétrica — eixo dos postes"],
    ["INTERFERENCES", "Outras interferências"],
    ["HYDROGRAPHY", "Hidrografia e receptores"],
    ["SOIL", "Solo e infiltração"],
    ["FLEET", "Frota e implementos"],
    ["OTHER", "Outro documento"],
  ];
  return options.map(([value, label]) => `<option value="${value}">${label}</option>`).join("");
}

function minimumDataCheck(label, ok, note) {
  return `<div class="check-row ${ok ? "" : "is-blocked"}"><div class="check-copy">${icon(ok ? "circle-check" : "circle-dashed")}<div><strong>${escapeHtml(label)}</strong><small style="display:block;color:var(--ink-500);margin-top:2px">${escapeHtml(note)}</small></div></div>${badge(ok ? "AVAILABLE" : "MISSING")}</div>`;
}

function renderAssetList(assets) {
  if (!assets.length) return `<div class="empty-state" style="min-height:220px"><span class="empty-icon">${icon("database")}</span><h3>Nenhum arquivo recebido</h3><p>Adicione os talhões e a fonte de elevação para começar.</p></div>`;
  return `<div class="data-table-wrap"><table class="data-table"><thead><tr><th>Arquivo</th><th>Tipo</th><th>Tamanho</th><th>CRS</th><th>QA</th><th></th></tr></thead><tbody>${assets.map((asset) => `<tr><td><strong>${escapeHtml(asset.name)}</strong><small class="mono" style="display:block;color:var(--ink-500);margin-top:3px">${escapeHtml(asset.checksum || "sem hash")}</small></td><td>${escapeHtml(asset.kind?.replaceAll("_", " ") || "OUTRO")}</td><td>${formatBytes(asset.size)}</td><td>${escapeHtml(asset.crs || "A confirmar")}</td><td>${badge(asset.status)}</td><td><button class="icon-button" data-delete-asset="${escapeHtml(asset.id)}" type="button" title="Remover arquivo" aria-label="Remover ${escapeHtml(asset.name)}">${icon("trash-2")}</button></td></tr>`).join("")}</tbody></table></div>`;
}

function bindUpload(projectId) {
  const input = document.querySelector("#file-input");
  const zone = document.querySelector("#drop-zone");
  const queueRoot = document.querySelector("#upload-queue");
  const enqueue = (files) => {
    for (const file of files) state.uploadQueue.push({ id: `${file.name}-${file.lastModified}-${Math.random()}`, file, progress: 0, status: "PENDING", error: null });
    renderUploadQueue(queueRoot);
  };
  input?.addEventListener("change", () => enqueue(input.files));
  ["dragenter", "dragover"].forEach((name) => zone?.addEventListener(name, (event) => { event.preventDefault(); zone.classList.add("is-dragging"); }));
  ["dragleave", "drop"].forEach((name) => zone?.addEventListener(name, (event) => { event.preventDefault(); zone.classList.remove("is-dragging"); }));
  zone?.addEventListener("drop", (event) => enqueue(event.dataTransfer.files));
  queueRoot?.addEventListener("click", async (event) => {
    const remove = event.target.closest("[data-remove-upload]");
    if (remove) {
      state.uploadQueue = state.uploadQueue.filter((item) => item.id !== remove.dataset.removeUpload);
      renderUploadQueue(queueRoot);
      return;
    }
    const upload = event.target.closest("#start-upload");
    if (!upload) return;
    upload.disabled = true;
    const kind = document.querySelector("#asset-kind")?.value || "OTHER";
    const crs = document.querySelector("#asset-crs")?.value || "";
    const pending = state.uploadQueue.filter((item) => ["PENDING", "FAILED"].includes(item.status));
    for (const item of pending) {
      item.status = "UPLOADING";
      item.error = null;
      renderUploadQueue(queueRoot);
      try {
        await api.uploadAsset(projectId, item.file, { kind, crs }, (progress) => { item.progress = progress; renderUploadQueue(queueRoot); });
        item.status = "DONE";
        item.progress = 100;
      } catch (error) {
        item.status = "FAILED";
        item.error = error.message;
      }
      renderUploadQueue(queueRoot);
    }
    if (pending.some((item) => item.status === "DONE")) {
      showToast("Upload concluído", "Os arquivos entraram na validação de qualidade.");
      window.setTimeout(() => renderProject(projectId, "data"), 600);
    }
  });
}

function renderUploadQueue(root) {
  if (!root) return;
  if (!state.uploadQueue.length) {
    root.innerHTML = "";
    return;
  }
  root.innerHTML = `<div class="file-list">${state.uploadQueue.map((item) => `<div class="file-row"><div class="file-identity"><span class="file-icon">${icon("file-up")}</span><div class="file-copy"><strong>${escapeHtml(item.file.name)}</strong><small>${formatBytes(item.file.size)} · ${item.error ? escapeHtml(item.error) : item.status === "UPLOADING" ? `enviando ${item.progress}%` : item.status === "DONE" ? "recebido" : "aguardando"}</small>${item.status === "UPLOADING" ? `<div class="progress-track" style="width:180px;margin-top:6px"><span style="width:${clamp(item.progress)}%"></span></div>` : ""}</div></div>${item.status === "DONE" ? badge("AVAILABLE", "Recebido") : item.status === "FAILED" ? badge("FAILED") : `<button class="icon-button" data-remove-upload="${escapeHtml(item.id)}" type="button" title="Remover da fila">${icon("x")}</button>`}</div>`).join("")}</div><div class="form-actions"><button class="button is-primary" id="start-upload" type="button">${icon("cloud-upload")} Enviar ${state.uploadQueue.filter((item) => ["PENDING", "FAILED"].includes(item.status)).length} arquivo(s)</button></div>`;
  hydrateIcons(root);
}

function bindAssetActions(projectId) {
  document.querySelectorAll("[data-delete-asset]").forEach((button) => button.addEventListener("click", async () => {
    if (!window.confirm("Remover este arquivo do projeto? O original e sua linhagem serão desvinculados da próxima rodada.")) return;
    try {
      await api.deleteAsset(projectId, button.dataset.deleteAsset);
      showToast("Arquivo removido", "A próxima avaliação de prontidão refletirá a mudança.", "warning");
      await renderProject(projectId, "data");
    } catch (error) {
      showToast("Não foi possível remover", error.message, "error");
    }
  }));
}

async function renderConfigureStep(project) {
  const [configuration, readiness] = await Promise.all([api.getConfiguration(project.id), api.getReadiness(project.id)]);
  const current = configuration || { preset_id: null, parameters: {} };
  main.innerHTML = `
    ${projectHead(project, "configure", `<button class="button" id="reset-parameters" type="button">${icon("rotate-ccw")} Restaurar preset</button><button class="button is-primary" id="save-configuration" type="button">${icon("save")} Salvar e continuar</button>`)}
    <section class="panel">
      <div class="panel-header"><div><h2>Preset inicial</h2><p>Defaults ajudam a começar, mas não promovem evidência nem removem gates técnicos.</p></div></div>
      <div class="panel-body"><div class="preset-grid">${systemCatalog.presets.map((preset) => `<article class="preset-card ${current.preset_id === preset.id ? "is-selected" : ""}" data-preset-id="${preset.id}" tabindex="0"><div class="card-title-row"><h3>${escapeHtml(preset.name)}</h3><span class="selection-check">${current.preset_id === preset.id ? icon("check") : ""}</span></div><p>${escapeHtml(preset.description)}</p><span class="badge is-info">${escapeHtml(preset.region)} · ${escapeHtml(preset.level)}</span></article>`).join("")}</div></div>
    </section>
    <section class="panel">
      <div class="panel-header"><div><h2>Parâmetros da rodada</h2><p>${readiness.parameter_summary?.resolved || 0} resolvidos; ${readiness.parameter_summary?.pending || 0} aguardam insumo ou confirmação.</p></div><span class="badge is-warning">Limite ${escapeHtml(readiness.maximum_delivery_level || "não pronto")}</span></div>
      <form id="parameter-form">${systemCatalog.parameterGroups.map((group, index) => renderParameterGroup(group, current.parameters || {}, index === 0)).join("")}</form>
    </section>`;

  let selectedPreset = current.preset_id || systemCatalog.presets[0].id;
  document.querySelectorAll("[data-preset-id]").forEach((card) => card.addEventListener("click", () => {
    selectedPreset = card.dataset.presetId;
    document.querySelectorAll("[data-preset-id]").forEach((item) => {
      item.classList.toggle("is-selected", item === card);
      item.querySelector(".selection-check").innerHTML = item === card ? icon("check") : "";
    });
    hydrateIcons(main);
  }));
  bindParameterGroups();
  document.querySelector("#reset-parameters")?.addEventListener("click", () => {
    if (!window.confirm("Restaurar todos os valores do preset selecionado?")) return;
    for (const group of systemCatalog.parameterGroups) for (const parameter of group.parameters) setParameterInput(parameter, parameter.default);
    showToast("Preset restaurado", "Os valores foram repostos no formulário; salve para registrar.", "warning");
  });
  document.querySelector("#save-configuration")?.addEventListener("click", async () => {
    const button = document.querySelector("#save-configuration");
    const payload = collectConfiguration(selectedPreset);
    const weights = ["objectives.soil_conservation_weight", "objectives.harvestability_weight", "objectives.performance_weight"].reduce((sum, id) => sum + Number(payload.parameters[id] || 0), 0);
    if (weights !== 100) {
      showToast("Pesos inválidos", `Os três objetivos somam ${weights}%. Ajuste para 100%.`, "error");
      return;
    }
    if (payload.parameters["hydrology.enabled"]) {
      const rainfall = String(payload.parameters["hydrology.rainfall_series_mm"] || "").split(/[;,\s]+/).filter(Boolean).map(Number);
      if (!rainfall.length || rainfall.some((value) => !Number.isFinite(value) || value < 0) || !String(payload.parameters["hydrology.parameter_source_id"] || "").trim()) {
        showToast("Dados da chuva incompletos", "Informe a chuva por intervalo com valores nao negativos e identifique a fonte dos parametros.", "error");
        return;
      }
      if (payload.parameters["hydrology.hydrograph_enabled"] && !(Number(payload.parameters["hydrology.catchment_lag_minutes"]) > 0)) {
        showToast("Tempo de resposta ausente", "Informe em quantos minutos a area responde ao evento de chuva.", "error");
        return;
      }
    }
    button.disabled = true;
    try {
      await api.saveConfiguration(project.id, payload);
      showToast("Configuração salva", "Valores e origem foram registrados para a próxima compilação.");
      navigate(`/projects/${project.id}/products`);
    } catch (error) {
      button.disabled = false;
      showToast("Falha ao salvar", error.message, "error");
    }
  });
}

function renderParameterGroup(group, values, expanded) {
  return `<div class="parameter-group"><div class="parameter-head" data-parameter-toggle="${group.id}" tabindex="0"><div><h3>${escapeHtml(group.name)}</h3><p>${escapeHtml(group.description)}</p></div>${icon(expanded ? "chevron-up" : "chevron-down")}</div><div class="parameter-body ${expanded ? "" : "is-hidden"}" data-parameter-body="${group.id}"><div class="data-table-wrap"><table class="parameter-table"><thead><tr><th>Parâmetro</th><th>Valor</th><th>Unidade</th><th>Origem</th></tr></thead><tbody>${group.parameters.map((parameter) => `<tr><td><strong>${escapeHtml(parameter.name)}</strong><small class="mono" style="display:block;color:var(--ink-500);margin-top:3px">${escapeHtml(parameter.id)}</small></td><td>${parameterInput(parameter, values[parameter.id] ?? parameter.default)}</td><td>${escapeHtml(parameter.unit || "—")}</td><td>${badge(parameter.source === "Cliente" ? "PARTIAL" : "AVAILABLE", parameter.source)}</td></tr>`).join("")}</tbody></table></div></div></div>`;
}

function parameterInput(parameter, value) {
  if (parameter.type === "boolean") return `<label class="toggle"><input data-parameter-id="${parameter.id}" type="checkbox" ${value ? "checked" : ""}><span></span></label>`;
  if (parameter.type === "select") return `<select data-parameter-id="${parameter.id}">${parameter.options.map((option) => `<option value="${escapeHtml(option.value)}" ${String(value) === option.value ? "selected" : ""}>${escapeHtml(option.label)}</option>`).join("")}</select>`;
  if (parameter.type === "text") return `<input data-parameter-id="${parameter.id}" type="text" value="${escapeHtml(value || "")}" />`;
  if (parameter.type === "json") return `<textarea data-parameter-id="${parameter.id}" rows="6">${escapeHtml(value || "[]")}</textarea>`;
  return `<input data-parameter-id="${parameter.id}" type="number" value="${escapeHtml(value)}" min="${parameter.min}" max="${parameter.max}" step="${parameter.step}" />`;
}

function bindParameterGroups() {
  document.querySelectorAll("[data-parameter-toggle]").forEach((head) => {
    const toggle = () => {
      const body = document.querySelector(`[data-parameter-body="${head.dataset.parameterToggle}"]`);
      body.classList.toggle("is-hidden");
      head.querySelector("svg")?.remove();
      head.insertAdjacentHTML("beforeend", icon(body.classList.contains("is-hidden") ? "chevron-down" : "chevron-up"));
      hydrateIcons(head);
    };
    head.addEventListener("click", toggle);
    head.addEventListener("keydown", (event) => { if (event.key === "Enter") toggle(); });
  });
}

function setParameterInput(parameter, value) {
  const input = document.querySelector(`[data-parameter-id="${CSS.escape(parameter.id)}"]`);
  if (!input) return;
  if (parameter.type === "boolean") input.checked = Boolean(value);
  else input.value = value;
}

function collectConfiguration(presetId) {
  const parameters = {};
  document.querySelectorAll("[data-parameter-id]").forEach((input) => {
    const definition = systemCatalog.parameterGroups.flatMap((group) => group.parameters).find((item) => item.id === input.dataset.parameterId);
    parameters[input.dataset.parameterId] = definition?.type === "boolean" ? input.checked : definition?.type === "number" ? Number(input.value) : input.value;
  });
  return { preset_id: presetId, parameters };
}

const readinessAliases = {
  SULCATION_E0: "E0",
  CF0_CONTINUOUS: "CF0",
  C1_EMBEDDED_SCREENING: "C1",
  C2_BROAD_BASE: "C2",
  C3_ESD: "C3",
  POA_STATIC: "POA",
  PCX1_RUNOFF_SCREENING: "PCX1_RUNOFF_SCREENING",
  PCX2_HYDROGRAPH_SCREENING: "PCX2_HYDROGRAPH_SCREENING",
  PCX3_REACH_ROUTING_SCREENING: "PCX3_REACH_ROUTING_SCREENING",
  PCX4_SECTION_CAPACITY_SCREENING: "PCX4_SECTION_CAPACITY_SCREENING",
  PCX5_WATER_SURFACE_PROFILE_SCREENING: "PCX5_WATER_SURFACE_PROFILE_SCREENING",
  PCX6_OVERFLOW_PATH_SCREENING: "PCX6_OVERFLOW_PATH_SCREENING",
};

function productReadiness(readiness, product) {
  const alias = readinessAliases[product.id];
  const gate = readiness.scenarios?.find((item) => item.id === product.id || item.id === alias);
  const topographyFallback = product.id === "TOPOGRAPHY_E0" && readiness.maximum_delivery_level !== "NOT_READY";
  const ready = Boolean(product.available && (gate ? gate.state === "READY" : topographyFallback));
  return { gate, ready };
}

function requestMatchesProducts(request, productIds) {
  if (!request) return false;
  const requested = [...(request.product_ids || request.products || [])].sort();
  const selected = [...productIds].sort();
  return requested.length === selected.length && requested.every((item, index) => item === selected[index]);
}

function executionEngineFor(productIds) {
  const hydrologyProducts = new Set(["PCX1_RUNOFF_SCREENING", "PCX2_HYDROGRAPH_SCREENING", "PCX3_REACH_ROUTING_SCREENING", "PCX4_SECTION_CAPACITY_SCREENING", "PCX5_WATER_SURFACE_PROFILE_SCREENING", "PCX6_OVERFLOW_PATH_SCREENING"]);
  if (productIds.length > 0 && productIds.every((item) => hydrologyProducts.has(item)) && productIds.includes("PCX1_RUNOFF_SCREENING")) return "project_hydrology_screening";
  const supported = new Set(["TOPOGRAPHY_E0", "SULCATION_E0", "CF0_CONTINUOUS"]);
  if (productIds.some((item) => !supported.has(item))) return null;
  if (productIds.length === 1 && productIds[0] === "TOPOGRAPHY_E0") return "project_topography";
  if (productIds.some((item) => ["SULCATION_E0", "CF0_CONTINUOUS"].includes(item))) return "project_pipeline_e0";
  return null;
}

const blockerLabels = {
  FIELD_BOUNDARY_MISSING: "Poligonos dos talhoes ausentes",
  FIELD_BOUNDARIES: "Poligonos dos talhoes",
  ELEVATION_SOURCE_MISSING: "Nuvem de pontos ou modelo de elevacao ausente",
  FIELD_ID_COLUMN_REQUIRED: "Campo identificador dos talhoes nao informado",
  POWER_NETWORK_UNRESOLVED: "Rede eletrica ainda nao declarada",
  SOIL_HYDROLOGY_MISSING: "Dados hidrologicos do solo ausentes",
  ROADS_CARRIERS_MISSING: "Carreadores e estradas ausentes",
  FLEET_CONFIGURATION_MISSING: "Configuracao da frota ausente",
  HYDRAULIC_RECEIVER_MISSING: "Saida de agua ou receptor nao informado",
  DESIGN_RAIN_MISSING: "Chuva de projeto ausente",
  RECEIVERS_MISSING: "Saidas de agua e receptores ausentes",
  CLIENT_ENGINE_NOT_AVAILABLE: "Motor deste produto ainda nao disponivel",
  PCX1_RUNOFF_DEPENDENCY_REQUIRED: "Selecione tambem Chuva que vira escoamento",
  HYDROGRAPH_CONFIGURATION_NOT_ENABLED: "Ative o calculo da vazao ao longo do tempo",
  CATCHMENT_LAG_REQUIRED: "Informe o tempo de resposta da area",
  HYDROGRAPH_DEPENDENCY_REQUIRED: "Selecione tambem o Hidrograma preliminar",
  ROUTING_CONFIGURATION_NOT_ENABLED: "Ative a propagacao da vazao pela rede",
  ROUTING_NETWORK_REQUIRED: "Informe o ponto de entrada e os trechos da rede",
  ROUTING_DEPENDENCY_REQUIRED: "Selecione tambem a Propagacao preliminar na rede",
  SECTION_CONFIGURATION_REQUIRED: "Informe as secoes e condicoes dos trechos",
  CF0_CONTINUOUS_DEPENDENCY_REQUIRED: "Gere primeiro a familia curva continua",
  PROJECT_SCOPE: "Limites e escopo do projeto",
  TERRAIN_SOURCE: "Fonte de elevacao do terreno",
  FIELD_BOUNDARY: "Poligonos dos talhoes",
  ELEVATION_SOURCE: "Nuvem de pontos ou modelo de elevacao",
  SOIL_HYDROLOGY: "Dados hidrologicos do solo",
  DESIGN_RAIN: "Chuva de projeto",
  RECEIVERS: "Saidas de agua e receptores",
  FLEET_CONFIGURATION: "Configuracao da frota",
  INTERFERENCES: "Interferencias",
  PORTALS: "Pontos autorizados de passagem",
  ROADS: "Carreadores e estradas",
  LOGISTICS: "Dados de logistica",
  HIDROGRAMA_PRELIMINAR: "Hidrograma preliminar",
  REDE_DE_ESCOAMENTO: "Trechos da rede de escoamento",
  PROPAGACAO_NA_REDE: "Propagacao preliminar na rede",
  SECOES_DOS_TRECHOS: "Secoes e condicoes dos trechos",
};

function blockerText(items = []) {
  return items.map((item) => blockerLabels[item] || String(item).toLowerCase().replaceAll("_", " ")).join("; ");
}

function deliveryLabel(level) {
  return ({ E0: "Estudo preliminar", E1: "Anteprojeto", E2: "Projeto tecnico", "E0–E3": "Varias etapas" })[level] || level;
}

async function renderProductsStep(project) {
  const [selection, readiness] = await Promise.all([api.getProductSelection(project.id), api.getReadiness(project.id)]);
  const selectableIds = new Set(systemCatalog.products.filter((product) => productReadiness(readiness, product).ready).map((product) => product.id));
  const selected = new Set((selection.products || []).filter((productId) => selectableIds.has(productId)));
  main.innerHTML = `
    ${projectHead(project, "products", `<button class="button is-primary" id="save-products" type="button">${icon("save")} Salvar escopo</button>`)}
    <div class="callout is-info">${icon("layers-3")}<div><strong>Escolha produtos, não promessas.</strong>A plataforma executará apenas os estágios liberados pelos dados; itens bloqueados permanecem visíveis com seus insumos necessários.</div></div>
    <section class="section">
      <div class="section-head"><div><h2>Produtos da rodada</h2><p>Selecione os produtos que devem compor o pedido e o dossiê.</p></div><span id="selection-count" class="badge is-info">${selected.size} selecionados</span></div>
      <div class="product-grid">${systemCatalog.products.map((product) => {
        const { gate, ready } = productReadiness(readiness, product);
        const blocked = !ready;
        const isSelected = selected.has(product.id) && !blocked;
        const blockers = gate?.blockers?.length ? blockerText(gate.blockers) : blockerText(product.requires);
        return `<article class="product-card ${isSelected ? "is-selected" : ""} ${blocked ? "is-disabled" : ""}" data-product-id="${product.id}" data-blocked="${blocked}" tabindex="${blocked ? "-1" : "0"}"><div class="card-title-row"><h3>${escapeHtml(product.name)}</h3><span class="selection-check">${isSelected ? icon("check") : blocked ? icon("lock") : ""}</span></div><p>${escapeHtml(product.description)}</p><div style="margin-top:auto;display:flex;align-items:center;gap:6px;flex-wrap:wrap"><span class="badge is-info">${escapeHtml(deliveryLabel(product.level))}</span>${blocked ? `<span class="badge is-danger">Bloqueado</span>` : `<span class="badge is-success">Disponível</span>`}</div>${blocked && blockers ? `<p class="field-help">Pendências: ${escapeHtml(blockers)}</p>` : ""}</article>`;
      }).join("")}</div>
    </section>
    <div class="form-actions"><button class="button" data-go-step="configure" type="button">${icon("arrow-left")} Voltar</button><button class="button is-primary" id="continue-products" type="button">Revisar execução ${icon("arrow-right")}</button></div>`;
  bindGoSteps(project.id);
  const updateCount = () => { document.querySelector("#selection-count").textContent = `${selected.size} selecionados`; };
  document.querySelectorAll("[data-product-id]").forEach((card) => {
    if (card.dataset.blocked === "true") return;
    const toggle = () => {
      if (selected.has(card.dataset.productId)) selected.delete(card.dataset.productId);
      else selected.add(card.dataset.productId);
      card.classList.toggle("is-selected", selected.has(card.dataset.productId));
      card.querySelector(".selection-check").innerHTML = selected.has(card.dataset.productId) ? icon("check") : "";
      updateCount();
      hydrateIcons(card);
    };
    card.addEventListener("click", toggle);
    card.addEventListener("keydown", (event) => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); toggle(); } });
  });
  const save = async (continueNext) => {
    if (!selected.size) return showToast("Seleção vazia", "Escolha pelo menos um produto para a rodada.", "error");
    try {
      await api.saveProductSelection(project.id, [...selected]);
      showToast("Escopo salvo", `${selected.size} produto(s) selecionado(s).`);
      if (continueNext) navigate(`/projects/${project.id}/run`);
    } catch (error) {
      showToast("Falha ao salvar", error.message, "error");
    }
  };
  document.querySelector("#save-products")?.addEventListener("click", () => save(false));
  document.querySelector("#continue-products")?.addEventListener("click", () => save(true));
}

async function renderRunStep(project) {
  const [selection, readiness, configuration, latestRequest, runs] = await Promise.all([api.getProductSelection(project.id), api.getReadiness(project.id), api.getConfiguration(project.id), api.getLatestRequest(project.id), api.listRuns(project.id)]);
  const products = systemCatalog.products.filter((item) => (selection.products || []).includes(item.id));
  const productIds = products.map((item) => item.id);
  const productStates = products.map((product) => ({ product, ...productReadiness(readiness, product) }));
  const engineId = executionEngineFor(productIds);
  const canRun = products.length > 0 && productStates.every((item) => item.ready) && Boolean(engineId) && (engineId === "project_hydrology_screening" || readiness.maximum_delivery_level !== "NOT_READY");
  const compiledRequest = requestMatchesProducts(latestRequest, productIds) ? latestRequest : null;
  main.innerHTML = `
    ${projectHead(project, "run", `<button class="button" id="compile-request" type="button">${icon("file-lock-2")} Compilar pedido</button><button class="button is-primary" id="start-run" type="button" ${!canRun ? "disabled" : ""}>${icon("play")} Iniciar processamento</button>`)}
    <div class="content-grid">
      <div>
        <section class="panel">
          <div class="panel-header"><div><h2>Revisão da rodada</h2><p>O pedido compilado é imutável e recebe hash antes de entrar na fila.</p></div>${compiledRequest ? badge(compiledRequest.status || "AVAILABLE", "Compilado") : badge("PENDING", "Não compilado")}</div>
          <div class="panel-body">
            <div class="form-grid is-three">
              <div class="stat-block"><div class="stat-top"><span>Preset</span>${icon("sliders-horizontal")}</div><strong style="display:block;margin-top:9px;font-size:12px">${escapeHtml(systemCatalog.presets.find((item) => item.id === configuration.preset_id)?.name || "Não definido")}</strong></div>
              <div class="stat-block"><div class="stat-top"><span>Produtos</span>${icon("package-check")}</div><strong class="stat-value">${products.length}</strong></div>
              <div class="stat-block"><div class="stat-top"><span>Limite atual</span>${icon("shield-check")}</div><strong style="display:block;margin-top:9px;font-size:12px">${escapeHtml(readiness.maximum_delivery_level || "Não pronto")}</strong></div>
            </div>
            <div class="section-head" style="margin-top:20px"><div><h2>Produtos solicitados</h2></div></div>
            <div class="readiness-list">${productStates.map(({ product, ready, gate }) => `<div class="readiness-row"><div class="readiness-copy"><strong>${escapeHtml(product.name)}</strong><small>${escapeHtml(ready ? product.description : blockerText(gate?.blockers) || "Produto indisponível para os insumos atuais")}</small></div>${badge(ready ? "READY" : "BLOCKED", ready ? deliveryLabel(product.level) : "Bloqueado")}</div>`).join("") || emptyCompact("Nenhum produto selecionado")}</div>
            ${compiledRequest ? `<div class="callout is-success" style="margin-top:14px">${icon("fingerprint")}<div><strong>Pedido ${escapeHtml(compiledRequest.id)}</strong><span class="mono">SHA-256 ${escapeHtml(compiledRequest.hash)}</span></div></div>` : `<div class="callout is-warning" style="margin-top:14px">${icon("file-warning")}<div><strong>${latestRequest ? "O escopo mudou desde o último pedido." : "O pedido ainda será compilado."}</strong>Compile para congelar os dados, parâmetros, produtos e versões desta rodada.</div></div>`}
          </div>
        </section>
        <section class="panel">
          <div class="panel-header"><div><h2>Execuções deste projeto</h2><p>Estados recentes e acesso aos logs.</p></div></div>
          <div class="panel-body">${runs.length ? runs.slice(0, 4).map(renderJobCard).join("") : `<div class="empty-state" style="min-height:190px"><span class="empty-icon">${icon("activity")}</span><h3>Nenhuma execução</h3><p>Compile o pedido e inicie a primeira rodada.</p></div>`}</div>
        </section>
      </div>
      <aside class="content-aside">
        <section class="panel"><div class="panel-header"><h3>Gates da execução</h3></div><div class="panel-body"><div class="checklist">
          ${minimumDataCheck(engineId === "project_hydrology_screening" ? "Dados da chuva completos" : "Dados mínimos E0", engineId === "project_hydrology_screening" || readiness.maximum_delivery_level !== "NOT_READY", engineId === "project_hydrology_screening" ? "Área, comportamento do solo, fonte e chuva no tempo" : readiness.maximum_delivery_level || "Não pronto")}
          ${minimumDataCheck("Produtos selecionados", products.length > 0, `${products.length} produto(s)`) }
          ${minimumDataCheck("Configuração salva", Boolean(configuration.preset_id), configuration.preset_id || "Escolha um preset")}
          ${minimumDataCheck("Produtos liberados", productStates.length > 0 && productStates.every((item) => item.ready), productStates.every((item) => item.ready) ? "Readiness confirmado" : "Há produto bloqueado")}
          ${minimumDataCheck("Motor compatível", Boolean(engineId), engineId || "Combinação ainda não executável")}
          ${minimumDataCheck("Pedido imutável", Boolean(compiledRequest), compiledRequest?.id || "Compile o escopo atual")}
        </div></div></section>
        <section class="panel"><div class="panel-header"><h3>Fronteira de entrega</h3></div><div class="panel-body"><div class="callout is-warning">${icon("shield-alert")}<div><strong>${escapeHtml(readiness.maximum_delivery_level || "Não pronta")}</strong>A execução pode concluir tecnicamente sem autorizar implantação, hidráulica ou guiamento.</div></div></div></section>
      </aside>
    </div>`;
  document.querySelectorAll("[data-run-id]").forEach((button) => button.addEventListener("click", () => navigate(`/runs/${button.dataset.runId}`)));
  let compiled = compiledRequest;
  const compile = async () => {
    const button = document.querySelector("#compile-request");
    button.disabled = true;
    button.innerHTML = `<span class="spinner is-small"></span> Compilando...`;
    try {
      compiled = await api.compileRequest(project.id, { products: products.map((item) => item.id), preset_id: configuration.preset_id, delivery_limit: readiness.maximum_delivery_level });
      showToast("Pedido compilado", "Hash e linhagem foram congelados para esta rodada.");
      await renderProject(project.id, "run");
      return compiled;
    } catch (error) {
      button.disabled = false;
      button.innerHTML = `${icon("file-lock-2")} Compilar pedido`;
      hydrateIcons(button);
      showToast("Falha na compilação", error.message, "error");
      throw error;
    }
  };
  document.querySelector("#compile-request")?.addEventListener("click", compile);
  document.querySelector("#start-run")?.addEventListener("click", async () => {
    const button = document.querySelector("#start-run");
    button.disabled = true;
    button.innerHTML = `<span class="spinner is-small"></span> Enviando à fila...`;
    try {
      if (!compiled) compiled = await api.compileRequest(project.id, { products: products.map((item) => item.id), preset_id: configuration.preset_id, delivery_limit: readiness.maximum_delivery_level });
      const run = await api.startRun(compiled.id, { name: `Rodada ${products.map((item) => item.id).join(" + ")}` });
      showToast("Execução iniciada", "A rodada entrou na fila de processamento.");
      navigate(`/runs/${run.id}`);
    } catch (error) {
      button.disabled = false;
      button.innerHTML = `${icon("play")} Iniciar processamento`;
      hydrateIcons(button);
      showToast("Não foi possível iniciar", error.message, "error");
    }
  });
}

function renderJobCard(run) {
  return `<article class="job-card"><div class="job-title"><div><h3>${escapeHtml(run.name)}</h3><p>${escapeHtml(run.stage || "Sem estágio informado")} · ${formatDate(run.started_at, true)}</p></div>${badge(run.status)}</div><div class="job-progress"><div class="progress-line"><span>${escapeHtml(run.stage || "Processamento")}</span><strong>${clamp(run.progress)}%</strong></div><div class="progress-track"><span style="width:${clamp(run.progress)}%"></span></div></div><div class="job-metrics"><span>Duração <strong>${formatDuration(run.duration_seconds)}</strong></span><span>Artefatos <strong>${Number(run.artifact_count || 0)}</strong></span><button class="button is-small" style="margin-left:auto" data-run-id="${escapeHtml(run.id)}" type="button">Abrir ${icon("arrow-right")}</button></div></article>`;
}

async function renderResultsStep(project) {
  const runs = await api.listRuns(project.id);
  const successful = runs.find((item) => item.status === "SUCCEEDED");
  if (!successful) {
    const latest = runs[0];
    const terminalWithoutResults = ["FAILED", "BLOCKED", "CANCELLED"].includes(latest?.status);
    const cancelled = latest?.status === "CANCELLED";
    const title = terminalWithoutResults
      ? cancelled ? "A rodada foi cancelada" : "A rodada não publicou resultados"
      : latest ? "A rodada ainda está em processamento" : "Ainda não há resultados";
    const detail = terminalWithoutResults
      ? cancelled
        ? "Nenhum produto final foi publicado. O pedido pode ser executado novamente quando necessário."
        : "Consulte os logs para identificar o gate ou erro de processamento antes de executar novamente."
      : latest ? "Os produtos aparecerão aqui somente depois da publicação concluída." : "Execute uma rodada para comparar cenários e baixar os produtos.";
    main.innerHTML = `${projectHead(project, "results")}<div class="panel empty-state"><span class="empty-icon">${icon(terminalWithoutResults ? cancelled ? "ban" : "circle-alert" : latest ? "loader-circle" : "package-open")}</span><h3>${title}</h3><p>${detail}</p>${latest ? `<button class="button is-primary" data-open-pending-run="${escapeHtml(latest.id)}" type="button">${icon("scroll-text")} Abrir execução</button>` : `<button class="button is-primary" data-go-step="run" type="button">${icon("play")} Ir para execução</button>`}</div>`;
    bindGoSteps(project.id);
    document.querySelector("[data-open-pending-run]")?.addEventListener("click", (event) => navigate(`/runs/${event.currentTarget.dataset.openPendingRun}`));
    return;
  }
  const [artifacts, scenarios] = await Promise.all([api.listArtifacts(successful.id), api.listScenarios(successful.id)]);
  const packageArtifact = findPackageArtifact(artifacts);
  state.compareIds = state.compareIds.filter((id) => scenarios.some((item) => item.id === id));
  if (state.compareIds.length < 2) {
    const eligibleFirst = [...scenarios].sort((left, right) => Number(scenarioIsGeometryEligible(right)) - Number(scenarioIsGeometryEligible(left)));
    state.compareIds = eligibleFirst.slice(0, Math.min(3, eligibleFirst.length)).map((item) => item.id);
  }
  const persistedReviewRepresentative = scenarios.find((item) => item.selected_for_review === true);
  state.selectedScenarioId = scenarios.some((item) => item.id === state.selectedScenarioId)
    ? state.selectedScenarioId
    : persistedReviewRepresentative?.id || scenarios.find(scenarioIsRecommended)?.id || scenarios.find(scenarioIsGeometryEligible)?.id || scenarios[0]?.id;
  main.innerHTML = `
    ${projectHead(project, "results", `<button class="button" data-open-run="${escapeHtml(successful.id)}" type="button">${icon("scroll-text")} Logs da rodada</button>${packageArtifact ? `<button class="button is-primary" id="download-package" type="button">${icon("download")} ${packageArtifact.format === "PDF" ? "Baixar dossiê" : "Baixar manifesto"}</button>` : ""}`)}
    ${scenarios.length ? `<div class="scenario-tablist" role="tablist" aria-label="Cenários publicados">${scenarios.map((scenario) => {
      const selected = scenario.id === state.selectedScenarioId;
      const eligible = scenarioIsGeometryEligible(scenario);
      const reviewRepresentative = scenario.selected_for_review === true;
      return `<button class="scenario-tab ${selected ? "is-active" : ""} ${eligible ? "" : "is-ineligible"}" data-scenario-tab="${escapeHtml(scenario.id)}" data-geometry-eligible="${eligible}" data-selected-for-review="${reviewRepresentative}" type="button" role="tab" aria-selected="${selected}"><strong>${escapeHtml(scenario.code)} · ${escapeHtml(scenario.name)}</strong><small>${escapeHtml(scenario.family)} · ${escapeHtml(statusLabel(scenario.status))}${scenarioIsRecommended(scenario) ? " · Recomendado" : ""}${reviewRepresentative ? " · Representante em revisão" : ""}</small></button>`;
    }).join("")}</div>${renderScenarioFocus(scenarios.find((item) => item.id === state.selectedScenarioId), artifacts)}<section class="section">
      <div class="section-head"><div><h2>Comparação de alternativas</h2><p>Selecione de duas a quatro alternativas; a recomendação não substitui decisão agronômica.</p></div><button class="button is-small" id="choose-comparison" type="button">${icon("list-checks")} Selecionar cenários</button></div>
      <div id="comparison-table">${renderComparisonTable(scenarios.filter((item) => state.compareIds.includes(item.id)))}</div>
    </section>` : renderNoScenarioFocus(artifacts, successful)}
    <section class="section">
      <div class="section-head"><div><h2>Produtos publicados</h2><p>Downloads preservam formato, verificador e checksum da rodada.</p></div><span class="badge is-success">${artifacts.length} artefatos</span></div>
      ${renderArtifacts(artifacts)}
    </section>`;
  bindResultActions(project, successful, artifacts, scenarios);
}

function renderNoScenarioFocus(artifacts, run) {
  if (run.engine_id === "project_hydrology_screening") return renderHydrologyFocus(run);
  const topography = renderTopographyFocus(artifacts);
  if (run.engine_id !== "project_pipeline_e0") return topography;
  return `<div class="callout is-warning">${icon("triangle-alert")}<div><strong>Nenhum cenário foi publicado.</strong>A topografia pode ter sido concluída, mas as alternativas E0/CF0/C1 não foram liberadas nesta rodada. Verifique o manifesto e os logs.</div></div>${topography}`;
}

function renderHydrologyFocus(run) {
  const summary = run.result_summary || {};
  const hydrograph = summary.peak_flow_m3_s != null
    ? `${statBlock("Vazao maxima estimada", `${Number(summary.peak_flow_m3_s).toLocaleString("pt-BR", { maximumFractionDigits: 3 })} m3/s`, "activity", "Hidrograma preliminar")}${statBlock("Tempo ate o pico", `${Number(summary.time_to_peak_minutes).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} min`, "clock-3", "Desde o inicio da chuva")}`
    : "";
  const pending = summary.peak_flow_m3_s == null
    ? "Vazao ao longo do tempo, percurso da agua, capacidade das estruturas e seguranca da saida."
    : "Percurso da agua, propagacao em canais, capacidade das estruturas e seguranca da saida.";
  const routing = summary.routed_reach_count != null
    ? `${statBlock("Trechos percorridos", Number(summary.routed_reach_count).toLocaleString("pt-BR"), "route", "Rede declarada")}${statBlock("Saidas finais", Number(summary.routing_outlet_count).toLocaleString("pt-BR"), "signpost", "Sem validar o receptor")}`
    : "";
  const routingPending = summary.routed_reach_count != null
    ? "Atenuacao, remanso, capacidade das estruturas, caminho de falha e seguranca das saidas."
    : pending;
  const capacity = summary.capacity_within_count != null
    ? `${statBlock("Trechos com folga", Number(summary.capacity_within_count).toLocaleString("pt-BR"), "circle-check", "Somente escoamento uniforme")}${statBlock("Trechos excedidos", Number(summary.capacity_exceeded_count).toLocaleString("pt-BR"), "triangle-alert", "Exigem revisao")}`
    : "";
  const stability = summary.stability_evaluated_count != null
    ? `${statBlock("Estados com limite comparado", Number(summary.stability_evaluated_count).toLocaleString("pt-BR"), "shield-check", "Velocidade ou tensao declarada")}${statBlock("Estados acima do limite", Number(summary.stability_exceeded_count).toLocaleString("pt-BR"), "shield-alert", "Nao aprova seguranca erosiva")}`
    : "";
  const freeboard = summary.freeboard_evaluated_count != null
    ? `${statBlock("Estados com margem informada", Number(summary.freeboard_evaluated_count).toLocaleString("pt-BR"), "move-vertical", "Borda livre declarada")}${statBlock("Estados com transbordamento", Number(summary.overtopping_count).toLocaleString("pt-BR"), "waves", `${Number(summary.overflow_path_declared_count || 0).toLocaleString("pt-BR")} caminhos declarados`)}`
    : "";
  const downstream = summary.downstream_evaluated_count != null
    ? `${statBlock("Estados com jusante informado", Number(summary.downstream_evaluated_count).toLocaleString("pt-BR"), "arrow-down-to-line", "Envelope preliminar")}${statBlock("Estados controlados pelo jusante", Number(summary.downstream_controlled_count).toLocaleString("pt-BR"), "move-up", "Pode elevar a lamina")}`
    : "";
  const profiles = summary.water_profile_count != null
    ? `${statBlock("Perfis calculados", Number(summary.water_profile_count).toLocaleString("pt-BR"), "chart-spline", "Passo padrao subcritico")}${statBlock("Maior profundidade do perfil", `${Number(summary.water_profile_maximum_depth_m).toLocaleString("pt-BR", { maximumFractionDigits: 3 })} m`, "ruler", "Entre as secoes declaradas")}`
    : "";
  const overflowPaths = summary.overflow_path_screened_count != null
    ? `${statBlock("Caminhos verificados", Number(summary.overflow_path_screened_count).toLocaleString("pt-BR"), "route", `${Number(summary.overflow_path_clear_count || 0).toLocaleString("pt-BR")} sem conflito detectado`)}${statBlock("Conflitos com barreiras", Number(summary.overflow_path_barrier_conflict_count || 0).toLocaleString("pt-BR"), "shield-alert", "Exigem revisao espacial")}`
    : "";
  const capacityPending = summary.capacity_within_count != null
    ? "Propagacao do volume extravasado, capacidade dos receptores, estruturas, validacao local dos limites erosivos e seguranca das saidas."
    : routingPending;
  return `<section class="panel"><div class="panel-header"><div><h2>Resposta da area a chuva</h2><p>Resultado do evento congelado no pedido; ainda nao representa dimensionamento de canais ou estruturas.</p></div>${badge("LIMITED", "Estudo preliminar")}</div><div class="panel-body"><div class="stats-grid">${statBlock("Chuva total", `${Number(summary.total_rainfall_mm || 0).toLocaleString("pt-BR", { maximumFractionDigits: 2 })} mm`, "cloud-rain", "Evento informado")}${statBlock("Parcela que escoa", `${Number(summary.total_rainfall_excess_mm || 0).toLocaleString("pt-BR", { maximumFractionDigits: 3 })} mm`, "waves", "Estimativa pelo solo e cobertura")}${statBlock("Volume gerado", `${Number(summary.total_rainfall_excess_volume_m3 || 0).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} m3`, "container", "Antes de percorrer a bacia")}${statBlock("Proporcao escoada", Number(summary.runoff_coefficient_event || 0).toLocaleString("pt-BR", { maximumFractionDigits: 3 }), "ratio", "Varia conforme o evento")}${hydrograph}${routing}${capacity}${stability}${freeboard}${downstream}${profiles}${overflowPaths}</div><div class="callout is-warning" style="margin-top:14px">${icon("shield-alert")}<div><strong>O que ainda precisa ser calculado</strong>${capacityPending}</div></div></div></section>`;
}

function renderTopographyFocus(artifacts) {
  const map = artifacts.find((item) => item.name === "topography_map.png") || artifacts.find((item) => item.kind === "MAP");
  const slope = artifacts.find((item) => item.name === "slope_map.png");
  return `<div class="content-grid is-wide-aside">
    <div class="map-panel">${map?.preview_url ? `<img src="${safeUrl(map.preview_url)}" alt="Mapa topográfico da rodada" />` : `<div class="map-placeholder">${icon("map")}<p>Mapa topográfico não publicado.</p></div>`}</div>
    <aside class="content-aside">
      <section class="panel"><div class="panel-header"><div><h3>Topografia E0</h3><p>Superfície e derivados verificados</p></div>${badge("PUBLISHED")}</div><div class="panel-body"><div class="checklist">
        ${minimumDataCheck("Modelo digital do terreno", artifacts.some((item) => item.name === "dtm.tif"), "GeoTIFF da rodada")}
        ${minimumDataCheck("Curvas de nível", artifacts.some((item) => item.name === "contours.gpkg"), "GeoPackage vetorial")}
        ${minimumDataCheck("Declividade", Boolean(slope), "Mapa e rasters percentuais")}
      </div></div></section>
      <section class="panel"><div class="panel-header"><h3>Fronteira técnica</h3></div><div class="panel-body"><div class="callout is-warning">${icon("shield-alert")}<div><strong>Triagem E0</strong>Precisão vertical, hidrologia e liberação para guiamento permanecem pendentes.</div></div></div></section>
    </aside>
  </div>`;
}

function renderScenarioFocus(scenario, artifacts) {
  if (!scenario) return `<div class="panel empty-state"><span class="empty-icon">${icon("git-compare-arrows")}</span><h3>Nenhum cenário publicado</h3><p>A execução concluiu sem alternativas comparáveis.</p></div>`;
  const family = `${scenario.family || ""} ${scenario.code || ""}`.toUpperCase();
  const preferredMapName = family.includes("C1")
    ? "embedded_terrace_screening_map.png"
    : family.includes("CF0")
      ? "continuous_family_map.png"
      : "sulcation_scenarios_map.png";
  const map = artifacts.find((item) => item.name === preferredMapName && item.preview_url);
  const metrics = scenario.metrics || {};
  const geometryIneligible = !scenarioIsGeometryEligible(scenario);
  const cf0HydraulicPending = family.includes("CF0") && !geometryIneligible;
  const selectableForReview = scenarioCanRepresentTechnicalReview(scenario);
  const selectedForReview = scenario.selected_for_review === true;
  const blockerText = (scenario.blocker_codes || []).join(", ");
  return `<div class="content-grid is-wide-aside">
    <div class="map-panel">
      ${map ? `<img src="${safeUrl(map.preview_url)}" data-map-product="${escapeHtml(preferredMapName)}" alt="Mapa do cenário ${escapeHtml(scenario.name)}" />` : `<div class="map-placeholder">${icon("map")}<p>O backend não publicou uma prévia cartográfica para este cenário.</p></div>`}
      <div class="map-legend"><strong>Produto cartográfico</strong><div class="legend-row"><span class="legend-line"></span>Geometria de triagem ${escapeHtml(scenario.family || "E0")}</div><div class="legend-row"><span class="legend-line is-contour"></span>Referência topográfica</div></div>
    </div>
    <aside class="content-aside">
      <section class="panel"><div class="panel-header"><div><h3>${escapeHtml(scenario.code)} · ${escapeHtml(scenario.name)}</h3><p>${escapeHtml(scenario.family)}</p></div>${badge(scenario.status)}</div><div class="panel-body"><div class="metric-list">
        ${metricBar("Conservação", metrics.conservation_score)}
        ${metricBar("Colheitabilidade", metrics.harvestability_score)}
        ${metricBar("Performance", metrics.operational_score)}
      </div><ul class="summary-list" style="margin-top:13px"><li><span>Tiro médio</span><strong>${formatScenarioMetric(metrics.average_shot_m, " m")}</strong></li><li><span>Tiro P95</span><strong>${formatScenarioMetric(metrics.p95_shot_m, " m")}</strong></li><li><span>Manobras/ha</span><strong>${formatScenarioMetric(metrics.maneuvers_per_ha)}</strong></li><li><span>Declividade transversal P95</span><strong>${formatScenarioMetric(metrics.cross_slope_p95_pct, "%")}</strong></li></ul></div></section>
      <section class="panel"><div class="panel-header"><h3>Decisão</h3></div><div class="panel-body" data-scenario-decision="${geometryIneligible ? "ineligible" : scenarioIsRecommended(scenario) ? "recommended" : cf0HydraulicPending ? "hydraulic-pending" : "comparable"}" data-selected-for-review="${selectedForReview}">${selectedForReview ? `<div class="callout is-success">${icon("clipboard-check")}<div><strong>Representante encaminhado</strong>Selecionado para revisão técnica em ${escapeHtml(formatDate(scenario.selected_at, true))}. Esta decisão não autoriza guiamento de máquinas.</div></div>` : geometryIneligible ? `<div class="callout is-danger">${icon("ban")}<div><strong>Diagnóstico não elegível</strong>Falhou em gates geométricos desta rodada${blockerText ? `: ${escapeHtml(blockerText)}` : ""}. Não pode ser recomendado.</div></div>` : scenarioIsRecommended(scenario) ? `<div class="callout is-success">${icon("badge-check")}<div><strong>Melhor pontuação da triagem</strong>Melhor compromisso matemático dentro dos objetivos e gates avaliados nesta rodada E0. Não representa autorização hidráulica ou de campo.</div></div>` : cf0HydraulicPending ? `<div class="callout is-warning">${icon("shield-alert")}<div><strong>Geometria CF0 aprovada</strong>A validação hidráulica permanece não confirmada. Não pode representar a rodada.</div></div>` : `<div class="callout is-info">${icon("scale")}<div><strong>Alternativa comparável</strong>Revise os trade-offs antes de selecionar.</div></div>`}${selectedForReview ? `<button class="button" style="width:100%;margin-top:12px" data-select-scenario="${escapeHtml(scenario.id)}" data-selected-state="true" type="button">${icon("clipboard-x")} Retirar da revisão</button>` : selectableForReview ? `<button class="button is-primary" style="width:100%;margin-top:12px" data-select-scenario="${escapeHtml(scenario.id)}" data-selected-state="false" type="button">${icon("clipboard-check")} Encaminhar para revisão</button>` : `<button class="button" style="width:100%;margin-top:12px" type="button" disabled aria-disabled="true">${icon("ban")} Não elegível à representação</button>`}</div></section>
    </aside>
  </div>`;
}

function formatScenarioMetric(value, suffix = "") {
  if (value === null || value === undefined || value === "") return "Não calculado";
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${numeric.toLocaleString("pt-BR", { maximumFractionDigits: 1 })}${suffix}` : "Não calculado";
}

function metricBar(label, value) {
  const numeric = Number(value);
  const available = value !== null && value !== undefined && value !== "" && Number.isFinite(numeric);
  return `<div><div class="metric-head"><span>${escapeHtml(label)}</span><strong>${available ? `${clamp(numeric)}/100` : "Não calculado"}</strong></div><div class="metric-bar"><span style="width:${available ? clamp(numeric) : 0}%"></span></div></div>`;
}

function renderComparisonTable(scenarios) {
  if (scenarios.length < 2) return `<div class="panel empty-state" style="min-height:200px"><span class="empty-icon">${icon("git-compare-arrows")}</span><h3>Escolha pelo menos dois cenários</h3><p>A comparação coloca métricas na mesma base.</p></div>`;
  const rows = [
    ["Família", "family", false],
    ["Elegibilidade geométrica", "geometry_eligible", false],
    ["Status geométrico", "status", false],
    ["Tiro médio (m)", "average_shot_m", true, "max"],
    ["Tiro P95 (m)", "p95_shot_m", true, "max"],
    ["Manobras por ha", "maneuvers_per_ha", true, "min"],
    ["Decliv. transversal P95 (%)", "cross_slope_p95_pct", true, "min"],
    ["Comprimento total (km)", "row_length_km", true, "max"],
    ["Conservação (0–100)", "conservation_score", true, "max"],
    ["Colheitabilidade (0–100)", "harvestability_score", true, "max"],
    ["Performance (0–100)", "operational_score", true, "max"],
  ];
  return `<div class="comparison-table-wrap"><table class="comparison-table"><thead><tr><th>Métrica</th>${scenarios.map((item) => `<th>${escapeHtml(item.code)}<small style="display:block;margin-top:3px;text-transform:none">${escapeHtml(item.name)}</small></th>`).join("")}</tr></thead><tbody>${rows.map(([label, key, numeric, preference]) => {
    const values = scenarios.map((item) => {
      if (key === "family") return item.family;
      if (key === "geometry_eligible") return scenarioIsGeometryEligible(item) ? "Elegível para comparação" : "Somente diagnóstico";
      if (key === "status") return statusLabel(item.status);
      const raw = item.metrics?.[key];
      return raw === null || raw === undefined || raw === "" || !Number.isFinite(Number(raw)) ? null : Number(raw);
    });
    const availableValues = numeric ? values.filter((value, index) => value !== null && scenarioIsGeometryEligible(scenarios[index])) : [];
    const best = availableValues.length ? (preference === "min" ? Math.min(...availableValues) : Math.max(...availableValues)) : null;
    return `<tr><td><strong>${escapeHtml(label)}</strong></td>${values.map((value, index) => {
      const eligible = scenarioIsGeometryEligible(scenarios[index]);
      return `<td data-scenario-eligible="${eligible}" class="${numeric && eligible && value !== null && value === best ? "is-best" : ""}">${numeric ? value === null ? "—" : value.toLocaleString("pt-BR", { maximumFractionDigits: 1 }) : escapeHtml(value || "—")}</td>`;
    }).join("")}</tr>`;
  }).join("")}</tbody></table></div>`;
}

function renderArtifacts(artifacts) {
  if (!artifacts.length) return `<div class="panel empty-state" style="min-height:220px"><span class="empty-icon">${icon("package-open")}</span><h3>Nenhum artefato publicado</h3><p>Consulte os logs e o manifesto da execução.</p></div>`;
  return `<div class="artifact-grid">${artifacts.map((artifact) => `<article class="artifact-card" data-product-id="${escapeHtml(artifact.product_id || artifact.artifact_type || "")}" data-artifact-format="${escapeHtml(artifact.format || "")}">${artifact.preview_url ? `<div class="artifact-preview"><img src="${safeUrl(artifact.preview_url)}" alt="Prévia de ${escapeHtml(artifact.name)}" loading="lazy" /></div>` : `<div class="artifact-preview">${icon(artifact.format === "PDF" ? "file-text" : artifact.format === "GPKG" ? "database" : "file-json")}</div>`}<div class="card-title-row"><h3>${escapeHtml(artifact.name)}</h3>${badge(artifact.status)}</div><p>${escapeHtml(artifact.format || artifact.kind)} · ${formatBytes(artifact.size)}${artifact.pages ? ` · ${artifact.pages} páginas` : ""}</p><div class="artifact-meta"><span class="mono">${escapeHtml(artifact.checksum || "sem hash")}</span><span>${formatDate(artifact.created_at)}</span></div><div class="artifact-actions">${artifact.download_url ? `<a class="button is-small" href="${safeUrl(artifact.download_url)}" target="_blank" rel="noopener" download>${icon("download")} Baixar</a>` : `<button class="button is-small" type="button" disabled aria-disabled="true">${icon("download")} Indisponível no mock</button>`}<button class="button is-small" data-review-artifact="${escapeHtml(artifact.id)}" type="button">${icon("clipboard-check")} Revisar</button></div></article>`).join("")}</div>`;
}

function bindResultActions(project, run, artifacts, scenarios) {
  document.querySelector("[data-open-run]")?.addEventListener("click", (event) => navigate(`/runs/${event.currentTarget.dataset.openRun}`));
  document.querySelectorAll("[data-scenario-tab]").forEach((button) => button.addEventListener("click", () => { state.selectedScenarioId = button.dataset.scenarioTab; renderProject(project.id, "results"); }));
  document.querySelector("[data-select-scenario]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const scenario = scenarios.find((item) => item.id === button.dataset.selectScenario);
    if (!scenario || button.disabled) return;
    const selecting = button.dataset.selectedState !== "true";
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    try {
      await api.selectScenario(run.id, scenario.id, { selected_for_review: selecting });
      showToast(
        selecting ? "Alternativa encaminhada" : "Alternativa retirada",
        selecting
          ? `${scenario.name} representa a rodada somente na revisão técnica; guiamento continua não autorizado.`
          : `${scenario.name} deixou de representar a revisão técnica.`,
      );
      await renderProject(project.id, "results");
    } catch (error) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
      showToast("Não foi possível atualizar", error.message, "error");
    }
  });
  document.querySelector("#choose-comparison")?.addEventListener("click", () => showComparisonDialog(scenarios, project.id));
  document.querySelector("#download-package")?.addEventListener("click", () => {
    const dossier = findPackageArtifact(artifacts);
    if (!dossier?.download_url) return showToast("Pacote indisponível", "A execução não publicou um dossiê ou manifesto para download.", "warning");
    window.open(dossier.download_url, "_blank", "noopener");
  });
  document.querySelectorAll("[data-review-artifact]").forEach((button) => button.addEventListener("click", () => showArtifactReviewDialog(button.dataset.reviewArtifact, artifacts.find((item) => item.id === button.dataset.reviewArtifact))));
}

function showComparisonDialog(scenarios, projectId) {
  const draft = new Set(state.compareIds);
  openDialog({
    title: "Selecionar cenários",
    eyebrow: "Comparação",
    html: `<div class="checklist">${scenarios.map((scenario) => {
      const eligible = scenarioIsGeometryEligible(scenario);
      const marker = scenarioIsRecommended(scenario)
        ? `<span class="badge is-success">Recomendado</span>`
        : !eligible ? `<span class="badge is-danger">Diagnóstico</span>` : "";
      return `<label class="check-row"><div class="check-copy"><input type="checkbox" data-compare-option="${escapeHtml(scenario.id)}" ${draft.has(scenario.id) ? "checked" : ""}><div><strong>${escapeHtml(scenario.code)} · ${escapeHtml(scenario.name)}</strong><small style="display:block;color:var(--ink-500);margin-top:2px">${escapeHtml(scenario.family)} · ${escapeHtml(statusLabel(scenario.status))}</small></div></div>${marker}</label>`;
    }).join("")}</div><div class="form-actions"><button class="button" data-close-dialog type="button">Cancelar</button><button class="button is-primary" id="apply-comparison" type="button">${icon("git-compare-arrows")} Comparar selecionados</button></div>`,
    onOpen: (root) => {
      root.querySelector("[data-close-dialog]")?.addEventListener("click", closeDialog);
      root.querySelectorAll("[data-compare-option]").forEach((input) => input.addEventListener("change", () => {
        if (input.checked && draft.size >= 4) {
          input.checked = false;
          showToast("Limite de comparação", "Selecione no máximo quatro cenários.", "warning");
        } else if (input.checked) draft.add(input.dataset.compareOption);
        else draft.delete(input.dataset.compareOption);
      }));
      root.querySelector("#apply-comparison")?.addEventListener("click", () => {
        if (draft.size < 2) return showToast("Seleção insuficiente", "Escolha pelo menos dois cenários.", "error");
        state.compareIds = [...draft];
        closeDialog();
        renderProject(projectId, "results");
      });
    },
  });
}

function showArtifactReviewDialog(artifactId, artifact) {
  openDialog({
    title: "Registrar revisão",
    eyebrow: artifact?.format || "Artefato",
    html: `<form id="review-form"><div class="callout is-info">${icon("fingerprint")}<div><strong>${escapeHtml(artifact?.name || artifactId)}</strong><span class="mono">${escapeHtml(artifact?.checksum || "checksum não informado")}</span></div></div><div class="form-grid" style="margin-top:16px"><div class="form-field"><label>Decisão</label><select class="select" name="decision"><option value="ACCEPTED_FOR_COMPARISON">Aceitar para comparação</option><option value="CHANGES_REQUESTED">Solicitar ajustes</option><option value="REJECTED">Rejeitar</option></select></div><div class="form-field"><label>Domínio</label><select class="select" name="domain"><option value="AGRONOMY">Agronomia</option><option value="HYDRAULICS">Hidráulica</option><option value="OPERATIONS">Operações</option><option value="SURVEY">Topografia</option></select></div><div class="form-field is-full"><label>Observação</label><textarea class="textarea" name="comment" required placeholder="Justificativa técnica e verificações necessárias."></textarea></div></div><div class="form-actions"><button class="button" data-close-dialog type="button">Cancelar</button><button class="button is-primary" type="submit">${icon("clipboard-check")} Registrar</button></div></form>`,
    onOpen: (root) => {
      root.querySelector("[data-close-dialog]")?.addEventListener("click", closeDialog);
      root.querySelector("form")?.addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
          await api.reviewArtifact(artifactId, Object.fromEntries(new FormData(event.currentTarget).entries()));
          closeDialog();
          showToast("Revisão registrada", "A decisão foi vinculada ao hash deste artefato.");
        } catch (error) {
          showToast("Falha ao registrar", error.message, "error");
        }
      });
    },
  });
}

async function renderRuns() {
  setActiveNav("runs");
  setBreadcrumb(["Execuções"]);
  setLoading("Carregando execuções...");
  try {
    const runs = await api.listRuns();
    main.innerHTML = `<div class="page-head"><div><span class="eyebrow">Processamento</span><h1>Execuções</h1><p>Acompanhe filas, progresso, verificações, falhas e fronteiras de entrega.</p></div></div><div class="stats-grid">${statBlock("Na fila", runs.filter((item) => item.status === "QUEUED").length, "list-ordered", "Aguardando worker")}${statBlock("Em execução", runs.filter((item) => ["RUNNING", "VERIFYING"].includes(item.status)).length, "loader-circle", "Processamento ativo")}${statBlock("Concluídas", runs.filter((item) => item.status === "SUCCEEDED").length, "circle-check", "Contrato do estágio aprovado")}${statBlock("Falhas", runs.filter((item) => ["FAILED", "BLOCKED"].includes(item.status)).length, "circle-x", "Exigem ação")}</div><section class="section"><div class="section-head"><div><h2>Histórico</h2><p>${runs.length} rodada(s) localizadas.</p></div></div>${runs.length ? runs.map(renderJobCard).join("") : `<div class="panel empty-state"><span class="empty-icon">${icon("activity")}</span><h3>Nenhuma execução</h3><p>Inicie uma rodada a partir de um projeto configurado.</p></div>`}</section>`;
    document.querySelectorAll("[data-run-id]").forEach((button) => button.addEventListener("click", () => navigate(`/runs/${button.dataset.runId}`)));
    hydrateIcons(main);
  } catch (error) {
    showError(error, renderRuns);
  }
}

async function renderRunDetail(runId, preserveScroll = false) {
  setActiveNav("runs");
  if (!preserveScroll) setLoading("Consultando execução...");
  try {
    const run = await api.getRun(runId);
    state.currentRun = run;
    setBreadcrumb(["Execuções", run.name]);
    main.innerHTML = `<div class="page-head"><div><span class="eyebrow mono">${escapeHtml(run.id)}</span><h1>${escapeHtml(run.name)}</h1><p>${escapeHtml(run.project_name || run.project_id)} · pedido ${escapeHtml(run.request_id || "—")}</p></div><div class="page-actions">${["QUEUED", "RUNNING", "VERIFYING"].includes(run.status) ? `<button class="button is-danger" id="cancel-run" type="button">${icon("square")} Cancelar</button>` : ""}${run.status === "SUCCEEDED" ? `<button class="button is-primary" id="open-run-results" type="button">${icon("package-check")} Ver resultados</button>` : ""}</div></div>
      <div class="stats-grid">${statBlock("Estado", statusMap[run.status]?.[0] || run.status, "activity", run.stage || "—")}${statBlock("Progresso", `${clamp(run.progress)}%`, "gauge", "Atualização automática")}${statBlock("Duração", formatDuration(run.duration_seconds), "clock-3", run.finished_at ? "Tempo total" : "Tempo decorrido")}${statBlock("Artefatos", Number(run.artifact_count || 0), "package-check", run.delivery_boundary || "Fronteira pendente")}</div>
      <div class="content-grid section">
        <div><section class="panel"><div class="panel-header"><div><h2>Progresso da execução</h2><p>${escapeHtml(run.stage || "Aguardando estágio")}</p></div>${badge(run.status)}</div><div class="panel-body"><div class="progress-line"><span>${escapeHtml(run.stage || "Processando")}</span><strong>${clamp(run.progress)}%</strong></div><div class="progress-track" style="height:9px"><span style="width:${clamp(run.progress)}%"></span></div><ul class="timeline" style="margin-top:22px">${renderRunTimeline(run)}</ul></div></section><section class="panel"><div class="panel-header"><div><h2>Logs estruturados</h2><p>Mensagens retornadas pelo worker.</p></div><button class="button is-small" id="copy-logs" type="button">${icon("copy")} Copiar</button></div><div class="panel-body"><div class="log-console" id="log-console">${(run.logs || []).map((log) => `<div class="log-line ${log.level === "WARNING" ? "is-warning" : log.level === "ERROR" ? "is-error" : ""}"><span class="log-time">${escapeHtml(log.timestamp)}</span><span class="log-level">${escapeHtml(log.level)}</span><span>${escapeHtml(log.message)}</span></div>`).join("") || `<div class="log-line"><span class="log-time">--:--:--</span><span class="log-level">INFO</span><span>Aguardando mensagens do worker.</span></div>`}</div></div></section></div>
        <aside class="content-aside"><section class="panel"><div class="panel-header"><h3>Identificação</h3></div><div class="panel-body"><ul class="summary-list"><li><span>Projeto</span><strong>${escapeHtml(run.project_name || run.project_id)}</strong></li><li><span>Início</span><strong>${formatDate(run.started_at, true)}</strong></li><li><span>Fim</span><strong>${formatDate(run.finished_at, true)}</strong></li><li><span>Pedido</span><strong class="mono">${escapeHtml(run.request_id || "—")}</strong></li></ul></div></section><section class="panel"><div class="panel-header"><h3>Fronteira</h3></div><div class="panel-body"><div class="callout ${run.status === "SUCCEEDED" ? "is-warning" : "is-info"}">${icon("shield-alert")}<div><strong>${escapeHtml(run.delivery_boundary || "Pendente")}</strong>Conclusão do job não autoriza execução de campo além deste limite.</div></div></div></section></aside>
      </div>`;
    document.querySelector("#cancel-run")?.addEventListener("click", async () => {
      if (!window.confirm("Cancelar esta execução? Produtos parciais não serão publicados.")) return;
      try {
        await api.cancelRun(run.id);
        showToast("Execução cancelada", "O pedido permanece disponível para uma nova rodada.", "warning");
        await renderRunDetail(run.id);
      } catch (error) {
        showToast("Falha ao cancelar", error.message, "error");
      }
    });
    document.querySelector("#open-run-results")?.addEventListener("click", () => navigate(`/projects/${run.project_id}/results`));
    document.querySelector("#copy-logs")?.addEventListener("click", async () => {
      const text = (run.logs || []).map((log) => `${log.timestamp} ${log.level} ${log.message}`).join("\n");
      await navigator.clipboard.writeText(text);
      showToast("Logs copiados", "O conteúdo está na área de transferência.");
    });
    hydrateIcons(main);
    scheduleRunPoll(run);
  } catch (error) {
    showError(error, () => renderRunDetail(runId));
  }
}

function renderRunTimeline(run) {
  const definitions = [
    ["Pedido validado", 5, "Entradas, hash e versões conferidos"],
    ["Dados normalizados", 20, "CRS, cobertura, topologia e QA"],
    ["Cenários gerados", 58, "Famílias elegíveis processadas"],
    ["Restrições verificadas", 82, "Gates hidráulicos e operacionais"],
    ["Produtos publicados", 100, "Artefatos, manifesto e checksums"],
  ];
  return definitions.map(([label, threshold, note]) => `<li class="${run.progress >= threshold ? "" : "is-pending"}"><strong>${escapeHtml(label)}</strong><small>${escapeHtml(note)} · ${run.progress >= threshold ? "concluído" : "pendente"}</small></li>`).join("");
}

function scheduleRunPoll(run) {
  window.clearTimeout(state.runPollTimer);
  if (!["QUEUED", "RUNNING", "VERIFYING"].includes(run.status)) return;
  state.runPollTimer = window.setTimeout(() => {
    const routeInfo = parseRoute();
    if (routeInfo.name === "run-detail" && routeInfo.runId === run.id) renderRunDetail(run.id, true);
  }, 2500);
}

function renderLibrary() {
  setActiveNav("library");
  setBreadcrumb(["Biblioteca"]);
  main.innerHTML = `<div class="page-head"><div><span class="eyebrow">Sistema</span><h1>Biblioteca técnica</h1><p>Presets, produtos e parâmetros configuráveis disponíveis para compor projetos.</p></div></div><div class="stats-grid">${statBlock("Presets", systemCatalog.presets.length, "sliders-horizontal", "Pontos de partida versionados")}${statBlock("Produtos", systemCatalog.products.length, "package-check", "Do E0 à implantação")}${statBlock("Parâmetros visíveis", systemCatalog.parameterGroups.reduce((sum, group) => sum + group.parameters.length, 0), "list-tree", "Recorte essencial da interface")}${statBlock("Evidências promovidas", 0, "shield-check", "Presets nunca viram evidência")}</div><section class="section"><div class="section-head"><div><h2>Presets do sistema</h2><p>Modelos selecionáveis para iniciar a parametrização.</p></div></div><div class="preset-grid">${systemCatalog.presets.map((preset) => `<article class="preset-card"><div class="card-title-row"><h3>${escapeHtml(preset.name)}</h3>${icon("sliders-horizontal")}</div><p>${escapeHtml(preset.description)}</p><span class="badge is-info">${escapeHtml(preset.region)} · ${escapeHtml(preset.level)}</span></article>`).join("")}</div></section><section class="section"><div class="section-head"><div><h2>Catálogo de produtos</h2><p>Disponibilidade real depende dos dados de cada projeto.</p></div></div><div class="product-grid">${systemCatalog.products.map((product) => `<article class="product-card"><div class="card-title-row"><h3>${escapeHtml(product.name)}</h3>${badge(product.available ? "AVAILABLE" : "BLOCKED")}</div><p>${escapeHtml(product.description)}</p><span class="badge is-info">${escapeHtml(product.level)}</span></article>`).join("")}</div></section>`;
  hydrateIcons(main);
}

function renderSettings() {
  setActiveNav("settings");
  setBreadcrumb(["Configurações"]);
  main.innerHTML = `<div class="page-head"><div><span class="eyebrow">Ambiente</span><h1>Configurações</h1><p>Conexão, modo de operação e informações desta estação de trabalho.</p></div></div><div class="content-grid"><div><section class="panel"><div class="panel-header"><div><h2>API TerraFlux</h2><p>Contrato central usado pela aplicação web.</p></div>${badge(api.mode === "live" ? "AVAILABLE" : "PARTIAL", api.mode === "live" ? "Conectada" : "Demonstração")}</div><div class="panel-body"><div class="form-field"><label>Base da API</label><input class="input mono" value="${escapeHtml(api.baseUrl)}" readonly /><p class="field-help">Defina <span class="mono">window.__TERRAFLUX_API_BASE__</span> antes de carregar o módulo para usar outra origem.</p></div>${api.mode === "mock" ? `<div class="callout is-warning" style="margin-top:14px">${icon("flask-conical")}<div><strong>Fallback local ativo.</strong>Os dados demonstrativos ficam somente no localStorage deste navegador e não executam motores geoespaciais.</div></div>` : `<div class="callout is-success" style="margin-top:14px">${icon("server-cog")}<div><strong>Backend conectado.</strong>Projetos e ações são persistidos pela API.</div></div>`}</div></section><section class="panel"><div class="panel-header"><h2>Sobre esta versão</h2></div><div class="panel-body"><ul class="summary-list"><li><span>Frontend</span><strong>Vanilla ES Modules</strong></li><li><span>Contrato</span><strong>/api</strong></li><li><span>Idioma</span><strong>Português (Brasil)</strong></li><li><span>Fuso</span><strong>America/Sao_Paulo</strong></li></ul></div></section></div><aside class="content-aside"><section class="panel"><div class="panel-header"><h3>Dados demonstrativos</h3></div><div class="panel-body"><p style="margin:0;color:var(--ink-600);font-size:10px;line-height:1.5">Restaure os exemplos originais e descarte projetos criados localmente.</p><button class="button is-danger" id="reset-demo" type="button" style="width:100%;margin-top:12px" ${api.mode !== "mock" ? "disabled" : ""}>${icon("rotate-ccw")} Restaurar demonstração</button></div></section></aside></div>`;
  document.querySelector("#reset-demo")?.addEventListener("click", () => {
    if (!window.confirm("Restaurar os dados demonstrativos? Projetos criados neste navegador serão removidos.")) return;
    api.resetDemo();
    showToast("Demonstração restaurada", "Os exemplos voltaram ao estado inicial.", "warning");
    navigate("/projects");
  });
  hydrateIcons(main);
}

async function route() {
  window.clearTimeout(state.runPollTimer);
  closeMobileMenu();
  const routeInfo = parseRoute();
  state.route = routeInfo;
  try {
    if (routeInfo.name === "project") await renderProject(routeInfo.projectId, routeInfo.step);
    else if (routeInfo.name === "runs") await renderRuns();
    else if (routeInfo.name === "run-detail") await renderRunDetail(routeInfo.runId);
    else if (routeInfo.name === "library") renderLibrary();
    else if (routeInfo.name === "settings") renderSettings();
    else await renderProjects();
  } finally {
    main.focus({ preventScroll: true });
  }
}

function bindGlobalEvents() {
  primaryNav?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-route]");
    if (button) navigate(`/${button.dataset.route}`);
  });
  document.querySelector(".sidebar-foot [data-route]")?.addEventListener("click", (event) => navigate(`/${event.currentTarget.dataset.route}`));
  menuToggle?.addEventListener("click", () => {
    const isOpen = sidebar.classList.toggle("is-open");
    sidebarScrim.hidden = !isOpen;
    menuToggle.setAttribute("aria-expanded", String(isOpen));
  });
  sidebarScrim?.addEventListener("click", closeMobileMenu);
  document.querySelector("#dialog-close")?.addEventListener("click", closeDialog);
  dialog?.addEventListener("click", (event) => { if (event.target === dialog) closeDialog(); });
  refreshButton?.addEventListener("click", () => route());
  window.addEventListener("hashchange", route);
  window.addEventListener("online", () => showToast("Conexão restabelecida", "Atualize a página para tentar reconectar à API."));
  window.addEventListener("offline", () => showToast("Sem conexão", "A plataforma não consegue sincronizar com a API.", "warning"));
}

async function initialize() {
  bindGlobalEvents();
  api.onModeChange(updateConnectionUi);
  updateConnectionUi({ mode: "checking" });
  try {
    await api.initialize();
  } catch (error) {
    showToast("Acesso à API negado", error.message, "error", 8000);
  }
  if (api.mode === "mock") {
    showToast("Modo demonstrativo", "A API está indisponível. Os dados exibidos são exemplos locais e não executam os motores.", "warning", 8500);
  }
  hydrateIcons();
  await route();
}

initialize();
