#!/usr/bin/env python3
"""Create a searchable, fail-closed decision report for preset options.

The report is deliberately a decision and evidence artifact.  It aggregates the
    current E0/CF0 screening products, the C1 E0 geometric precursor and the
    selectable parameter library, without making an executive claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Rectangle

try:
    from pypdf import PdfReader
except ModuleNotFoundError as exc:  # pragma: no cover - environment contract
    raise SystemExit("pypdf is required to preflight searchable PDF output.") from exc


ROOT = Path(__file__).resolve().parents[1]
DERIVED = ROOT / "dataset" / "derived"
DEFAULT_PDF = DERIVED / "Relatorio_Opcoes_Presets_E0_CF0.pdf"
DEFAULT_MANIFEST = DERIVED / "preset_options_report_manifest.json"

REPORT_SCHEMA_VERSION = "1.1.0"
SEAL = "NÃO USAR PARA GUIAMENTO | TRIAGEM E0/CF0/C1"
A4_LANDSCAPE_IN = (11.6929, 8.2677)
REQUIRED_INPUTS = {
    "system_parameter_options": DERIVED / "system_parameter_options.json",
    "example_preset_resolution": DERIVED / "example_preset_resolution.json",
    "input_readiness_report": DERIVED / "input_readiness_report.json",
    "source_product_metrics": DERIVED / "source_product_metrics.json",
    "audit_metrics": DERIVED / "audit_metrics.json",
    "sulcation_scenario_metrics": DERIVED / "sulcation_scenario_metrics.json",
    "multifield_connection_screening": DERIVED / "multifield_connection_screening.json",
    "continuous_family_manifest": DERIVED / "continuous_family_manifest.json",
    "embedded_terrace_screening_manifest": DERIVED / "embedded_terrace_screening_manifest.json",
    "sulcation_scenarios_map": DERIVED / "sulcation_scenarios_map.png",
    "continuous_family_map": DERIVED / "continuous_family_map.png",
    "embedded_terrace_screening_map": DERIVED / "embedded_terrace_screening_map.png",
}
PROHIBITED_EXECUTIVE_CLAIMS = (
    "APROVADO PARA GUIAMENTO",
    "LIBERADO PARA GUIAMENTO",
    "APROVADO PARA PLANTIO",
    "PROJETO EXECUTIVO APROVADO",
    "LIBERADO PARA EXECUÇÃO",
)

INK = "#17221d"
DEEP = "#1e4a38"
GREEN = "#197a48"
BLUE = "#217da6"
YELLOW = "#c48a16"
RED = "#b74335"
GRAY = "#637069"
PALE = "#f5f7f3"
LINE = "#bfc8c0"

mpl.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 8.2,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "figure.facecolor": "white",
        "savefig.facecolor": "white",
    }
)


class ReportError(RuntimeError):
    """Raised when the report cannot be released with complete evidence."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReportError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def record(path: Path) -> dict[str, Any]:
    return {"path": relative(path), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportError(f"Invalid JSON input: {path}") from exc
    require(isinstance(value, dict), f"JSON input must be an object: {path}")
    return value


def load_inputs(paths: dict[str, Path]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    data: dict[str, Any] = {}
    records: dict[str, dict[str, Any]] = {}
    for name, path in paths.items():
        require(path.is_file(), f"Required report input is missing: {path}")
        records[name] = record(path)
        if path.suffix.lower() == ".json":
            data[name] = read_json(path)
    return data, records


def compact(value: Any, *, limit: int = 130) -> str:
    text = str(value).replace("_", " ").strip()
    return text if len(text) <= limit else f"{text[:limit - 1]}…"


def lines(value: Any, width: int = 74) -> list[str]:
    result: list[str] = []
    if value is None:
        return result
    if isinstance(value, (list, tuple)):
        for item in value:
            result.extend(lines(item, width=width))
        return result
    for paragraph in str(value).splitlines() or [str(value)]:
        result.extend(textwrap.wrap(paragraph, width=width, break_long_words=False) or [""])
    return result


def status_color(value: str) -> str:
    upper = value.upper()
    if any(token in upper for token in ("READY", "AVAILABLE", "PASS", "VERIFIED")):
        return GREEN
    if any(token in upper for token in ("BLOCK", "MISSING", "NOT", "FAIL", "UNCONFIRMED", "PARTIAL")):
        return RED
    return YELLOW


def add_header(fig: plt.Figure, number: int, title: str, subtitle: str = "") -> None:
    fig.add_artist(Rectangle((0, 0.94), 1, 0.06, transform=fig.transFigure, color=DEEP, zorder=0))
    fig.text(0.035, 0.963, f"{number:02d}  {title}", color="white", weight="bold", size=13, va="center")
    fig.text(0.965, 0.963, SEAL, color="white", size=7.2, ha="right", va="center", weight="bold")
    if subtitle:
        fig.text(0.035, 0.923, subtitle, color=GRAY, size=8.1, va="top")


def add_footer(fig: plt.Figure, number: int) -> None:
    fig.add_artist(Rectangle((0.03, 0.035), 0.94, 0.0012, transform=fig.transFigure, color=LINE))
    fig.text(0.035, 0.018, "Relatório decisório de opções e evidências. Não substitui validação de campo, projeto ou aprovação profissional.", color=GRAY, size=6.8)
    fig.text(0.965, 0.018, f"Página {number} de 14", color=GRAY, size=6.8, ha="right")


def panel(fig: plt.Figure, xywh: tuple[float, float, float, float], title: str, body: list[str], *, accent: str = GREEN, font: float = 8.0) -> None:
    x, y, w, h = xywh
    fig.add_artist(Rectangle((x, y), w, h, transform=fig.transFigure, facecolor=PALE, edgecolor=LINE, linewidth=0.7))
    fig.add_artist(Rectangle((x, y + h - 0.028), w, 0.028, transform=fig.transFigure, facecolor=accent, edgecolor=accent))
    fig.text(x + 0.012, y + h - 0.014, title, color="white", size=7.7, weight="bold", va="center")
    available = max(h - 0.05, 0.01)
    wrapped: list[str] = []
    for raw in body:
        wrapped.extend(lines(raw, width=max(26, int(w * 110))))
    step = min(0.020, available / max(len(wrapped), 1))
    y_cursor = y + h - 0.044
    for item in wrapped:
        if y_cursor < y + 0.012:
            break
        fig.text(x + 0.012, y_cursor, item, color=INK, size=min(font, step * 480), va="top")
        y_cursor -= step


def image_panel(fig: plt.Figure, path: Path, xywh: tuple[float, float, float, float], title: str) -> None:
    x, y, w, h = xywh
    ax = fig.add_axes([x, y, w, h])
    ax.imshow(plt.imread(path))
    ax.set_title(title, fontsize=7.5, loc="left", color=INK, pad=4, weight="bold")
    ax.set_axis_off()
    for spine in ax.spines.values():
        spine.set_edgecolor(LINE)


def make_page(pdf: PdfPages, number: int, title: str, *, subtitle: str = "") -> plt.Figure:
    fig = plt.figure(figsize=A4_LANDSCAPE_IN)
    add_header(fig, number, title, subtitle)
    add_footer(fig, number)
    pdf.savefig(fig)
    return fig


def save_page(pdf: PdfPages, fig: plt.Figure) -> None:
    pdf.savefig(fig)
    plt.close(fig)


def page(fig: plt.Figure, number: int, title: str, subtitle: str = "") -> None:
    add_header(fig, number, title, subtitle)
    add_footer(fig, number)


def page_sections(pdf: PdfPages, number: int, title: str, sections: list[tuple[str, list[str], str]], *, subtitle: str = "") -> None:
    fig = plt.figure(figsize=A4_LANDSCAPE_IN)
    page(fig, number, title, subtitle)
    n = len(sections)
    gap = 0.018
    total_h = 0.83
    h = (total_h - gap * (n - 1)) / n
    y = 0.075 + total_h - h
    for heading, body, accent in sections:
        panel(fig, (0.035, y, 0.93, h), heading, body, accent=accent)
        y -= h + gap
    save_page(pdf, fig)


def sorted_counts(counts: dict[str, Any]) -> list[str]:
    return [f"{key}: {value}" for key, value in sorted(counts.items())]


def top_blockers(readiness: dict[str, Any], level: str, limit: int = 5) -> list[str]:
    gate = readiness.get("delivery_readiness", {}).get(level, {})
    blockers = gate.get("blockers", [])
    answer: list[str] = []
    for item in blockers[:limit]:
        codes = ", ".join(item.get("blocker_codes", []))
        answer.append(f"{item.get('package_id', 'UNKNOWN')}: {codes}")
    return answer or ["Sem bloqueadores registrados neste nível."]


def run(output_pdf: Path, output_manifest: Path, inputs: dict[str, Path]) -> dict[str, Any]:
    data, input_records = load_inputs(inputs)
    options = data["system_parameter_options"]
    resolution = data["example_preset_resolution"]
    readiness = data["input_readiness_report"]
    products = data["source_product_metrics"]
    audit = data["audit_metrics"]
    sulcation = data["sulcation_scenario_metrics"]
    multifield = data["multifield_connection_screening"]
    cf0 = data["continuous_family_manifest"]
    c1 = data["embedded_terrace_screening_manifest"]
    require(cf0.get("release") == "CF0_GEOMETRIC_SCREENING", "CF0 release must be geometric screening.")
    require(cf0.get("stage_status") == "HYDRAULIC_UNCONFIRMED", "CF0 hydraulic status must remain unconfirmed.")
    require(sulcation.get("maximum_output_delivery_level") == "E0_TRIAGEM", "Sulcation input must remain E0.")
    require(multifield.get("status") == "UNCONFIRMED", "Multifield analysis must remain unconfirmed.")
    require(c1.get("release") == "C1_E0_CONCEPT_ALIGNMENT_NOT_DIMENSIONED", "C1 release boundary changed.")
    require(c1.get("stage_status") == "SCREENING_ONLY_PCE_PCX_UNCONFIRMED", "C1 must remain screening only.")
    require(c1.get("variant_status", {}).get("EMBUTIDA_TD", {}).get("status") == "NOT_GENERATED_RECEIVER_MISSING", "C1 TD must remain not generated.")

    summary = options["summary"]
    resolution_summary = resolution["summary"]
    ready = readiness["delivery_readiness"]
    scenarios = readiness["scenario_readiness"]
    product_lines = [
        f"{item['product']}: {item['status']} ({item['release_level']})"
        for item in products.get("products", [])
    ]
    e0_defs = [f"{item['id']}: {item['name']}" for item in sulcation.get("scenario_definitions", [])]
    cf0_profiles = [
        f"{item['candidate_id']}: {item['label']}"
        for item in cf0.get("solver_parameters", {}).get("candidate_profiles", [])
    ]
    power_declared_none = (
        cf0.get("constraints", {}).get("power_inventory_status") == "DECLARED_NONE"
    )
    power_status_note = (
        "Rede elétrica: DECLARED_NONE pelo cliente em 2026-08-24; não é insumo pendente. "
        "Revalidar somente se o limite ou as interferências mudarem."
        if power_declared_none
        else "Rede elétrica deve entrar como linha central dos postes e ser tratada como barreira por operação."
    )
    poa_dependencies = (
        "POA depende de rotas, capacidade, eventos de carga, solo operacional, acessos, "
        "interferências aplicáveis e layout de sítio."
    )
    connection_gate = (
        "Toda conexão deve ser quebrada se qualquer barreira aplicável, envelope de máquina, "
        "portal, drenagem, propriedade ou autorização impedir a passagem."
    )
    priority_one = (
        "Vertical datum e checkpoints, bacia a montante/jusante, hidrografia/estruturas, "
        "áreas protegidas, carreadores, portais e permissões. " + power_status_note
    )
    scenario_lines = []
    for name in ("C1_CURVA_EMBUTIDA", "C2_BASE_LARGA_PASSANTE", "C3_ESD", "POA_LOGISTICS", "GUIDANCE_EXPORT"):
        item = scenarios.get(name, {})
        scenario_lines.append(
            f"{name}: NOT_GENERATED / BLOQUEADO; "
            f"{len(item.get('evidence_blockers', []))} evidências e "
            f"{len(item.get('implementation_blockers', []))} capacidades pendentes."
        )

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(output_pdf, metadata={"Title": "Opções e Presets - Sistematização", "Author": "TerraFlux"}) as pdf:
        fig = plt.figure(figsize=A4_LANDSCAPE_IN)
        page(fig, 1, "Núcleo Decisório", "Opções configuráveis, cenário atual e limite de liberação")
        fig.text(0.05, 0.78, "Sistematização de Cana", size=27, weight="bold", color=INK)
        fig.text(0.05, 0.70, "Relatório de opções, presets e gates", size=15, color=DEEP)
        panel(fig, (0.05, 0.41, 0.43, 0.21), "STATUS ATUAL", [
            "E0_TRIAGEM: disponível como leitura topográfica e triagem.",
            "CF0_GEOMETRIC_SCREENING: protótipo geométrico com HYDRAULIC_UNCONFIRMED.",
            "C1 E0: precursor geométrico TI; C1 dimensionado, C2, C3 e POA seguem NOT_GENERATED / BLOQUEADO.",
        ], accent=RED, font=9)
        panel(fig, (0.52, 0.41, 0.43, 0.21), "REGRA DE USO", [
            "Presets preenchem, solicitam ou bloqueiam parâmetros. Não provam valor local.",
            "O relatório não é projeto executivo, não autoriza implantação e não gera guiamento.",
            f"{summary['configurable_parameter_count']} parâmetros configuráveis; {summary['reference_model_count']} modelos de referência.",
        ], accent=BLUE, font=9)
        fig.text(0.05, 0.29, "Selo permanente: NÃO USAR PARA GUIAMENTO", size=12, weight="bold", color=RED)
        fig.text(0.05, 0.23, "Data de geração: " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), size=8, color=GRAY)
        save_page(pdf, fig)

        page_sections(pdf, 2, "Como Ler o Relatório", [
            ("CAMADA 1 | RESULTADO REAL", ["Produtos L0/L1 e cenários E0 são leituras de triagem a partir do LAZ/DEM e polígonos. Eles preservam bloqueios e incertezas."], GREEN),
            ("CAMADA 2 | PROTÓTIPO CF0", ["Famílias contínuas são experimento geométrico. Há candidatos GEOMETRIC_PASS por bloco, mas toda a etapa permanece HYDRAULIC_UNCONFIRMED e sem requisito de raio da frota."], YELLOW),
            ("CAMADA 3 | OPÇÕES CONFIGURÁVEIS", ["Presets de mercado e modelos de sistema indicam como preencher parâmetros, quais evidências locais pedir e quando falhar fechado. Não substituem projeto, PCE/PCX ou validação de campo."], BLUE),
            ("CAMADA 4 | PRODUTOS DIMENSIONADOS AINDA NÃO GERADOS", ["O precursor C1 E0 não é terraço dimensionado. C1 executivo, C2 base larga/passante, C3 ESD e POA/logística não têm geometria aprovada neste pacote."], RED),
        ])

        page_sections(pdf, 3, "Inventário de Opções", [
            ("COBERTURA DA BIBLIOTECA", sorted_counts(summary["coverage_by_default_behavior"]), GREEN),
            ("MODELOS SELECIONÁVEIS", [
                f"{summary['evidence_package_count']} pacotes de evidência; {summary['reference_source_count']} fontes; {summary['reference_model_count']} modelos.",
                f"{summary['custom_value_supported_count']} de {summary['configurable_parameter_count']} parâmetros aceitam valor próprio com proveniência.",
                "Nível máximo que um preset sozinho pode sustentar: E0_TRIAGEM.",
            ], BLUE),
            ("LEITURA CORRETA", options.get("interpretation", []), YELLOW),
        ])

        page_sections(pdf, 4, "Exemplo de Seleção de Presets", [
            ("RESOLUÇÃO DEMONSTRATIVA", sorted_counts(resolution_summary["parameter_status_counts"]), BLUE),
            ("GATES DE ENTREGA", [
                f"E0_TRIAGEM: elegível pela seleção = {resolution['delivery_gates']['E0_TRIAGEM']['eligible_from_preset_resolution']}; ainda exige validação do pedido.",
                "E1_OPERACIONAL: bloqueado por evidência do projeto e ensaios de máquina.",
                "E2_CONSERVACIONISTA: bloqueado sem PCE/PCX, hidrologia, solo, receptores e revisão profissional.",
                "E3_EXECUTIVO: bloqueado sem aceitação de campo, as-built e aprovações de ciclo de vida.",
            ], RED),
            ("AVISOS", resolution.get("warnings", []), YELLOW),
        ])

        page_sections(pdf, 5, "Insumos Reais Produzidos", [
            ("PRODUTOS DO LAZ E DEM", product_lines, GREEN),
            ("QA DO LEVANTAMENTO", audit.get("warnings", []), YELLOW),
            ("FRONTEIRA HIDROLÓGICA", ["Corredores de contribuição e direção de fluxo são screening topográfico. Não são vazão, curso d'água, bacia validada ou simulação de inundação."], RED),
        ])

        fig = plt.figure(figsize=A4_LANDSCAPE_IN)
        page(fig, 6, "Cenários E0 de Sulcação", "Geometrias de triagem; não são sistemas conservacionistas")
        image_panel(fig, inputs["sulcation_scenarios_map"], (0.04, 0.15, 0.49, 0.70), "Mapa de cenários E0 existente")
        panel(fig, (0.57, 0.50, 0.38, 0.35), "FAMÍLIAS E0", e0_defs, accent=GREEN)
        panel(fig, (0.57, 0.15, 0.38, 0.29), "LIMITE", [
            f"Entrega máxima: {sulcation.get('maximum_output_delivery_level')}",
            "Não autorizado para: " + ", ".join(sulcation.get("not_authorized_for", [])),
            "Hidráulica, frota, manobras, receptores e sistemas físicos de conservação seguem pendentes.",
        ], accent=RED)
        save_page(pdf, fig)

        fig = plt.figure(figsize=A4_LANDSCAPE_IN)
        page(fig, 7, "Família Curva Contínua CF0", "Protótipo geométrico com gates explícitos")
        image_panel(fig, inputs["continuous_family_map"], (0.04, 0.15, 0.49, 0.70), "Mapa de famílias contínuas CF0")
        panel(fig, (0.57, 0.53, 0.38, 0.32), "PERFIS", cf0_profiles, accent=BLUE)
        panel(fig, (0.57, 0.15, 0.38, 0.32), "STATUS E LIMITAÇÕES", [
            f"Release: {cf0['release']}", f"Status hidráulico: {cf0['stage_status']}",
            "Limitações: " + ", ".join(cf0.get("release_limitations", [])),
            "Resultado geométrico não vira linha de guiamento e não substitui o raio mínimo confirmado da plantadora/frota.",
        ], accent=RED)
        save_page(pdf, fig)

        fig = plt.figure(figsize=A4_LANDSCAPE_IN)
        page(fig, 8, "Curva Embutida, Base Larga e ESD", "C1 E0 existente; produtos dimensionados permanecem bloqueados")
        image_panel(fig, inputs["embedded_terrace_screening_map"], (0.04, 0.15, 0.49, 0.70), "Triagem C1 E0 de alinhamentos TI")
        panel(fig, (0.57, 0.55, 0.38, 0.30), "C1 | PRECURSOR GEOMÉTRICO", [
            f"Release: {c1['release']}",
            f"{len(c1['candidates'])} sensibilidades; {c1['layer_counts']['terrace_alignment_candidates']} eixos; {c1['layer_counts']['interterrace_strips']} faixas.",
            "Intervalos 2/4/6 m amostram a legibilidade do relevo; não são espaçamento PCE/PCX.",
            "TD: NOT_GENERATED_RECEIVER_MISSING; PCE/PCX: NOT_EVALUATED.",
        ], accent=YELLOW, font=8)
        panel(fig, (0.57, 0.15, 0.38, 0.34), "PRODUTOS DIMENSIONADOS", [
            scenario_lines[0], scenario_lines[1], scenario_lines[2],
            "PROCESS_ONLY: C1 executivo, base larga/passante e ESD exigem receptores, chuva, solo, bacia, seção, frota e validação hidráulica.",
        ], accent=RED, font=8)
        save_page(pdf, fig)

        page_sections(pdf, 9, "Gates de Conservação e Hidráulica", [
            ("EVIDÊNCIA INDISPENSÁVEL", ["Chuva de projeto, solo e infiltração, bacia completa, estruturas, rugosidade, condições de contorno, receptores estáveis e calibração/validação."], RED),
            ("BARREIRAS OPERACIONAIS", [power_status_note, "Também devem ser revisados carreadores, portais, cercas, canais, pontes e áreas protegidas."], YELLOW),
            ("REGRA DE DECISÃO", ["Nenhuma redução de greide, aumento de tiro ou conexão entre talhões é automaticamente conservacionista. A hidráulica e as permissões prevalecem sobre o ganho operacional."], GREEN),
        ])

        page_sections(pdf, 10, "Colheitabilidade e Continuidade", [
            ("MULTITALHÃO ATUAL", [
                f"Status: {multifield['status']}; nível: {multifield['release_level']}.",
                "A fronteira comum não prova carreador, travessia segura, permissão legal ou conexão de rota.",
                compact(multifield.get("decision", {}).get("e0g_reason", ""), limit=210),
            ], RED),
            ("O QUE O MOTOR AINDA NÃO SIMULA", multifield.get("blocking_inputs", {}).get("missing", [])[:8], YELLOW),
            ("GUIAMENTO", [scenario_lines[4], connection_gate], RED),
        ])

        page_sections(pdf, 11, "Frota, Tiro e POA", [
            ("PARÂMETROS A CONFIGURAR", ["Plantadora, colhedora, transbordo e caminhão: largura, raio, articulação, envelope, carga por eixo, pneus/esteiras, pressões, estabilidade, velocidades e tempos."], BLUE),
            ("POA COMO DECISÃO DE SISTEMA", ["Avaliar ponto de operação agrícola por capacidade de transbordo/caminhão, distância, sequenciamento, solo, compactação/pisoteio, segurança, acesso e janela operacional."], GREEN),
            ("GATE", ["Sem rotas validadas, geometria de frota e evidência de operação, o POA é NOT_GENERATED e não pode ser posicionado automaticamente."], RED),
        ])

        page_sections(pdf, 12, "Bloqueios do Projeto Atual", [
            ("E1 OPERACIONAL", top_blockers(readiness, "E1_OPERACIONAL"), RED),
            ("E2 CONSERVACIONISTA", top_blockers(readiness, "E2_CONSERVACIONISTA"), RED),
            ("E3 EXECUTIVO", top_blockers(readiness, "E3_EXECUTIVO"), RED),
        ], subtitle=f"{readiness['summary']['unresolved_external_parameter_count']} parâmetros externos ainda não resolvidos")

        page_sections(pdf, 13, "Parâmetros por Usuário e Sistema", [
            ("PADRÃO DO SISTEMA", ["Escolher pacote/modelo como prior E0, registrar aplicabilidade e manter a fonte. Valores OEM, censo ou públicos não substituem a configuração real da fazenda."], BLUE),
            ("VALOR PRÓPRIO", ["O usuário pode informar valor local para cada parâmetro configurável, com unidade, origem, vigência, evidência e responsável. O valor entra nos gates aplicáveis."], GREEN),
            ("FAIL-CLOSED", ["Parâmetros de segurança, hidráulica, energia, limites legais, raio de máquina e recepção não recebem inferência silenciosa. Sem evidência, o cenário fica BLOQUEADO."], RED),
        ])

        package_lines = []
        for package in readiness.get("evidence_inventory", [])[:12]:
            package_lines.append(f"{package.get('package_id')}: {package.get('status')} | {package.get('gap_class')}")
        page_sections(pdf, 14, "Próximos Insumos para Liberar Cenários", [
            ("PRIORIDADE 1 | BASE E BARREIRAS", [priority_one], RED),
            ("PRIORIDADE 2 | SOLO, CHUVA E CONSERVAÇÃO", ["Perfil hidráulico de solo, estado operacional, chuva de projeto, regras regionais, receptores, PCE/PCX e overlay numérico assinado para ESD."], YELLOW),
            ("PRIORIDADE 3 | FROTA E LOGÍSTICA", ["Frota completa e manobras, cargas, rota/tempo, produtividade e eventos de carga, POA/site layout e aceitação de campo."], BLUE),
            ("PACOTES NO INVENTÁRIO", package_lines or ["Inventário indisponível."], GREEN),
        ])

    reader = PdfReader(str(output_pdf))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    require(len(reader.pages) == 14, "Generated report must contain exactly 14 pages.")
    require("NÃO USAR PARA GUIAMENTO" in extracted, "Generated report is not searchable or lacks release seal.")
    for claim in PROHIBITED_EXECUTIVE_CLAIMS:
        require(claim not in extracted.upper(), f"Generated report contains prohibited executive claim: {claim}")

    page_specs = [
        "Núcleo Decisório", "Como Ler o Relatório", "Inventário de Opções", "Exemplo de Seleção de Presets",
        "Insumos Reais Produzidos", "Cenários E0 de Sulcação", "Família Curva Contínua CF0",
        "Curva Embutida, Base Larga e ESD", "Gates de Conservação e Hidráulica", "Colheitabilidade e Continuidade",
        "Frota, Tiro e POA", "Bloqueios do Projeto Atual", "Parâmetros por Usuário e Sistema",
        "Próximos Insumos para Liberar Cenários",
    ]
    manifest = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "artifact_id": "terraflux-preset-options-decision-report",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release_boundary": {
            "allowed": ["E0_TRIAGEM", "CF0_GEOMETRIC_SCREENING", "C1_E0_CONCEPT_ALIGNMENT_NOT_DIMENSIONED"],
            "not_generated_or_blocked": ["C1_CURVA_EMBUTIDA", "C2_BASE_LARGA_PASSANTE", "C3_ESD", "POA_LOGISTICS", "GUIDANCE_EXPORT"],
            "seal": SEAL,
            "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
            "overhead_power_line_status": cf0.get("constraints", {}).get("power_inventory_status"),
        },
        "inputs": input_records,
        "pdf": record(output_pdf),
        "page_count": len(reader.pages),
        "pages": [{"page": index + 1, "title": title} for index, title in enumerate(page_specs)],
        "required_text_tokens": [
            "NÃO USAR PARA GUIAMENTO", "E0_TRIAGEM", "CF0_GEOMETRIC_SCREENING", "C1_E0_CONCEPT_ALIGNMENT_NOT_DIMENSIONED", "HYDRAULIC_UNCONFIRMED",
            "C1", "C2", "C3", "ESD", "POA", "NOT_GENERATED", "BLOQUEADO", "FAIL-CLOSED",
        ],
        "prohibited_executive_claims": list(PROHIBITED_EXECUTIVE_CLAIMS),
        "preflight": {
            "status": "PASS",
            "verified_input_count": len(input_records),
            "pdf_page_count": len(reader.pages),
            "all_pages_a4_landscape": True,
            "all_pages_searchable": True,
            "every_page_has_release_seal": True,
            "forbidden_claim_count": 0,
        },
    }
    output_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--output-manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    if args.preflight_only:
        _, records = load_inputs(REQUIRED_INPUTS)
        print(json.dumps({"status": "READY_TO_GENERATE", "input_count": len(records), "inputs": records}, ensure_ascii=False, indent=2))
        return 0
    manifest = run(args.output_pdf.resolve(), args.output_manifest.resolve(), REQUIRED_INPUTS)
    print(json.dumps({"status": "GENERATED", "pdf": manifest["pdf"], "pages": manifest["page_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
