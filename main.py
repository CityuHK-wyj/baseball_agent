import duckdb

con = duckdb.connect(database=':memory:')

# 🚨 这一段完全复制了 DeepSeek 刚才在终端里自动生成的最终极 SQL
final_sql = """
SELECT 
    player_name, 
    COUNT(*) AS hr_count, 
    ROUND(AVG(launch_speed)::NUMERIC, 1) AS avg_launch_speed, 
    ROUND(MAX(launch_speed)::NUMERIC, 1) AS max_launch_speed
FROM read_parquet('/home/158112/baseball_agent/baseball_agent/data_loader/parquet_archive/mlb_statcast_*.parquet')
WHERE events ILIKE '%home_run%'
  AND launch_speed IS NOT NULL
GROUP BY player_name
HAVING COUNT(*) >= 30
ORDER BY avg_launch_speed DESC
LIMIT 5
"""

df = con.execute(final_sql).df()
print("\n🔥 2015-2023 全联盟本垒打暴力美学最终前五名：")
print(df.to_string(index=False))