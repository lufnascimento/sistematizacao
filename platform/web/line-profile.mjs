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
