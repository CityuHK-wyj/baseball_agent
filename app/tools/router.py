"""Tool registry used by the agent orchestration layer."""

from app.tools.duckdb import query_local_cold_parquet
from app.tools.postgres import query_local_hot_db
from app.tools.web_api import fetch_mlb_network_api, fetch_mlb_player_names


AVAILABLE_TOOLS = {
    "query_local_hot_db": query_local_hot_db,
    "query_local_cold_parquet": query_local_cold_parquet,
    "fetch_mlb_network_api": fetch_mlb_network_api,
    "fetch_mlb_player_names": fetch_mlb_player_names,
}
