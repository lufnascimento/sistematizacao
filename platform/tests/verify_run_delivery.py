"""Download a real delivery and verify every archived product against its manifest."""

import argparse
import hashlib
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8003")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from urllib.parse import quote
    url = f"{args.base_url.rstrip('/')}/api/runs/{quote(args.run_id, safe='')}/delivery"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=180) as response, args.output.open("wb") as target:
        assert response.headers.get_content_type() == "application/zip"
        shutil.copyfileobj(response, target, length=1024 * 1024)
    with zipfile.ZipFile(args.output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["run_id"] == args.run_id
        assert manifest["guidance_authorized"] is False
        assert len(manifest["artifacts"]) == manifest["artifact_count"]
        assert len(archive.namelist()) == len(set(archive.namelist()))
        total = 0
        for entry in manifest["artifacts"]:
            digest = hashlib.sha256()
            size = 0
            with archive.open(entry["archive_path"]) as source:
                while block := source.read(1024 * 1024):
                    digest.update(block)
                    size += len(block)
            assert digest.hexdigest() == entry["sha256"], entry["id"]
            assert size == entry["size_bytes"], entry["id"]
            total += size
        assert total == manifest["source_size_bytes"]
        print(json.dumps({"status": "PASS", "run_id": args.run_id,
                          "artifact_count": manifest["artifact_count"], "source_bytes": total,
                          "zip_bytes": args.output.stat().st_size, "output": str(args.output)}))


if __name__ == "__main__":
    main()
