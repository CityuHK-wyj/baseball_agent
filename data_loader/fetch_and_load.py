import os
import datetime
import warnings
import psycopg2
from psycopg2.extras import execute_values
import pandas as pd
from pybaseball import statcast, playerid_reverse_lookup
from pybaseball import cache

# 🚨 一键开启本地缓存，赋予爬虫断点续传的“复活甲”
# Legacy import-time cache writes are disabled.

# 屏蔽高级 Python 3.14 环境下的 Pandas 迁移提示
warnings.simplefilter(action='ignore', category=FutureWarning)

# Canonical pitch-level columns retained from the upstream Statcast export. Order is the
# positional INSERT order for statcast_pitches (DataFrame 'pitcher'/'batter' map to the
# DDL columns pitcher_id/batter_id). This is a maintenance loader run as baseball_admin;
# the Agent runtime stays read-only and never imports this module.
PITCH_COLUMNS = [
    'game_date', 'game_pk', 'game_type', 'release_speed', 'release_spin_rate', 'pitch_type',
    'player_name', 'pitcher', 'batter', 'events', 'description', 'plate_x', 'plate_z',
    'sz_top', 'sz_bot', 'p_throws', 'stand', 'balls', 'strikes', 'zone', 'inning',
    'launch_speed', 'launch_angle', 'hit_distance_sc',
    'estimated_ba_using_speedangle', 'estimated_woba_using_speedangle'
]


def _require_admin_credentials() -> None:
    if not os.environ.get("POSTGRES_ADMIN_PASSWORD"):
        raise RuntimeError("Maintenance loader requires POSTGRES_ADMIN_PASSWORD (baseball_admin)")


def sync_player_dictionary(cursor, batter_ids):
    """
    动态维表同步：联网反查新打者 ID 对应的人类姓名，并注册到本地字典表
    """
    _require_admin_credentials()
    if not batter_ids:
        return

    cursor.execute("SELECT player_id FROM player_dictionary;")
    existing_ids = set(row[0] for row in cursor.fetchall())
    new_ids = list(set(batter_ids) - existing_ids)

    if not new_ids:
        return

    print(f"🔍 字典发现新面孔！正在联网反查 {len(new_ids)} 个新打者的真实姓名...")
    try:
        df_players = playerid_reverse_lookup(new_ids, key_type='mlbam')
        if df_players.empty:
            return

        dict_tuples = []
        for _, row_p in df_players.iterrows():
            p_id = int(row_p['key_mlbam'])
            full_name = f"{str(row_p['name_last']).capitalize()}, {str(row_p['name_first']).capitalize()}"
            dict_tuples.append((p_id, full_name))

        insert_dict_query = """
        INSERT INTO player_dictionary (player_id, player_name)
        VALUES %s ON CONFLICT (player_id) DO NOTHING;
        """
        execute_values(cursor, insert_dict_query, dict_tuples)
        print(f"💾 成功将 {len(dict_tuples)} 位新选手注册至本地字典表！")
    except Exception as e:
        print(f"⚠️ 字典表更新轻微超时/闪退，已跳过（不影响核心入库）: {e}")


