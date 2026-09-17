const ELIGIBLE_STATUSES = new Set([
  "E0_SCREENING_ONLY_NOT_AUTHORIZED",
  "CF0_GEOMETRIC_PASS_HYDRAULIC_UNCONFIRMED",
]);

export function scenarioIsGeometryEligible(scenario) {
  return ELIGIBLE_STATUSES.has(String(scenario?.status || "").toUpperCase())
    && scenario?.geometry_eligible !== false
    && Array.isArray(scenario?.blocker_codes ?? [])
    && (scenario?.blocker_codes?.length ?? 0) === 0;
}

export function scenarioMetric(scenario, key) {
  const value = scenario?.metrics?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function comparisonBasis(scenario) {
  // Only the complete E0 scope currently has a shared score/metric contract.
  if (!scenarioIsGeometryEligible(scenario) || scenario.status !== "E0_SCREENING_ONLY_NOT_AUTHORIZED") return null;
  const lineage = scenario.source_lineage;
  const fields = scenario.field_ids;
  if (!scenario.run_id || !scenario.project_id || !lineage?.request_id
      || !lineage?.request_sha256 || !Array.isArray(fields) || !fields.length
      || fields.some((field) => typeof field !== "string" || !field)) return null;
  return JSON.stringify([scenario.project_id, scenario.run_id, lineage.request_id,
    lineage.request_sha256, [...new Set(fields)].sort()]);
}

export function bestMetricIndexes(scenarios, key, preference) {
  if (!["min", "max"].includes(preference)) return new Set();
  const candidates = scenarios.map((scenario, index) => ({
    index, basis: comparisonBasis(scenario), value: scenarioMetric(scenario, key),
  })).filter((item) => item.basis !== null && item.value !== null);
  if (candidates.length < 2 || new Set(candidates.map((item) => item.basis)).size !== 1) return new Set();
  const best = Math[preference](...candidates.map((item) => item.value));
  return new Set(candidates.filter((item) => item.value === best).map((item) => item.index));
}
