"""Source-vertex screening summaries, not hydraulic or operational approval."""

import math
import re


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def summarize_profile(properties):
    result = {
        "row_id": properties.get("row_id", properties.get("line_id", properties.get("id"))),
        "field_id": properties.get("field_id", properties.get("field_code")),
        "screening_status": "NOT_EVALUATED", "reason": "INVALID_SOURCE_PROFILE",
        "length_m": None, "maximum_absolute_segment_grade_pct": None,
        "alert_length_m": None, "alert_intervals": None,
    }
    stations, heights = properties.get("source_chainages_m"), properties.get("source_elevations_m")
    if (properties.get("chainage_reference") != "SOURCE_PROJECTED_METRIC_XY"
            or not isinstance(stations, list) or not isinstance(heights, list)
            or len(stations) < 2 or len(stations) != len(heights)
            or stations[0] != 0 or not all(finite(item) for item in stations + heights)):
        return result
    maximum_grade, segments = 0.0, []
    for index in range(1, len(stations)):
        distance = stations[index] - stations[index - 1]
        if distance <= 0:
            return result
        grade = abs(heights[index] - heights[index - 1]) / distance * 100
        if not math.isfinite(grade):
            return result
        maximum_grade = max(maximum_grade, grade)
        segments.append((stations[index - 1], stations[index], grade))
    if not math.isfinite(max(heights) - min(heights)):
        return result
    result.update(length_m=stations[-1], maximum_absolute_segment_grade_pct=maximum_grade,
                  reason="TRACEABLE_REFERENCE_MISSING")
    reference = properties.get("profile_reference")
    if not isinstance(reference, dict):
        return result
    threshold = reference.get("grade_alert_pct")
    if (reference.get("usage") != "SCREENING_ALERT_ONLY"
            or reference.get("parameter_id") != "e0.reference_alert_grade_pct"
            or not isinstance(reference.get("request_id"), str) or not reference["request_id"]
            or not isinstance(reference.get("request_sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", reference["request_sha256"])
            or not finite(threshold) or not 0 < threshold <= 100):
        return result
    intervals, length = [], 0.0
    for start, end, grade in segments:
        if grade <= threshold + 1e-9:
            continue
        length += end - start
        if intervals and intervals[-1]["end_chainage_m"] == start:
            intervals[-1]["end_chainage_m"] = end
            intervals[-1]["maximum_absolute_grade_pct"] = max(intervals[-1]["maximum_absolute_grade_pct"], grade)
        else:
            intervals.append({"start_chainage_m": start, "end_chainage_m": end, "maximum_absolute_grade_pct": grade})
    result.update(reason=None, alert_length_m=length, alert_intervals=intervals,
                  screening_status="REFERENCE_EXCEEDED" if intervals else "NO_SAMPLED_EXCEEDANCE_NOT_APPROVED")
    return result
