from __future__ import annotations

from pathlib import Path


ASSET_TYPES: dict[str, dict] = {
    "FIELD_BOUNDARY": {
        "label": "Talhoes e limites",
        "required": True,
        "extensions": [".zip", ".gpkg", ".geojson", ".json", ".shp", ".shx", ".dbf", ".prj", ".cpg"],
        "geometry": "Polygon",
    },
    "POINT_CLOUD": {
        "label": "Nuvem de pontos",
        "required_one_of_group": "ELEVATION_SOURCE",
        "extensions": [".las", ".laz"],
    },
    "ORTHOMOSAIC": {
        "label": "Ortomosaico",
        "required": False,
        "note": "Imagem de contexto; so conta como altimetria quando um MDE/MDT correspondente tambem e fornecido.",
        "extensions": [".tif", ".tiff"],
    },
    "DTM_DEM": {
        "label": "MDE ou MDT",
        "required_one_of_group": "ELEVATION_SOURCE",
        "extensions": [".tif", ".tiff"],
    },
    "POWER_NETWORK": {
        "label": "Rede eletrica e postes",
        "required": False,
        "extensions": [".zip", ".gpkg", ".geojson", ".json", ".shp", ".shx", ".dbf", ".prj", ".cpg"],
        "geometry": "LineString or Point",
    },
    "ROADS_CARRIERS": {
        "label": "Carreadores e estradas",
        "required": False,
        "extensions": [".zip", ".gpkg", ".geojson", ".json", ".shp", ".shx", ".dbf", ".prj", ".cpg"],
    },
    "OBSTACLES": {
        "label": "Obstaculos e exclusoes",
        "required": False,
        "extensions": [".zip", ".gpkg", ".geojson", ".json", ".shp", ".shx", ".dbf", ".prj", ".cpg"],
    },
    "SOIL_MAP": {
        "label": "Solos e amostragem",
        "required": False,
        "extensions": [".zip", ".gpkg", ".geojson", ".json", ".shp", ".csv", ".xlsx"],
    },
    "RAINFALL": {
        "label": "Chuva e hidrologia",
        "required": False,
        "extensions": [".csv", ".json", ".xlsx"],
    },
    "HYDROGRAPHY_RECEIVERS": {
        "label": "Hidrografia, saidas e receptores",
        "required": False,
        "extensions": [".zip", ".gpkg", ".geojson", ".json", ".shp", ".shx", ".dbf", ".prj", ".cpg"],
    },
    "FLEET_CONFIGURATION": {
        "label": "Frota e implementos",
        "required": False,
        "extensions": [".json", ".csv", ".xlsx", ".pdf"],
    },
    "TECHNICAL_EVIDENCE": {
        "label": "Laudos e evidencias tecnicas",
        "required": False,
        "extensions": [".pdf", ".csv", ".json", ".xlsx", ".txt"],
    },
}


