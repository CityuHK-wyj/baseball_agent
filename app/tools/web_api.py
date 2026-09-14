"""Live pybaseball adapters migrated from ``mlb_agent.py``."""

import json

from pybaseball import batting_stats, pitching_stats, playerid_reverse_lookup


def fetch_mlb_network_api(
    action_type: str, target_year: int, player_type: str = "batting"
) -> str:
    """Fetch a compact Fangraphs leaderboard for a specified season."""
    try:
        if action_type != "season_leaderboard":
            return "?????????"
        if player_type == "batting":
            dataframe = batting_stats(target_year)
            columns = ["Name", "Age", "G", "HR", "BA", "OBP", "SLG", "OPS", "wRC+", "WAR"]
        else:
            dataframe = pitching_stats(target_year)
            columns = ["Name", "Team", "W", "L", "ERA", "SO", "WHIP", "FIP", "WAR"]
        return dataframe[columns].head(15).to_json(orient="records", force_ascii=False)
    except Exception as error:
        return json.dumps({"error": f"?? API ??????: {error}"}, ensure_ascii=False)


def fetch_mlb_player_names(player_ids: list[int]) -> str:
    """Resolve MLBAM identifiers to player names."""
    try:
        if not player_ids:
            return "[]"
        dataframe = playerid_reverse_lookup([int(player_id) for player_id in player_ids], key_type="mlbam")
        mapping = [
            {"player_id": int(row["key_mlbam"]), "name": f"{str(row['name_first']).capitalize()} {str(row['name_last']).capitalize()}"}
            for _, row in dataframe.iterrows()
        ]
        return json.dumps(mapping, ensure_ascii=False)
    except Exception as error:
        return json.dumps({"error": f"??????: {error}"}, ensure_ascii=False)
