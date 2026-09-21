import assert from "node:assert/strict";
import { selectResultRun } from "../web/result-runs.mjs";

const runs = [
  { id: "running", status: "RUNNING" },
  { id: "new", status: "SUCCEEDED" },
  { id: "old", status: "SUCCEEDED" },
  { id: "failed", status: "FAILED" },
];
assert.equal(selectResultRun(runs, null).id, "new");
assert.equal(selectResultRun(runs, "old").id, "old");
assert.equal(selectResultRun(runs, "failed").status, "FAILED");
assert.equal(selectResultRun(runs, "another-project-run"), null);
assert.equal(selectResultRun([], null), null);
assert.equal(selectResultRun([runs[0]], null).status, "RUNNING");
console.log("PASS: explicit history selection never falls back to another run");
