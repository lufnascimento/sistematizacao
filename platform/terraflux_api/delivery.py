"""Build a bounded, checksum-verified delivery from one immutable run snapshot."""

import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path

from .services import canonical_sha256, utc_now
from .uploads import safe_filename


class DeliveryError(ValueError):
    pass


def build_delivery(snapshot, run_id, data_root, *, max_bytes=2 * 1024**3):
    run = snapshot["runs"].get(run_id)
    if not run or run.get("status") != "SUCCEEDED":
        raise DeliveryError("DELIVERY_REQUIRES_COMPLETED_RUN")
    project_id = run["project_id"]
    root = Path(data_root).resolve()
    run_root = (root / "projects" / project_id / "runs" / run_id).resolve()
    if root not in run_root.parents:
        raise DeliveryError("DELIVERY_INVALID_RUN_PATH")
    request = snapshot["generation_requests"].get(run.get("request_id"))
    if run.get("request_id"):
        if not request or request.get("project_id") != project_id:
            raise DeliveryError("DELIVERY_REQUEST_SCOPE_MISMATCH")
        unsigned = {key: value for key, value in request.items() if key != "sha256"}
        if canonical_sha256(unsigned) != request.get("sha256"):
            raise DeliveryError("DELIVERY_REQUEST_HASH_MISMATCH")
    artifacts = sorted((item for item in snapshot["artifacts"].values()
                        if item.get("run_id") == run_id), key=lambda item: item["id"])
    if not artifacts or len(artifacts) > 2000:
        raise DeliveryError("DELIVERY_INVALID_ARTIFACT_COUNT")
    entries = []
    total = 0
    for index, artifact in enumerate(artifacts, 1):
        if artifact.get("project_id") != project_id:
            raise DeliveryError("DELIVERY_ARTIFACT_SCOPE_MISMATCH")
        path = Path(artifact["path"]).resolve()
        if run_root not in path.parents or not path.is_file():
            raise DeliveryError("DELIVERY_ARTIFACT_OUTSIDE_RUN_OR_MISSING")
        size = artifact.get("size_bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0 or path.stat().st_size != size:
            raise DeliveryError("DELIVERY_ARTIFACT_SIZE_MISMATCH")
        total += size
        if total > max_bytes:
            raise DeliveryError("DELIVERY_SIZE_LIMIT_EXCEEDED")
        archive_path = f"products/{safe_filename(artifact.get('product_id'))}/{index:04d}_{safe_filename(artifact.get('filename'))}"
        entries.append((artifact, path, archive_path))
    scenarios = [item for item in snapshot["scenarios"].values() if item.get("run_id") == run_id]
    selections = [item for item in snapshot["scenario_selections"].values() if item.get("run_id") == run_id]
    reviews = [item for item in snapshot["artifact_reviews"].values() if item.get("run_id") == run_id]
    artifact_ids = {item["id"] for item in artifacts}
    if any(item.get("artifact_id") not in artifact_ids for item in reviews):
        raise DeliveryError("DELIVERY_REVIEW_ARTIFACT_MISMATCH")
    if any(item.get("project_id") != project_id for item in scenarios + selections + reviews):
        raise DeliveryError("DELIVERY_SCENARIO_SCOPE_MISMATCH")
    export_root = root / "exports"
    export_root.mkdir(exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="delivery-", suffix=".zip", dir=export_root)
    os.close(fd)
    output = Path(name)
    manifest = {
        "schema_version": "1.0.0", "run_id": run_id, "project_id": project_id,
        "generated_at": utc_now(), "request_id": run.get("request_id"),
        "request_sha256": request.get("sha256") if request else None,
        "authorization_boundary": "TECHNICAL_REVIEW_ONLY_NOT_MACHINE_GUIDANCE",
        "guidance_authorized": False, "original_inputs_included": False,
        "artifact_count": len(entries), "source_size_bytes": total, "artifacts": [],
    }
    try:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
            for artifact, path, archive_path in entries:
                digest = hashlib.sha256()
                copied = 0
                with path.open("rb") as source, archive.open(archive_path, "w", force_zip64=True) as target:
                    while block := source.read(1024 * 1024):
                        copied += len(block)
                        if copied > artifact["size_bytes"]:
                            raise DeliveryError("DELIVERY_ARTIFACT_CHANGED_DURING_COPY")
                        digest.update(block)
                        target.write(block)
                if copied != artifact["size_bytes"] or digest.hexdigest() != artifact.get("sha256"):
                    raise DeliveryError("DELIVERY_ARTIFACT_HASH_MISMATCH")
                manifest["artifacts"].append({
                    "id": artifact["id"], "product_id": artifact.get("product_id"),
                    "filename": artifact.get("filename"), "archive_path": archive_path,
                    "size_bytes": copied, "sha256": digest.hexdigest(),
                    "delivery_level": artifact.get("delivery_level"),
                })
            for filename, payload in (
                ("manifest.json", manifest), ("scenarios.json", scenarios),
                ("review_selections.json", selections), ("request.json", request),
                ("artifact_reviews.json", reviews),
            ):
                archive.writestr(filename, json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        return output
    except BaseException:
        output.unlink(missing_ok=True)
        raise
