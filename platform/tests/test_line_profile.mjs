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
