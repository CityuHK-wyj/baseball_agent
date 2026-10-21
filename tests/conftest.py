import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires live DB/LLM/network")


def pytest_collection_modifyitems(config, items):
    run_integration = os.environ.get("RUN_INTEGRATION") == "1"
    if run_integration:
        return
    skip = pytest.mark.skip(reason="set RUN_INTEGRATION=1 to run live integration tests")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
