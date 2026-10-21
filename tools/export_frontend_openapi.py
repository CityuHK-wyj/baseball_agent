"""Export the authoritative FastAPI contract for openapi-typescript and drift checks."""
import json
from app.api.main import app

print(json.dumps(app.openapi(), sort_keys=True))
