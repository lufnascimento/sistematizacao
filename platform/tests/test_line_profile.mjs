import assert from "node:assert/strict";
import { lineProfile } from "../web/line-profile.mjs";

const source = { chainage_reference: "SOURCE_PROJECTED_METRIC_XY",
  source_chainages_m: [0, 10, 30], source_elevations_m: [100, 101, 99] };
const result = lineProfile(source);
assert.equal(result.length, 30);
assert.equal(result.difference, -1);
assert.equal(result.maximumGrade, 10);
assert.equal(result.minimum, 99);
assert.equal(result.maximum, 101);
for (const change of [
  { chainage_reference: "DISPLAY_MERCATOR" },
  { source_chainages_m: undefined }, { source_chainages_m: [0, 10] },
  { source_chainages_m: [1, 10, 30] }, { source_chainages_m: [0, 0, 30] },
  { source_chainages_m: [0, 10, 9] }, { source_chainages_m: [0, 10, Infinity] },
  { source_elevations_m: [100, null, 99] }, { source_elevations_m: [100, "101", 99] },
]) assert.equal(lineProfile({ ...source, ...change }), null);
assert.equal(lineProfile({ ...source, source_elevations_m: [0, 0, 0] }).maximumGrade, 0);
assert.equal(lineProfile({}), null);
console.log("Line profile checks passed");

const reference = { usage: "SCREENING_ALERT_ONLY", parameter_id: "e0.reference_alert_grade_pct",
  grade_alert_pct: 5, request_id: "req-test", request_sha256: "a".repeat(64) };
const diagnostic = { chainage_reference: source.chainage_reference,
  source_chainages_m: [0, 10, 20, 30, 40], source_elevations_m: [0, 1, 0, 0, 1], profile_reference: reference };
const alert = lineProfile(diagnostic).alert;
assert.equal(alert.length, 30);
assert.deepEqual(alert.intervals.map(i => [i.start, i.end, i.maximumGrade]), [[0, 20, 10], [30, 40, 10]]);
assert.equal(lineProfile({ ...diagnostic, profile_reference: { ...reference, grade_alert_pct: 10 } }).alert.length, 0);
assert.equal(lineProfile(source).alert, null);
for (const change of [{ grade_alert_pct: 0 }, { grade_alert_pct: "5" }, { grade_alert_pct: 101 },
  { request_sha256: "invalid" }, { request_id: "" }, { usage: "APPROVED" }]) {
  assert.equal(lineProfile({ ...diagnostic, profile_reference: { ...reference, ...change } }).alert, null);
}
console.log("Traceable grade alerts and interval merging passed");
