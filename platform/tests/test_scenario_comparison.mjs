import assert from "node:assert/strict";
import { bestMetricIndexes, scenarioIsGeometryEligible, scenarioMetric } from "../web/scenario-comparison.mjs";

const scenario = (value, changes = {}) => ({
  status: "E0_SCREENING_ONLY_NOT_AUTHORIZED", geometry_eligible: true,
  blocker_codes: [], project_id: "p", run_id: "r", field_ids: ["2", "1"],
  source_lineage: { request_id: "q", request_sha256: "abc" }, metrics: { score: value }, ...changes,
});
assert.deepEqual([...bestMetricIndexes([scenario(2), scenario(4)], "score", "max")], [1]);
assert.deepEqual([...bestMetricIndexes([scenario(0), scenario(4)], "score", "min")], [0]);
assert.equal(bestMetricIndexes([scenario(4)], "score", "max").size, 0);
assert.equal(bestMetricIndexes([scenario(4), scenario(6, { run_id: "other" })], "score", "max").size, 0);
assert.equal(bestMetricIndexes([scenario(4), scenario(6, { field_ids: ["1"] })], "score", "max").size, 0);
assert.equal(bestMetricIndexes([scenario(4), scenario(6, { source_lineage: {} })], "score", "max").size, 0);
assert.equal(bestMetricIndexes([scenario(4), scenario(6)], "score").size, 0);
assert.deepEqual([...bestMetricIndexes([scenario(4), scenario(4, { field_ids: ["1", "2"] })], "score", "max")], [0, 1]);
for (const status of ["", "UNKNOWN", "BLOCKED", "CONCEPT_ONLY", "CF0_PARTIAL_GEOMETRIC_SCREENING"]) {
  assert.equal(scenarioIsGeometryEligible(scenario(9, { status })), false);
}
assert.equal(scenarioIsGeometryEligible(scenario(9, { blocker_codes: ["RADIUS"] })), false);
assert.equal(scenarioIsGeometryEligible(scenario(9, { geometry_eligible: false })), false);
for (const value of [null, undefined, "", "4", false, NaN, Infinity]) {
  assert.equal(scenarioMetric(scenario(value), "score"), null);
}
assert.deepEqual([...bestMetricIndexes([scenario(2), scenario(4), scenario(999, {
  status: "CF0_PARTIAL_GEOMETRIC_SCREENING",
})], "score", "max")], [1]);
console.log("PASS: comparable scope, eligibility, missing values and non-ranking metrics");
