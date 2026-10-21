#!/usr/bin/env python3
"""Lossless evidence packaging and verification, outside frozen production."""
import argparse
import gzip
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.evaluate_m212r import OUT, freeze_check

MANIFEST = OUT / "EVIDENCE_MANIFEST.json"
RAW_ARCHIVE = Path("/tmp/m212r-raw-evidence")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify():
    freeze_check()
    manifest = json.loads(MANIFEST.read_text())
    expected = {r["path"] for r in manifest["files"]}
    actual = {str(p.relative_to(OUT)) for p in OUT.rglob("*")
              if p.is_file() and p != MANIFEST}
    assert actual == expected, "evidence file set changed"
    for record in manifest["files"]:
        data = (OUT / record["path"]).read_bytes()
        assert len(data) == record["bytes"]
        assert digest(data) == record["sha256"], record["path"]
        if "original_sha256" in record:
            original = gzip.decompress(data)
            assert len(original) == record["original_bytes"]
            assert digest(original) == record["original_sha256"], record["path"]
    return manifest


def package():
    frozen = freeze_check()
    # Keep small summaries, manifests, questions and independent references readable.
    for path in sorted(OUT.rglob("*.json")):
        if path == MANIFEST or path.stat().st_size <= 200_000:
            continue
        if path.name in {"metrics.json", "final-integration-diagnostics.json"}:
            continue
        if path.name.endswith(("-tasks.json", "-references.json", "-summary.json")):
            continue
        original = path.read_bytes()
        compressed = gzip.compress(original, compresslevel=9, mtime=0)
        assert gzip.decompress(compressed) == original
        target = Path(str(path) + ".gz")
        if target.exists():
            assert target.read_bytes() == compressed
        else:
            target.write_bytes(compressed)
        archived = RAW_ARCHIVE / path.relative_to(OUT)
        archived.parent.mkdir(parents=True, exist_ok=True)
        if archived.exists():
            assert archived.read_bytes() == original
        else:
            shutil.move(str(path), str(archived))
        # A rerun may restore a byte-identical raw copy; retain both rather than delete.
    files = []
    for path in sorted(OUT.rglob("*")):
        if not path.is_file() or path == MANIFEST:
            continue
        data = path.read_bytes()
        record = dict(path=str(path.relative_to(OUT)), bytes=len(data), sha256=digest(data))
        if path.suffix == ".gz":
            original = gzip.decompress(data)
            record.update(original_path=record["path"][:-3], original_bytes=len(original),
                          original_sha256=digest(original))
        files.append(record)
    MANIFEST.write_text(json.dumps(dict(frozen_sha=frozen["implementation_sha"],
        packaging="lossless gzip, mtime=0; all recorded failures retained", files=files), indent=2) + "\n")
    return verify()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    manifest = verify() if args.verify else package()
    print(json.dumps(dict(files=len(manifest["files"]),
        stored_bytes=sum(r["bytes"] for r in manifest["files"]),
        expanded_bytes=sum(r.get("original_bytes",r["bytes"]) for r in manifest["files"]),
        production_hashes_match=True, evidence_hashes_match=True), indent=2))


if __name__ == "__main__":
    main()
