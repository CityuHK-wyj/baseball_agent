#!/usr/bin/env python3
"""Losslessly package M2.12 evidence and verify the unchanged frozen production."""
import gzip
import hashlib
import json
from pathlib import Path

OUT = Path("docs/audit/m212")
frozen = json.loads((OUT / "IMPLEMENTATION_FROZEN.json").read_text())
for path, expected in frozen["production_hashes"].items():
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == expected, path

for path in OUT.rglob("*-user-*.json"):
    if path.stem.rsplit("-", 1)[-1] in {"tasks", "references", "summary"}:
        continue
    raw = path.read_bytes()
    compressed = gzip.compress(raw, mtime=0)
    assert gzip.decompress(compressed) == raw
    path.with_suffix(".json.gz").write_bytes(compressed)
    path.unlink()  # Only this phase's verified, losslessly preserved trace copies.

files = {}
for path in sorted(OUT.rglob("*")):
    if not path.is_file() or path.name == "EVIDENCE_MANIFEST.json":
        continue
    data = path.read_bytes()
    record = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    if path.suffix == ".gz":
        raw = gzip.decompress(data)
        record.update(encoding="gzip", uncompressed_sha256=hashlib.sha256(raw).hexdigest(), uncompressed_bytes=len(raw))
    files[str(path.relative_to(OUT))] = record
scripts = {
    str(path): hashlib.sha256(path.read_bytes()).hexdigest()
    for pattern in ("*m212*.py",)
    for path in sorted(Path("tools").glob(pattern))
}
(OUT / "EVIDENCE_MANIFEST.json").write_text(json.dumps({
    "frozen_implementation_sha": frozen["implementation_sha"],
    "production_unchanged": True,
    "files": files,
    "evaluation_script_hashes": scripts,
}, indent=2) + "\n")
print("Verified frozen production; packaged", len(files), "evidence files.")
