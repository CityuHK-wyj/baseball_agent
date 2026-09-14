import os
import warnings
from pybaseball import cache, statcast
import pandas as pd

# 1. 屏蔽 Python 3.14 高版本下的 Pandas 迁移噪音
warnings.simplefilter(action='ignore', category=FutureWarning)

# 2. 🛡️ 开启本地高速缓存，这是抓取历史长周期数据的绝对生命线！
# Legacy import-time cache writes are disabled.


def archive_mlb_history(output_dir="./parquet_archive"):
    """
    循环抓取 2015 至 2023 赛季的全联盟原始流水，并按年度压缩归档为 Parquet 文件
    """
    from app.validation.policy import deny_analytics_write
    deny_analytics_write()
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"📁 已自动创建归档目标文件夹: {output_dir}")

    # 定义我们要为未来 Agent 储备的黄金物理与博弈列
    target_columns = [
        'game_date', 'game_pk', 'release_speed', 'release_spin_rate', 'pitch_type',
        'player_name', 'pitcher', 'batter', 'events', 'description', 'plate_x', 'plate_z',
        'stand', 'balls', 'strikes', 'zone', 'inning',
        'launch_speed', 'launch_angle', 'hit_distance_sc',
        'estimated_ba_using_speedangle', 'estimated_woba_using_speedangle'
    ]

    # 循环抓取每一年（棒球常规赛通常从 4 月初持续到 11 月初）
    for year in range(2015, 2024):
        start_date = f"{year}-03-01"
        end_date = f"{year}-11-10"
        file_path = os.path.join(output_dir, f"mlb_statcast_{year}.parquet")

        # 幂等性检查：如果这一年的文件已经下好了，直接跳过，绝不重复浪费网络和时间
        if os.path.exists(file_path):
            print(f"⭐ 检查到 {year} 赛季的 Parquet 归档已存在，自动跳过。")
            continue

        print(f"\n📡 [开始同步] 正在发动大规模查询，抓取 {year} 赛季全量数据...")

        try:
            # 联网抓取
            df = statcast(start_dt=start_date, end_dt=end_date)

            if df is None or df.empty:
                print(f"⚠️ {year} 赛季未找到任何有效数据。")
                continue

            print(f"📊 {year} 赛季原始数据拉取成功：共 {len(df)} 条投球记录。")

            # 清洗转换：剔除没有轨迹坐标的死数据
            available_cols = [c for c in target_columns if c in df.columns]
            df_clean = df[available_cols].dropna(subset=['plate_x', 'plate_z']).copy()

            # 严格的数据类型固化（防止转换为 Parquet 时发生 Object 类型混乱）
            df_clean['game_date'] = pd.to_datetime(df_clean['game_date'])
            for col in ['game_pk', 'batter', 'pitcher', 'zone', 'inning']:
                if col in df_clean.columns:
                    df_clean[col] = pd.to_numeric(df_clean[col], errors='coerce').astype('Int64')

            print(f"💾 正在将 {year} 赛季数据进行高倍压缩并写入 Parquet 文件...")

            # 🚨 核心写入函数
            df_clean.to_parquet(
                file_path,
                engine='pyarrow',
                compression='snappy',
                index=False
            )

            print(f"🎉 成功！{year} 赛季归档落盘 -> {file_path}")

        except Exception as e:
            print(f"❌ 抓取 {year} 赛季时发生中断: {e}")
            print("💡 别慌！因为开启了 cache.enable()，你重新运行脚本时会秒级无缝续传。")


if __name__ == "__main__":
    archive_mlb_history()
