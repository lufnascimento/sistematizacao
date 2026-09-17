// Clip display lines to actual mesh triangles; never fill holes in the surface.
export class TerrainSurface {
  constructor(positions, indices) {
    this.positions = positions;
    this.indices = indices;
    this.cells = new Map();
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (let i = 0; i < positions.length; i += 3) {
      minX = Math.min(minX, positions[i]); maxX = Math.max(maxX, positions[i]);
      minY = Math.min(minY, positions[i + 1]); maxY = Math.max(maxY, positions[i + 1]);
    }
    this.origin = [minX, minY];
    this.cellSize = Math.max(Math.sqrt((maxX - minX) * (maxY - minY) / Math.max(indices.length / 3, 1)) * 2, 0.01);
    for (let triangle = 0; triangle < indices.length; triangle += 3) {
      const vertices = this.triangle(triangle);
      const xs = vertices.map(p => this.cell(p)[0]);
      const ys = vertices.map(p => this.cell(p)[1]);
      for (let x = Math.min(...xs); x <= Math.max(...xs); x++) {
        for (let y = Math.min(...ys); y <= Math.max(...ys); y++) {
          const key = `${x},${y}`;
          if (!this.cells.has(key)) this.cells.set(key, []);
          this.cells.get(key).push(triangle);
        }
      }
    }
  }

  cell(point) {
    return [Math.floor((point[0] - this.origin[0]) / this.cellSize), Math.floor((point[1] - this.origin[1]) / this.cellSize)];
  }

  triangle(index) {
    return [0, 1, 2].map(offset => {
      const vertex = this.indices[index + offset] * 3;
      return [this.positions[vertex], this.positions[vertex + 1], this.positions[vertex + 2]];
    });
  }

  weights(point, vertices) {
    const [a, b, c] = vertices;
    const denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1]);
    if (Math.abs(denominator) < 1e-12) return null;
    const u = ((b[1] - c[1]) * (point[0] - c[0]) + (c[0] - b[0]) * (point[1] - c[1])) / denominator;
    const v = ((c[1] - a[1]) * (point[0] - c[0]) + (a[0] - c[0]) * (point[1] - c[1])) / denominator;
    return [u, v, 1 - u - v];
  }

  segment(a, b) {
    const candidates = new Set();
    const steps = Math.max(1, Math.ceil(Math.max(Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1])) / this.cellSize * 2));
    for (let step = 0; step <= steps; step++) {
      const t = step / steps;
      const [x, y] = this.cell([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]);
      for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
        for (const index of this.cells.get(`${x + dx},${y + dy}`) || []) candidates.add(index);
      }
    }
    const intervals = [];
    for (const index of candidates) {
      const vertices = this.triangle(index);
      const start = this.weights(a, vertices), end = this.weights(b, vertices);
      if (!start || !end) continue;
      let low = 0, high = 1;
      for (let axis = 0; axis < 3; axis++) {
        const delta = end[axis] - start[axis];
        if (Math.abs(delta) < 1e-12) {
          if (start[axis] < -1e-10) high = -1;
        } else if (delta > 0) low = Math.max(low, -start[axis] / delta);
        else high = Math.min(high, -start[axis] / delta);
      }
      if (high - low <= 1e-10) continue;
      const point = t => {
        const weights = start.map((weight, i) => weight + (end[i] - weight) * t);
        return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t,
          weights.reduce((sum, weight, i) => sum + weight * vertices[i][2], 0)];
      };
      intervals.push({ low, high, point });
    }
    intervals.sort((a, b) => a.low - b.low || b.high - a.high);
    const pieces = [];
    let cursor = 0;
    for (const interval of intervals) {
      const low = Math.max(cursor, interval.low);
      if (interval.high - low <= 1e-10) continue;
      pieces.push([interval.point(low), interval.point(interval.high)]);
      cursor = interval.high;
    }
    return pieces;
  }

  drape(points) {
    const parts = [];
    let part = [];
    for (let i = 1; i < points.length; i++) {
      const pieces = this.segment(points[i - 1], points[i]);
      if (!pieces.length && part.length) { parts.push(part); part = []; }
      for (const [start, end] of pieces) {
        const last = part.at(-1);
        if (last && Math.hypot(last[0] - start[0], last[1] - start[1], last[2] - start[2]) > 1e-7) {
          parts.push(part); part = [];
        }
        if (!part.length) part.push(start);
        part.push(end);
      }
    }
    if (part.length) parts.push(part);
    return parts;
  }
}
