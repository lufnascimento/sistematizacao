from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProjectCreate(StrictModel):
    name: str = Field(min_length=2, max_length=120)
    farm_name: str | None = Field(default=None, max_length=120)
    client_name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    crs: str | None = Field(default=None, pattern=r"^EPSG:\d{4,6}$")


class ProjectUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    farm_name: str | None = Field(default=None, max_length=120)
    client_name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    crs: str | None = Field(default=None, pattern=r"^EPSG:\d{4,6}$")


class SulcationConfiguration(StrictModel):
    row_spacing_m: float = Field(default=1.5, ge=0.8, le=3.0)
    headland_width_m: float = Field(default=18.0, ge=5.0, le=100.0)
    min_turn_radius_m: float = Field(default=12.0, ge=2.0, le=100.0)
    min_shot_length_m: float = Field(default=50.0, ge=5.0, le=5000.0)
    nominal_speed_kmh: float = Field(default=5.0, gt=0.0, le=20.0)
    maneuver_time_s: float = Field(default=38.5, ge=0.0, le=600.0)
    max_cross_slope_pct: float = Field(default=12.0, ge=0.0, le=100.0)
    terrain_smoothing_radius_m: float = Field(default=5.0, ge=0.0, le=100.0)
    allow_cross_field: bool = False
    allow_cross_property: bool = False


class TopographyConfiguration(StrictModel):
    resolution_m: float = Field(default=1.0, ge=0.2, le=20.0)
    contour_interval_m: float = Field(default=1.0, ge=0.1, le=20.0)
    field_id_column: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    boundary_layer: str | None = Field(default=None, max_length=120)
    elevation_source_preference: Literal["DTM_DEM", "POINT_CLOUD"] = "DTM_DEM"


class ConservationConfiguration(StrictModel):
    scenario_families: list[Literal["E0", "CF0", "C1_TI", "C1_TD", "C2", "C3_ESD"]] = Field(
        default_factory=lambda: ["E0", "CF0", "C1_TI"]
    )
    rainfall_return_period_years: int | None = Field(default=None, ge=2, le=500)
    pce_m: float | None = Field(default=None, gt=0.0, le=100.0)
    pcx_m: float | None = Field(default=None, gt=0.0, le=2000.0)
    hydraulic_receiver_id: str | None = Field(default=None, max_length=100)


class RainfallIntervalConfiguration(StrictModel):
    duration_s: float = Field(gt=0.0, le=604800.0)
    rainfall_mm: float = Field(ge=0.0, le=1000.0)