PRODUCTS: dict[str, dict] = {
    "TOPOGRAPHY_E0": {
        "label": "Topografia E0",
        "description": "MDT, curvas, declividade, densidade e hidrologia preliminar.",
        "stage": "E0_TRIAGEM",
        "implementation": "DEMO_AVAILABLE",
        "client_engine": "project_topography",
        "required_asset_groups": ["FIELD_BOUNDARY", "ELEVATION_SOURCE"],
        "demo_artifacts": [
            "topography_map.png", "contours_map.png", "slope_map.png", "density_map.png",
            "preliminary_flow_map.png", "dtm_1m.tif", "contours_1m.gpkg",
        ],
    },
    "SULCATION_E0": {
        "label": "Cenarios de sulcacao E0",
        "description": "Familias geometricas iniciais e metricas comparativas.",
        "stage": "E0_TRIAGEM",
        "implementation": "DEMO_AVAILABLE",
        "client_engine": "project_pipeline_e0",
        "required_asset_groups": ["FIELD_BOUNDARY", "ELEVATION_SOURCE"],
        "demo_artifacts": [
            "sulcation_scenarios.gpkg", "sulcation_scenarios_map.png", "sulcation_scenario_metrics.json",
        ],
    },
    "CF0_CONTINUOUS": {
        "label": "Familia curva continua CF0",
        "description": "Orientacao axial local, continuidade e filtros geometricos.",
        "stage": "E0_TRIAGEM",
        "implementation": "DEMO_AVAILABLE",
        "client_engine": "project_pipeline_e0",
        "required_asset_groups": ["FIELD_BOUNDARY", "ELEVATION_SOURCE"],
        "demo_artifacts": [
            "continuous_family_candidates.gpkg", "continuous_family_map.png", "continuous_family_manifest.json",
            "Relatorio_Tecnico_CF0_Familias_Continuas.pdf",
        ],
    },
    "C1_EMBEDDED_SCREENING": {
        "label": "Curva embutida C1 - precursor",
        "description": "Triagem conceitual TI, ainda sem dimensionamento PCE/PCX ou hidraulico.",
        "stage": "E0_TRIAGEM",
        "implementation": "DEMO_AVAILABLE_LIMITED",
        "client_engine": "project_pipeline_e0",
        "depends_on_product_ids": ["CF0_CONTINUOUS"],
        "required_asset_groups": ["FIELD_BOUNDARY", "ELEVATION_SOURCE"],
        "limitations": [
            "CONCEPT_ONLY",
            "PCE_PCX_NOT_EVALUATED",
            "HYDRAULIC_UNCONFIRMED",
            "TD_NOT_GENERATED",
            "GUIDANCE_NOT_AUTHORIZED",
        ],
        "demo_artifacts": [
            "embedded_terrace_screening.gpkg", "embedded_terrace_screening_map.png",
            "embedded_terrace_screening_manifest.json", "Relatorio_Triagem_C1_Curva_Embutida_E0.pdf",
        ],
    },
    "C2_BROAD_BASE": {
        "label": "Base larga ou passante C2",
        "description": "Produto conservacionista dimensionado.",
        "stage": "E2_EXECUTIVO",
        "implementation": "PLANNED",
        "required_asset_groups": ["FIELD_BOUNDARY", "ELEVATION_SOURCE", "SOIL_HYDROLOGY"],
    },
    "C3_ESD": {
        "label": "ESD e canal escoadouro C3",
        "description": "Sistema com receptor, secoes e verificacao hidraulica.",
        "stage": "E2_EXECUTIVO",
        "implementation": "PLANNED",
        "required_asset_groups": ["FIELD_BOUNDARY", "ELEVATION_SOURCE", "SOIL_HYDROLOGY", "RECEIVER"],
    },
    "POA_STATIC": {
        "label": "POA e logistica estatica",
        "description": "Triagem de pontos de operacao agricola e distancias.",
        "stage": "E0_TRIAGEM",
        "implementation": "ENGINE_AVAILABLE_NOT_DEMO_PUBLISHED",
        "required_asset_groups": ["FIELD_BOUNDARY", "ROADS_CARRIERS", "FLEET"],
    },
    "COMPLETE_DOSSIER": {
        "label": "Dossie consolidado",
        "description": "Mapas, comparativos, limites e rastreabilidade dos cenarios.",
        "stage": "E0_TRIAGEM",
        "implementation": "DEMO_AVAILABLE_AUTO_GENERATED_RUN_PACKAGE",
        "auto_generated": True,
        "limitations": ["REVIEW_ONLY", "HYDRAULIC_UNCONFIRMED", "GUIDANCE_NOT_AUTHORIZED"],
        "required_asset_groups": [],
        "demo_artifacts": ["Dossie_Completo_Sistematizacao_E0_CF0_C1_Opcoes_2026-08-24.pdf"],
    },
}


ENGINE_CATALOG: dict[str, dict] = {
    "validate_uploads": {
        "label": "Validar dados enviados",
        "mode": "CLIENT_DATA",
        "executes_external_process": False,
        "description": "Verifica integridade, papeis, formatos e prontidao sem gerar geometria.",
    },
    "project_topography": {
        "label": "Gerar topografia E0 do projeto",
        "mode": "CLIENT_DATA",
        "executes_external_process": True,
        "supported_product_ids": ["TOPOGRAPHY_E0"],
        "description": "Gera MDT, declividade, relevo sombreado, curvas, mapas e densidade quando houver LAS/LAZ.",
        "delivery_boundary": "E0_TRIAGEM_NOT_GUIDANCE_AUTHORIZED",
    },
    "project_pipeline_e0": {
        "label": "Gerar cenarios E0 e familia continua CF0",
        "mode": "CLIENT_DATA",
        "executes_external_process": True,
        "supported_product_ids": ["TOPOGRAPHY_E0", "SULCATION_E0", "CF0_CONTINUOUS", "C1_EMBEDDED_SCREENING"],
        "description": "Gera a base topografica, familias geometricas E0, candidatos CF0 e a triagem conceitual C1 dependente de CF0.",
        "delivery_boundary": "E0_TRIAGEM_NOT_GUIDANCE_AUTHORIZED",
    },
    "demo_current_dataset": {
        "label": "Publicar demonstracao local controlada",
        "mode": "DEMO_CURRENT_DATASET",
        "executes_external_process": False,
        "description": "Registra somente artefatos existentes e permitidos do dataset demonstrativo.",
    },
}


def public_catalog() -> dict:
    return {
        "asset_types": [{"id": key, **value} for key, value in ASSET_TYPES.items()],
        "products": [{"id": key, **value} for key, value in PRODUCTS.items()],
        "engines": [{"id": key, **value} for key, value in ENGINE_CATALOG.items()],
    }


def demo_artifact_path(workspace_root: Path, filename: str) -> Path:
    return workspace_root / "dataset" / "derived" / filename
