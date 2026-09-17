import assert from "node:assert/strict";
import { TerrainSurface } from "../web/terrain-surface.mjs";

const surface = new TerrainSurface([0, 0, 10, 1, 0, 11, 0, 1, 10, 1, 1, 11], [0, 1, 2, 1, 3, 2]);
const parts = surface.drape([[-1, .5], [2, .5]]);
assert.equal(parts.length, 1);
assert.deepEqual(parts[0][0], [0, .5, 10]);
assert.deepEqual(parts[0].at(-1), [1, .5, 11]);
assert.equal(surface.drape([[-1, -1], [-1, 2]]).length, 0);

const gap = new TerrainSurface([0, 0, 10, 1, 0, 10, 0, 1, 10, 1, 1, 10, 2, 0, 20, 3, 0, 20, 2, 1, 20, 3, 1, 20], [0, 1, 2, 1, 3, 2, 4, 5, 6, 5, 7, 6]);
const crossing = gap.drape([[-1, .5], [4, .5]]);
assert.equal(crossing.length, 2);
assert.equal(crossing[0].at(-1)[0], 1);
assert.equal(crossing[1][0][0], 2);
assert.equal(crossing[0][0][2], 10);
assert.equal(crossing[1][0][2], 20);
const reverse = gap.drape([[4, .5], [-1, .5]]);
assert.equal(reverse.length, 2);
assert.ok(reverse[0][0][0] > reverse[1][0][0]);
assert.equal(surface.drape([[.5, .5], [.5, .5]]).length, 1);
console.log("PASS: triangle heights, clipping, source order and NoData gaps");
