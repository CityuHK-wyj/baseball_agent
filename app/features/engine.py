"""Derived batting snapshots and pitch-location visualizations."""

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from psycopg2.extras import execute_values

from app.config import Settings, settings
from app.tools.postgres import connect


def build_batting_snapshot(start_date: str, end_date: str, config: Settings = settings) -> int:
    """Build the legacy OPS snapshot table for an inclusive date range."""
    from app.validation.policy import deny_analytics_write
    deny_analytics_write()
    query = """
        SELECT d.player_name, COUNT(DISTINCT p.game_date) AS games,
          SUM(CASE WHEN p.events IN ('single','double','triple','home_run','field_out','force_out','double_play','fielders_choice','strikeout','strikeout_double_play','grounded_into_double_play') THEN 1 ELSE 0 END) AS at_bats,
          SUM(CASE WHEN p.events IN ('single','double','triple','home_run') THEN 1 ELSE 0 END) AS hits,
          SUM(CASE WHEN p.events = 'home_run' THEN 1 ELSE 0 END) AS home_runs,
          SUM(CASE WHEN p.events = 'walk' THEN 1 ELSE 0 END) AS walks,
          SUM(CASE WHEN p.events = 'hit_by_pitch' THEN 1 ELSE 0 END) AS hbp,
          SUM(CASE WHEN p.events = 'sac_fly' THEN 1 ELSE 0 END) AS sac_fly,
          SUM(CASE p.events WHEN 'single' THEN 1 WHEN 'double' THEN 2 WHEN 'triple' THEN 3 WHEN 'home_run' THEN 4 ELSE 0 END) AS total_bases
        FROM statcast_pitches p JOIN player_dictionary d ON p.batter_id = d.player_id
        WHERE p.game_date BETWEEN %s AND %s GROUP BY d.player_name
    """
    with connect(config) as connection, connection.cursor() as cursor:
        cursor.execute(query, (start_date, end_date))
        rows = cursor.fetchall()
        if not rows:
            return 0
        frame = pd.DataFrame(rows, columns=[item[0] for item in cursor.description])
        frame["batting_avg"] = (frame["hits"] / frame["at_bats"]).fillna(0)
        frame["on_base_pct"] = ((frame["hits"] + frame["walks"] + frame["hbp"]) / (frame["at_bats"] + frame["walks"] + frame["hbp"] + frame["sac_fly"])).fillna(0)
        frame["slugging_pct"] = (frame["total_bases"] / frame["at_bats"]).fillna(0)
        frame["ops"] = frame["on_base_pct"] + frame["slugging_pct"]
        fields = ["batting_avg", "on_base_pct", "slugging_pct", "ops"]
        frame[fields] = frame[fields].round(3)
        frame["snapshot_start"], frame["snapshot_end"] = start_date, end_date
        cursor.execute("""CREATE TABLE IF NOT EXISTS batting_stats_snapshot (
            player_name VARCHAR(150), games INT, at_bats INT, home_runs INT, hits INT, walks INT,
            batting_avg FLOAT, on_base_pct FLOAT, slugging_pct FLOAT, ops FLOAT,
            snapshot_start DATE, snapshot_end DATE,
            PRIMARY KEY (player_name, snapshot_start, snapshot_end))""")
        cursor.execute("DELETE FROM batting_stats_snapshot WHERE snapshot_start=%s AND snapshot_end=%s", (start_date, end_date))
        columns = ["player_name", "games", "at_bats", "home_runs", "hits", "walks", "batting_avg", "on_base_pct", "slugging_pct", "ops", "snapshot_start", "snapshot_end"]
        records = frame[columns].astype(object).where(frame[columns].notna(), None)
        execute_values(cursor, "INSERT INTO batting_stats_snapshot VALUES %s", [tuple(row) for row in records.values])
    return len(frame)


def generate_pitch_heatmap(
    pitcher_name: str,
    start_date: str | None = None,
    end_date: str | None = None,
    save_path: str | Path = "pitch_heatmap.png",
    config: Settings = settings,
) -> Path | None:
    """Generate the existing strike-zone KDE heatmap and return its output path."""
    from app.validation.policy import deny_analytics_write
    deny_analytics_write()
    query = "SELECT plate_x, plate_z FROM statcast_pitches WHERE player_name ILIKE %s AND plate_x IS NOT NULL AND plate_z IS NOT NULL"
    parameters = [f"%{pitcher_name}%"]
    if start_date:
        query += " AND game_date >= %s"
        parameters.append(start_date)
    if end_date:
        query += " AND game_date <= %s"
        parameters.append(end_date)
    with connect(config) as connection, connection.cursor() as cursor:
        cursor.execute(query, parameters)
        rows = cursor.fetchall()
    if not rows:
        return None
    dataframe = pd.DataFrame(rows, columns=["plate_x", "plate_z"])
    output = Path(save_path)
    plt.figure(figsize=(8, 8))
    sns.kdeplot(data=dataframe, x="plate_x", y="plate_z", cmap="Reds", fill=True, bw_adjust=0.5)
    for x in (-0.708, 0.708):
        plt.axvline(x=x, color="black", linestyle="--")
    for y in (1.5, 3.5):
        plt.axhline(y=y, color="black", linestyle="--")
    plt.xlim(-2, 2)
    plt.ylim(0, 5)
    date_info = f" ({start_date} to {end_date})" if start_date or end_date else ""
    plt.title(f"Pitch Location Heatmap\\nPlayer: {pitcher_name}{date_info}")
    plt.xlabel("Horizontal Position (ft)")
    plt.ylabel("Vertical Position (ft)")
    plt.savefig(output, bbox_inches="tight")
    plt.close()
    return output
