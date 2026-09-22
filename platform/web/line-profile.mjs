// Never infer engineering distances from the display projection or draped mesh.
export function lineProfile(properties) {
  if (!properties || typeof properties !== "object") return null;
  const stations = properties.source_chainages_m;
  const heights = properties.source_elevations_m;
  if (properties.chainage_reference !== "SOURCE_PROJECTED_METRIC_XY" ||
      !Array.isArray(stations) || !Array.isArray(heights) ||
      stations.length < 2 || stations.length !== heights.length || stations[0] !== 0 ||
      !stations.every(Number.isFinite) || !heights.every(Number.isFinite)) return null;
  let maximumGrade = 0;
  let minimum = heights[0], maximum = heights[0];
  const segments = [];
  for (let i = 1; i < stations.length; i++) {
    const distance = stations[i] - stations[i - 1];
    if (distance <= 0) return null;
    const grade = (heights[i] - heights[i - 1]) / distance * 100;
    maximumGrade = Math.max(maximumGrade, Math.abs(grade));
    segments.push({ index: i - 1, start: stations[i - 1], end: stations[i], grade });
    minimum = Math.min(minimum, heights[i]);
    maximum = Math.max(maximum, heights[i]);
  }
  if (!Number.isFinite(maximumGrade) || !Number.isFinite(maximum - minimum)) return null;
  const reference = properties.profile_reference;
  const hasReference = reference?.usage === "SCREENING_ALERT_ONLY" &&
    reference.parameter_id === "e0.reference_alert_grade_pct" &&
    typeof reference.request_id === "string" && reference.request_id.length > 0 &&
    /^[a-f0-9]{64}$/.test(reference.request_sha256 || "") &&
    Number.isFinite(reference.grade_alert_pct) && reference.grade_alert_pct > 0 && reference.grade_alert_pct <= 100;
  const alert = hasReference ? { reference, intervals: [], length: 0 } : null;
  if (alert) for (const segment of segments) {
    if (Math.abs(segment.grade) <= reference.grade_alert_pct + 1e-9) continue;
    alert.length += segment.end - segment.start;
    const previous = alert.intervals.at(-1);
    if (previous && previous.end === segment.start) {
      previous.end = segment.end;
      previous.lastIndex = segment.index;
      previous.maximumGrade = Math.max(previous.maximumGrade, Math.abs(segment.grade));
    } else alert.intervals.push({ start: segment.start, end: segment.end,
      firstIndex: segment.index, lastIndex: segment.index, maximumGrade: Math.abs(segment.grade) });
  }
  return { stations, heights, length: stations.at(-1), minimum, maximum,
    difference: heights.at(-1) - heights[0], maximumGrade, segments, alert };
}

export function hasProfileReportSource(source) {
  return Boolean(source &&
    [source.run_id, source.project_id, source.artifact_id].every(value => typeof value === "string" && value.length > 0) &&
    typeof source.artifact_sha256 === "string" && /^[a-f0-9]{64}$/.test(source.artifact_sha256));
}

export function profileInspectionReport(properties, source) {
  if (!hasProfileReportSource(source)) return null;
  const profile = lineProfile(properties);
  if (!profile) return null;
  const reference = profile.alert?.reference || null;
  return {
    schema_version: "1.0.0", report_type: "CLIENT_PROFILE_INSPECTION", inspection_only: true,
    guidance_authorized: false, hydraulic_approval: false,
    checks_not_performed: ["HYDRAULIC_CAPACITY", "TURN_RADIUS", "ROW_SPACING", "OPERATIONAL_CROSSINGS", "MACHINE_GUIDANCE"],
    source: { run_id: source.run_id, project_id: source.project_id,
      artifact_id: source.artifact_id, artifact_sha256: source.artifact_sha256,
      hash_verification: "DECLARED_BY_RUN_MANIFEST_NOT_REHASHED_IN_BROWSER",
      row_id: properties.row_id ?? properties.line_id ?? properties.id ?? null,
      field_id: properties.field_id ?? properties.field_code ?? null,
      inspection_status: ["SCREENING", "DIAGNOSTIC"].includes(properties.inspection_status) ? properties.inspection_status : null,
      scenario_id: source.scenario_id || null, source_layer: properties.source_layer || null },
    basis: { distance: "SOURCE_PROJECTED_METRIC_XY", elevation: "SOURCE_GEOMETRY_VERTICES",
      vertical_reference: "UNSPECIFIED_SOURCE_DATUM", direction: "SOURCE_VERTEX_ORDER_NOT_FLOW_DIRECTION",
      grade: "SIGNED_ELEVATION_DIFFERENCE_DIVIDED_BY_PLAN_DISTANCE_PERCENT",
      comparison_tolerance_pct: 1e-9, display_mesh_used_for_measurements: false },
    reference,
    summary: { length_m: profile.length, minimum_elevation_m: profile.minimum,
      maximum_elevation_m: profile.maximum, final_minus_initial_elevation_m: profile.difference,
      maximum_absolute_segment_grade_pct: profile.maximumGrade,
      alert_length_m: profile.alert?.length ?? null,
      alert_interval_count: profile.alert?.intervals.length ?? null,
      screening_status: !reference ? "NOT_EVALUATED" : profile.alert.intervals.length ? "REFERENCE_EXCEEDED" : "NO_SAMPLED_EXCEEDANCE_NOT_APPROVED" },
    vertices: profile.stations.map((station, index) => ({ index, chainage_m: station, elevation_m: profile.heights[index] })),
    segments: profile.segments.map(segment => ({ index: segment.index,
      start_chainage_m: segment.start, end_chainage_m: segment.end, signed_grade_pct: segment.grade,
      above_reference: reference ? Math.abs(segment.grade) > reference.grade_alert_pct + 1e-9 : null })),
    alert_intervals: profile.alert ? profile.alert.intervals.map(interval => ({
      start_chainage_m: interval.start, end_chainage_m: interval.end,
      first_segment_index: interval.firstIndex, last_segment_index: interval.lastIndex,
      maximum_absolute_grade_pct: interval.maximumGrade,
    })) : null,
  };
}