class RoutingReachConfiguration(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    upstream_node_id: str = Field(min_length=1, max_length=100)
    downstream_node_id: str = Field(min_length=1, max_length=100)
    travel_time_minutes: float = Field(ge=0.0, le=100_000.0)
    length_m: float | None = Field(default=None, gt=0.0, le=10_000_000.0)


class ReachSectionConfiguration(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    condition_state: Literal["NEW", "CURRENT", "DEGRADED"] = "CURRENT"
    bottom_width_m: float = Field(ge=0.0, le=10_000.0)
    side_slope_h_to_v: float = Field(ge=0.0, le=100.0)
    slope_m_m: float = Field(gt=0.0, le=1.0)
    manning_n: float = Field(gt=0.0, le=1.0)
    maximum_flow_depth_m: float = Field(gt=0.0, le=1000.0)
    bankfull_depth_m: float | None = Field(default=None, gt=0.0, le=1000.0)
    required_freeboard_m: float | None = Field(default=None, ge=0.0, le=1000.0)
    overflow_path_state: Literal["NOT_DECLARED", "DECLARED_NOT_REVIEWED", "PROJECT_REVIEWED"] = "NOT_DECLARED"
    overflow_receiver_id: str | None = Field(default=None, max_length=200)
    downstream_water_depth_m: float | None = Field(default=None, ge=0.0, le=1000.0)
    downstream_velocity_m_s: float | None = Field(default=None, ge=0.0, le=100.0)
    transition_loss_coefficient: float | None = Field(default=None, ge=0.0, le=1.0)
    downstream_boundary_source_id: str | None = Field(default=None, max_length=300)
    downstream_boundary_evidence_state: Literal["SYSTEM_REFERENCE", "PROJECT_EVIDENCE"] | None = None
    maximum_admissible_velocity_m_s: float | None = Field(default=None, gt=0.0, le=100.0)
    maximum_admissible_shear_pa: float | None = Field(default=None, gt=0.0, le=1_000_000.0)
    stability_limit_source_id: str | None = Field(default=None, max_length=300)
    stability_limit_evidence_state: Literal["SYSTEM_REFERENCE", "PROJECT_EVIDENCE"] | None = None

    @model_validator(mode="after")
    def stability_limits_have_lineage(self) -> "ReachSectionConfiguration":
        has_limit = self.maximum_admissible_velocity_m_s is not None or self.maximum_admissible_shear_pa is not None
        if has_limit and (not self.stability_limit_source_id or self.stability_limit_evidence_state is None):
            raise ValueError("stability limits require source and evidence state")
        if not has_limit and (self.stability_limit_source_id or self.stability_limit_evidence_state is not None):
            raise ValueError("stability limit lineage requires at least one declared limit")
        has_freeboard = self.bankfull_depth_m is not None or self.required_freeboard_m is not None
        if has_freeboard and (self.bankfull_depth_m is None or self.required_freeboard_m is None):
            raise ValueError("bankfull depth and required freeboard must be declared together")
        if self.bankfull_depth_m is not None and self.bankfull_depth_m < self.maximum_flow_depth_m:
            raise ValueError("bankfull depth cannot be lower than maximum flow depth")
        if self.bankfull_depth_m is not None and self.maximum_flow_depth_m + self.required_freeboard_m > self.bankfull_depth_m:
            raise ValueError("maximum flow depth plus required freeboard exceeds bankfull depth")
        if self.overflow_path_state == "NOT_DECLARED" and self.overflow_receiver_id:
            raise ValueError("overflow receiver requires a declared overflow path")
        if self.overflow_path_state != "NOT_DECLARED" and not self.overflow_receiver_id:
            raise ValueError("declared overflow path requires a receiver id")
        boundary_values = (self.downstream_water_depth_m, self.downstream_velocity_m_s, self.transition_loss_coefficient)
        has_boundary = any(value is not None for value in boundary_values)
        if has_boundary and (self.downstream_water_depth_m is None or not self.downstream_boundary_source_id or self.downstream_boundary_evidence_state is None):
            raise ValueError("downstream screening requires depth, source and evidence state")
        if self.transition_loss_coefficient is not None and self.downstream_velocity_m_s is None:
            raise ValueError("transition loss coefficient requires downstream velocity")
        if not has_boundary and (self.downstream_boundary_source_id or self.downstream_boundary_evidence_state is not None):
            raise ValueError("downstream lineage requires declared boundary data")
        return self


class HydrologyScreeningConfiguration(StrictModel):
    enabled: bool = False
    method: Literal["NRCS_CURVE_NUMBER_EVENT_SCREENING"] = "NRCS_CURVE_NUMBER_EVENT_SCREENING"
    catchment_area_ha: float | None = Field(default=None, gt=0.0, le=1_000_000.0)
    curve_number: float | None = Field(default=None, gt=0.0, le=100.0)
    initial_abstraction_ratio: float = Field(default=0.2, ge=0.0, le=0.30)
    parameter_evidence_state: Literal[
        "PROJECT_EVIDENCE", "E0_ASSUMPTION", "SYNTHETIC_TEST_ONLY"
    ] = "E0_ASSUMPTION"
    parameter_source_id: str | None = Field(default=None, max_length=200)
    rainfall_intervals: list[RainfallIntervalConfiguration] = Field(
        default_factory=list, max_length=10_000
    )
    hydrograph_enabled: bool = False
    catchment_lag_minutes: float | None = Field(default=None, gt=0.0, le=100_000.0)
    hydrograph_step_minutes: float = Field(default=1.0, gt=0.0, le=10_000.0)
    triangle_base_to_peak_ratio: float = Field(default=2.67, gt=1.0, le=20.0)
    routing_enabled: bool = False
    routing_source_node_id: str | None = Field(default=None, max_length=100)
    routing_reaches: list[RoutingReachConfiguration] = Field(default_factory=list, max_length=10_000)
    capacity_enabled: bool = False
    reach_sections: list[ReachSectionConfiguration] = Field(default_factory=list, max_length=10_000)
    profile_enabled: bool = False
    profile_step_count: int = Field(default=20, ge=2, le=1000)

    @model_validator(mode="after")
    def enabled_screening_is_complete(self) -> "HydrologyScreeningConfiguration":
        if not self.enabled:
            return self
        if self.catchment_area_ha is None:
            raise ValueError("catchment_area_ha is required when hydrology screening is enabled")
        if self.curve_number is None:
            raise ValueError("curve_number is required when hydrology screening is enabled")
        if not self.rainfall_intervals:
            raise ValueError("rainfall_intervals are required when hydrology screening is enabled")
        if not self.parameter_source_id:
            raise ValueError("parameter_source_id is required when hydrology screening is enabled")
        if self.hydrograph_enabled and self.catchment_lag_minutes is None:
            raise ValueError("catchment_lag_minutes is required when hydrograph is enabled")
        if self.routing_enabled:
            if not self.hydrograph_enabled:
                raise ValueError("hydrograph must be enabled when routing is enabled")
            if not self.routing_source_node_id:
                raise ValueError("routing_source_node_id is required when routing is enabled")
            if not self.routing_reaches:
                raise ValueError("routing_reaches are required when routing is enabled")
        if self.capacity_enabled:
            if not self.routing_enabled:
                raise ValueError("routing must be enabled when capacity is enabled")
            if not self.reach_sections:
                raise ValueError("reach_sections are required when capacity is enabled")
        if self.profile_enabled:
            if not self.capacity_enabled:
                raise ValueError("capacity must be enabled when profile is enabled")
            if any(reach.length_m is None for reach in self.routing_reaches):
                raise ValueError("every routed reach requires length when profile is enabled")
            if any(section.downstream_water_depth_m is None or section.downstream_water_depth_m <= 0 for section in self.reach_sections):
                raise ValueError("every section state requires downstream depth when profile is enabled")
        return self


class ObjectiveConfiguration(StrictModel):
    soil_conservation_weight: float = Field(default=45.0, ge=0.0, le=100.0)
    harvestability_weight: float = Field(default=35.0, ge=0.0, le=100.0)
    performance_weight: float = Field(default=20.0, ge=0.0, le=100.0)

    @field_validator("performance_weight")
    @classmethod
    def weights_total_one_hundred(cls, value: float, info) -> float:
        values = info.data
        total = float(values.get("soil_conservation_weight", 0)) + float(values.get("harvestability_weight", 0)) + value
        if abs(total - 100.0) > 1e-6:
            raise ValueError("objective weights must total 100")
        return value


class ConstraintConfiguration(StrictModel):
    power_network_state: Literal["UNKNOWN", "DECLARED_NONE", "UPLOADED"] = "UNKNOWN"
    power_line_buffer_m: float = Field(default=15.0, ge=0.0, le=200.0)
    general_review_status: Literal["NOT_REVIEWED", "PARTIAL", "REVIEWED"] = "NOT_REVIEWED"


class LogisticsConfiguration(StrictModel):
    harvester_model: str | None = Field(default=None, max_length=120)
    transshipment_capacity_t: float | None = Field(default=None, gt=0.0, le=100.0)
    yield_t_ha: float | None = Field(default=None, gt=0.0, le=300.0)
    poa_enabled: bool = False


class ProjectConfiguration(StrictModel):
    system_preset_id: str = Field(default="cana_sp_equilibrio_e0", min_length=1, max_length=100)
    topography: TopographyConfiguration = Field(default_factory=TopographyConfiguration)
    sulcation: SulcationConfiguration = Field(default_factory=SulcationConfiguration)
    conservation: ConservationConfiguration = Field(default_factory=ConservationConfiguration)
    hydrology_screening: HydrologyScreeningConfiguration = Field(
        default_factory=HydrologyScreeningConfiguration
    )
    objectives: ObjectiveConfiguration = Field(default_factory=ObjectiveConfiguration)
    constraints: ConstraintConfiguration = Field(default_factory=ConstraintConfiguration)
    logistics: LogisticsConfiguration = Field(default_factory=LogisticsConfiguration)
    selected_product_ids: list[str] = Field(
        default_factory=lambda: ["TOPOGRAPHY_E0"]
    )

    @field_validator("selected_product_ids")
    @classmethod
    def unique_products(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("select at least one product")
        if len(value) != len(set(value)):
            raise ValueError("product ids must be unique")
        return value


class PresetSelectionCreate(StrictModel):
    requested_delivery_level: Literal["E0_TRIAGEM", "E1_ANTEPROJETO", "E2_EXECUTIVO", "E3_IMPLANTACAO"] = "E0_TRIAGEM"
    use_system_defaults_for_unselected_packages: bool = True
    package_selections: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    standalone_parameter_selections: list[dict[str, Any]] = Field(default_factory=list, max_length=300)
    custom_parameter_values: list[dict[str, Any]] = Field(default_factory=list, max_length=500)


class GenerationRequestCreate(StrictModel):
    name: str = Field(default="Rodada de cenarios", min_length=2, max_length=120)
    product_ids: list[str] = Field(min_length=1, max_length=20)
    delivery_level: Literal["E0_TRIAGEM", "E1_ANTEPROJETO", "E2_EXECUTIVO", "E3_IMPLANTACAO"] = "E0_TRIAGEM"
    preset_selection_id: str | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("product_ids")
    @classmethod
    def unique_products(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("product ids must be unique")
        return value


class RunCreate(StrictModel):
    engine_id: Literal[
        "validate_uploads", "project_topography", "project_pipeline_e0",
        "project_hydrology_screening", "demo_current_dataset"
    ] = "validate_uploads"
    request_id: str | None = None
    product_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("product_ids")
    @classmethod
    def unique_products(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("product ids must be unique")
        return value


class ArtifactReviewCreate(StrictModel):
    decision: Literal["ACCEPTED_FOR_COMPARISON", "CHANGES_REQUESTED", "REJECTED"]
    domain: Literal["AGRONOMY", "HYDRAULICS", "OPERATIONS", "SURVEY"]
    comment: str = Field(min_length=3, max_length=4000)


class ScenarioSelectionCreate(StrictModel):
    selected_for_review: bool = True
    selection_scope: Literal["E0_REPRESENTATIVE_FOR_TECHNICAL_REVIEW"] = (
        "E0_REPRESENTATIVE_FOR_TECHNICAL_REVIEW"
    )
    reviewer_note: str | None = Field(default=None, max_length=2000)
