// Never infer engineering distances from the display projection or draped mesh.
export function lineProfile(properties) {
  const stations = properties.source_chainages_m;
  const heights = properties.source_elevations_m;
  if (properties.chainage_reference !== "SOURCE_PROJECTED_METRIC_XY" ||
      !Array.isArray(stations) || !Array.isArray(heights) ||
      stations.length < 2 || stations.length !== heights.length || stations[0] !== 0 ||
      !stations.every(Number.isFinite) || !heights.every(Number.isFinite)) return null;
  let maximumGrade = 0;
  let minimum = heights[0], maximum = heights[0];
  for (let i = 1; i < stations.length; i++) {
    const distance = stations[i] - stations[i - 1];
    if (distance <= 0) return null;
    maximumGrade = Math.max(maximumGrade, Math.abs(heights[i] - heights[i - 1]) / distance * 100);
    minimum = Math.min(minimum, heights[i]);
    maximum = Math.max(maximum, heights[i]);
  }
  if (!Number.isFinite(maximumGrade)) return null;
  return { stations, heights, length: stations.at(-1), minimum, maximum,
    difference: heights.at(-1) - heights[0], maximumGrade };
}
