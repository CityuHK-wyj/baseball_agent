"""Compatibility entry points; no client creation or network access at import."""

from app.agent.orchestrator import run_all_channel_baseball_agent
from app.tools.postgres import query_local_hot_db
from app.tools.duckdb import query_local_cold_parquet
from app.tools.web_api import fetch_mlb_network_api, fetch_mlb_player_names


if __name__ == "__main__":
    raise SystemExit("The legacy agent is disabled pending the validated planning/response loop.")
