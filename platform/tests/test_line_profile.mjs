import assert from "node:assert/strict";
import { lineProfile, profileInspectionReport } from "../web/line-profile.mjs";

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

const reportSource = { run_id: "run-1", project_id: "project-1", artifact_id: "artifact-1",
  artifact_sha256: "b".repeat(64), scenario_id: "scenario-1" };
const report = profileInspectionReport({ ...diagnostic, row_id: "row-1", field_id: "field-1", guidance_authorized: true }, reportSource);
assert.equal(report.guidance_authorized, false);
assert.equal(report.hydraulic_approval, false);
assert.equal(report.summary.alert_length_m, 30);
assert.equal(report.summary.screening_status, "REFERENCE_EXCEEDED");
assert.equal(report.segments[1].signed_grade_pct, -10);
assert.equal(report.segments[2].above_reference, false);
assert.equal(report.vertices[4].chainage_m, 40);
assert.equal(report.reference.request_sha256, "a".repeat(64));
assert.equal(report.source.artifact_sha256, "b".repeat(64));
assert.equal(report.basis.display_mesh_used_for_measurements, false);
assert.equal(JSON.parse(JSON.stringify(report)).segments.length, 4);
const unassessed = profileInspectionReport(source, reportSource);
assert.equal(unassessed.summary.screening_status, "NOT_EVALUATED");
assert.equal(unassessed.summary.alert_length_m, null);
assert.equal(unassessed.alert_intervals, null);
assert.equal(unassessed.segments[0].above_reference, null);
const clear = profileInspectionReport({ ...diagnostic, profile_reference: { ...reference, grade_alert_pct: 10 } }, reportSource);
assert.equal(clear.summary.screening_status, "NO_SAMPLED_EXCEEDANCE_NOT_APPROVED");
for (const invalid of [null, {}, { ...reportSource, artifact_sha256: "bad" }, { ...reportSource, run_id: "" }]) {
  assert.equal(profileInspectionReport(diagnostic, invalid), null);
}
assert.equal(profileInspectionReport({}, reportSource), null);
assert.equal(profileInspectionReport(null, reportSource), null);
const many = { chainage_reference: source.chainage_reference, profile_reference: reference,
  source_chainages_m: Array.from({ length: 604 }, (_, i) => i * 10),
  source_elevations_m: Array.from({ length: 604 }, (_, i) => Math.floor((i + 1) / 2)) };
assert.ok(profileInspectionReport(many, reportSource).alert_intervals.length > 200, "Export must not truncate the UI interval list");
console.log("Inspection export lineage, measurements, full intervals and non-approval checks passed");
