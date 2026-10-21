"""Backend OpenAPI must generate the checked-in frontend types verbatim."""
import os
import subprocess
from pathlib import Path


def test_openapi_client_contract_has_no_drift(tmp_path):
    root = Path(__file__).resolve().parents[1]
    openapi = tmp_path / "api.json"
    with openapi.open("w") as handle:
        subprocess.run(["python", "tools/export_frontend_openapi.py"], cwd=root,
                       env={**os.environ, "PYTHONPATH": str(root)}, stdout=handle, check=True)
    generated = tmp_path / "generated.ts"
    subprocess.run([str(root / "frontend/node_modules/.bin/openapi-typescript"), str(openapi), "-o", str(generated)],
                   cwd=root, check=True, capture_output=True)
    assert generated.read_bytes() == (root / "frontend/src/generated.ts").read_bytes(), (
        "API schema changed: run `cd frontend && npm run types:generate`, then check Zod validators/components"
    )