def fetch_and_append_mlb_data(start_date, end_date):
    """
    一键双层数据灌录：同步更新 Pitch流水表、Player字典表，并自动衍生出 Agent 专用的 Event核心事件表
    """
    _require_admin_credentials()
    print(f"📡 正在从 MLB Statcast 抓取数据 [{start_date} 至 {end_date}]...")

    try:
        df = statcast(start_dt=start_date, end_dt=end_date)
    except Exception as e:
        print(f"❌ 抓取失败: {e}")
        return

    if df is None or df.empty:
        print(f"⚠️ 警告：该时间段内无数据！")
        return

    print(f"📊 成功从网络抓取到 {len(df)} 条原始投球数据！")

    pitch_columns = PITCH_COLUMNS

    # 清洗：剔除坐标缺失的流水记录（保障绘图），并做类型硬转换
    available_cols = [c for c in pitch_columns if c in df.columns]
    df_pitch = df[available_cols].dropna(subset=['plate_x', 'plate_z']).copy()

    df_pitch['game_date'] = pd.to_datetime(df_pitch['game_date']).dt.date
    df_pitch['game_pk'] = df_pitch['game_pk'].apply(lambda x: int(x) if pd.notnull(x) else None)
    df_pitch['batter'] = df_pitch['batter'].apply(lambda x: int(x) if pd.notnull(x) else None)
    df_pitch['pitcher'] = df_pitch['pitcher'].apply(lambda x: int(x) if pd.notnull(x) else None)
    df_pitch['zone'] = df_pitch['zone'].apply(lambda x: int(x) if pd.notnull(x) else None)
    df_pitch['inning'] = df_pitch['inning'].apply(lambda x: int(x) if pd.notnull(x) else None)

    # 强制让 DataFrame 以 Python 的 object 形式兼容数据库的 None (NULL)
    df_pitch = df_pitch.astype(object).where(pd.notnull(df_pitch), None)

    # 提取打者 ID 供字典登记
    all_batter_ids = [int(x) for x in df_pitch['batter'].dropna().unique()]

    print("🔌 正在连接 PostgreSQL 数据库...")
    conn = psycopg2.connect(
        host="127.0.0.1", database="baseball_analytics",
        user="baseball_admin", password=os.environ["POSTGRES_ADMIN_PASSWORD"], port="5433"
    )
    cursor = conn.cursor()

    # ========================== 建表建舱 ==========================
    # 1. 字典维表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS player_dictionary (player_id INT PRIMARY KEY, player_name VARCHAR(150));
    """)
    # 2. 原始流水表 (Pitch Level)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS statcast_pitches (
            game_date DATE, game_pk BIGINT, game_type VARCHAR(4), release_speed FLOAT, release_spin_rate FLOAT, pitch_type VARCHAR(10),
            player_name VARCHAR(100), pitcher_id INT, batter_id INT, events VARCHAR(100), description VARCHAR(255),
            plate_x FLOAT, plate_z FLOAT, sz_top FLOAT, sz_bot FLOAT, p_throws VARCHAR(5),
            stand VARCHAR(5), balls INT, strikes INT, zone INT, inning INT,
            launch_speed FLOAT, launch_angle FLOAT, hit_distance_sc FLOAT,
            estimated_ba_using_speedangle FLOAT, estimated_woba_using_speedangle FLOAT
        );
    """)
    # Idempotent migration for databases created before sz_top/sz_bot/p_throws/game_type.
    cursor.execute("""
        ALTER TABLE statcast_pitches
        ADD COLUMN IF NOT EXISTS sz_top FLOAT,
        ADD COLUMN IF NOT EXISTS sz_bot FLOAT,
        ADD COLUMN IF NOT EXISTS p_throws VARCHAR(5),
        ADD COLUMN IF NOT EXISTS game_type VARCHAR(4);
    """)
    # 3. 🚨 核心改动 2：为你量身定制的 Agent 战力事件表 (At-Bat / Event Level)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS batting_events (
            game_date DATE, game_pk BIGINT, batter_id INT, batter_name VARCHAR(150),
            pitcher_id INT, pitcher_name VARCHAR(150), inning INT, events VARCHAR(100),
            launch_speed FLOAT, launch_angle FLOAT, hit_distance_sc FLOAT, stand VARCHAR(5),
            pitch_type VARCHAR(20), estimated_ba_using_speedangle FLOAT, estimated_woba_using_speedangle FLOAT
        );
    """)

    # 动态维护注册字典
    sync_player_dictionary(cursor, all_batter_ids)

    # 精准局部幂等清理（删除老时间段内的数据，防止重复跑任务时记录翻倍）
    print(f"扫帚清理：正在移除本地库中 {start_date} 至 {end_date} 的旧流水与旧事件...")
    cursor.execute("DELETE FROM statcast_pitches WHERE game_date >= %s AND game_date <= %s;", (start_date, end_date))
    cursor.execute("DELETE FROM batting_events WHERE game_date >= %s AND game_date <= %s;", (start_date, end_date))

    # ========================== 1. 注入 Pitch 流水大表 ==========================
    sql_ordered_pitch = PITCH_COLUMNS
    df_pitch_ordered = df_pitch[sql_ordered_pitch]
    pitch_tuples = [tuple(x) for x in df_pitch_ordered.values]

    # Column names (not positions) keep the INSERT correct even after ALTER appends.
    pitch_target_columns = (
        'game_date', 'game_pk', 'game_type', 'release_speed', 'release_spin_rate', 'pitch_type',
        'player_name', 'pitcher_id', 'batter_id', 'events', 'description', 'plate_x',
        'plate_z', 'sz_top', 'sz_bot', 'p_throws', 'stand', 'balls', 'strikes', 'zone',
        'inning', 'launch_speed', 'launch_angle', 'hit_distance_sc',
        'estimated_ba_using_speedangle', 'estimated_woba_using_speedangle'
    )

    print(f"📥 正在向流水大表(statcast_pitches)灌入 {len(pitch_tuples)} 条一球一记记录...")
    insert_pitch_query = (
        f"INSERT INTO statcast_pitches ({', '.join(pitch_target_columns)}) VALUES %s"
    )
    execute_values(cursor, insert_pitch_query, pitch_tuples)

    # ========================== 2. 🚨 核心改动 3：动态衍生事件表 (Event Level) ==========================
    # 过滤出真正产生对决结果（events 不为空）的那最后一枪，它天生就是完美的 Event Level！
    df_event = df_pitch[df_pitch['events'].notnull()].copy()

    if not df_event.empty:
        # 为了给 batting_events 补齐人类看得懂的打者名字，我们直接从刚刚维护好的本地字典表中进行一次内存 JOIN
        print("⚡ 正在执行内存级维表关联，补齐打者人类姓名描述...")
        cursor.execute("SELECT player_id, player_name FROM player_dictionary;")
        dict_map = dict(cursor.fetchall())
        df_event['batter_name'] = df_event['batter'].map(dict_map)

        # 对齐事件表列结构
        sql_ordered_event = [
            'game_date', 'game_pk', 'batter', 'batter_name',
            'pitcher', 'player_name', 'inning', 'events',
            'launch_speed', 'launch_angle', 'hit_distance_sc', 'stand',
            'pitch_type', 'estimated_ba_using_speedangle', 'estimated_woba_using_speedangle'
        ]
        df_event_ordered = df_event[sql_ordered_event].astype(object).where(pd.notnull(df_event[sql_ordered_event]),
                                                                            None)
        event_tuples = [tuple(x) for x in df_event_ordered.values]
        event_target_columns = (
            'game_date', 'game_pk', 'batter_id', 'batter_name', 'pitcher_id',
            'pitcher_name', 'inning', 'events', 'launch_speed', 'launch_angle',
            'hit_distance_sc', 'stand', 'pitch_type', 'estimated_ba_using_speedangle',
            'estimated_woba_using_speedangle'
        )

        print(f"💥 完美派生！正在向事件大表(batting_events)灌入 {len(event_tuples)} 条一打席一记的核心博弈事件...")
        insert_event_query = (
            f"INSERT INTO batting_events ({', '.join(event_target_columns)}) VALUES %s"
        )
        execute_values(cursor, insert_event_query, event_tuples)
    else:
        print("⚠️ 提示：本次抓取的时间段内，没有产生任何出局、上垒等终结事件（events全是空值）。")

    conn.commit()
    cursor.close()
    conn.close()
    print(f"🎉 终极重构成功！数仓 [Pitch层] & [Event层] 已实现全面并联同步！\n")


if __name__ == "__main__":
    fetch_and_append_mlb_data(start_date="2024-01-01", end_date="2026-07-29")
