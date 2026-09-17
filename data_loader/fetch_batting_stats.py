import os
import warnings

import pandas as pd
import psycopg2

from psycopg2.extras import execute_values

warnings.simplefilter(
    action="ignore",
    category=FutureWarning
)


def build_batting_snapshot(
    start_date: str,
    end_date: str
):
    """
    根据 statcast_pitches + player_dictionary
    构建打者快照表 batting_stats_snapshot

    Maintenance tool run as baseball_admin; the Agent runtime stays read-only.
    """
    if not os.environ.get("POSTGRES_ADMIN_PASSWORD"):
        raise RuntimeError("Maintenance loader requires POSTGRES_ADMIN_PASSWORD")

    print(
        f"📡 正在生成打者快照 "
        f"[{start_date} ~ {end_date}]..."
    )

    conn = psycopg2.connect(
        host="127.0.0.1",
        database="baseball_analytics",
        user="baseball_admin",
        password=os.environ["POSTGRES_ADMIN_PASSWORD"],
        port="5433"
    )

    cursor = conn.cursor()

    #
    # 创建快照表
    #
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS batting_stats_snapshot (
        player_name VARCHAR(150),
        games INT,
        at_bats INT,
        home_runs INT,
        hits INT,
        walks INT,
        batting_avg FLOAT,
        on_base_pct FLOAT,
        slugging_pct FLOAT,
        ops FLOAT,
        snapshot_start DATE,
        snapshot_end DATE,
        PRIMARY KEY (
            player_name,
            snapshot_start,
            snapshot_end
        )
    );
    """)

    #
    # 聚合SQL
    #
    sql = """
    SELECT
        d.player_name,

        COUNT(
            DISTINCT p.game_date
        ) AS games,

        SUM(
            CASE
                WHEN p.events IN (
                    'single',
                    'double',
                    'triple',
                    'home_run',
                    'field_out',
                    'force_out',
                    'double_play',
                    'fielders_choice',
                    'strikeout',
                    'strikeout_double_play',
                    'grounded_into_double_play'
                )
                THEN 1
                ELSE 0
            END
        ) AS at_bats,

        SUM(
            CASE
                WHEN p.events IN (
                    'single',
                    'double',
                    'triple',
                    'home_run'
                )
                THEN 1
                ELSE 0
            END
        ) AS hits,

        SUM(
            CASE
                WHEN p.events = 'home_run'
                THEN 1
                ELSE 0
            END
        ) AS home_runs,

        SUM(
            CASE
                WHEN p.events = 'walk'
                THEN 1
                ELSE 0
            END
        ) AS walks,

        SUM(
            CASE
                WHEN p.events = 'hit_by_pitch'
                THEN 1
                ELSE 0
            END
        ) AS hbp,

        SUM(
            CASE
                WHEN p.events = 'sac_fly'
                THEN 1
                ELSE 0
            END
        ) AS sac_fly,

        SUM(
            CASE
                WHEN p.events = 'single'
                THEN 1

                WHEN p.events = 'double'
                THEN 2

                WHEN p.events = 'triple'
                THEN 3

                WHEN p.events = 'home_run'
                THEN 4

                ELSE 0
            END
        ) AS total_bases

    FROM statcast_pitches p

    INNER JOIN player_dictionary d
        ON p.batter_id = d.player_id

    WHERE p.game_date BETWEEN %s AND %s

    GROUP BY
        d.player_name

    ORDER BY
        hits DESC;
    """

    cursor.execute(
        sql,
        (start_date, end_date)
    )

    rows = cursor.fetchall()

    if not rows:
        print(
            "⚠️ 指定时间段没有数据"
        )
        cursor.close()
        conn.close()
        return

    columns = [
        desc[0]
        for desc in cursor.description
    ]

    df = pd.DataFrame(
        rows,
        columns=columns
    )

    #
    # 统计计算
    #

    df["batting_avg"] = (
        df["hits"] /
        df["at_bats"]
    ).fillna(0)

    obp_denom = (
        df["at_bats"]
        + df["walks"]
        + df["hbp"]
        + df["sac_fly"]
    )

    df["on_base_pct"] = (
        (
            df["hits"]
            + df["walks"]
            + df["hbp"]
        )
        /
        obp_denom
    ).fillna(0)

    df["slugging_pct"] = (
        df["total_bases"]
        /
        df["at_bats"]
    ).fillna(0)

    df["ops"] = (
        df["on_base_pct"]
        +
        df["slugging_pct"]
    )

    #
    # 保留三位小数
    #
    df["batting_avg"] = (
        df["batting_avg"]
        .round(3)
    )

    df["on_base_pct"] = (
        df["on_base_pct"]
        .round(3)
    )

    df["slugging_pct"] = (
        df["slugging_pct"]
        .round(3)
    )

    df["ops"] = (
        df["ops"]
        .round(3)
    )

    #
    # 快照日期
    #
    df["snapshot_start"] = start_date
    df["snapshot_end"] = end_date

    #
    # 删除旧快照
    #
    cursor.execute(
        """
        DELETE FROM batting_stats_snapshot
        WHERE snapshot_start=%s
          AND snapshot_end=%s
        """,
        (
            start_date,
            end_date
        )
    )

    #
    # 插入顺序
    #
    df = df[
        [
            "player_name",
            "games",
            "at_bats",
            "home_runs",
            "hits",
            "walks",
            "batting_avg",
            "on_base_pct",
            "slugging_pct",
            "ops",
            "snapshot_start",
            "snapshot_end"
        ]
    ]

    df = df.astype(object).where(
        pd.notnull(df),
        None
    )

    data = [
        tuple(row)
        for row in df.values
    ]

    execute_values(
        cursor,
        """
        INSERT INTO batting_stats_snapshot (
            player_name,
            games,
            at_bats,
            home_runs,
            hits,
            walks,
            batting_avg,
            on_base_pct,
            slugging_pct,
            ops,
            snapshot_start,
            snapshot_end
        )
        VALUES %s
        """,
        data
    )

    conn.commit()

    print(
        f"🎉 成功生成 "
        f"{len(df)} 位打者快照"
    )

    #
    # 输出前10名
    #
    print(
        df[
            [
                "player_name",
                "hits",
                "home_runs",
                "batting_avg",
                "ops"
            ]
        ]
        .sort_values(
            "ops",
            ascending=False
        )
        .head(10)
    )

    cursor.close()
    conn.close()


if __name__ == "__main__":

    build_batting_snapshot(
        start_date="2025-10-24",
        end_date="2025-11-01"
    )
