import psycopg2
import sys

sys.stdout.reconfigure(encoding="utf-8")
conn = psycopg2.connect(
    host="124.222.37.179",
    port=5432,
    user="postgres",
    password="1qaz@4321",
    dbname="dmcj",
)
cur = conn.cursor()
for t in [
    "t_data_dxswj",
    "t_data_fcb",
    "t_data_gnss",
    "t_data_gq",
    "t_data_jyb",
    "t_data_kxsylj",
    "t_data_qxz",
]:
    cur.execute(f"SELECT COUNT(*) FROM public.{t}")
    print(f"{t}: {cur.fetchone()[0]}")
print("--- dxswj 朝阳金盏 ---")
cur.execute(
    """
    SELECT station_name, project_name, data_time, deep, elevation
    FROM public.t_data_dxswj
    WHERE station_name = '朝阳金盏监测站'
    ORDER BY data_time LIMIT 3
    """
)
for r in cur.fetchall():
    print(r)
print("--- gnss ---")
cur.execute(
    """
    SELECT station_name, gps_total_x, gps_total_y, project_name
    FROM public.t_data_gnss LIMIT 4
    """
)
for r in cur.fetchall():
    print(r)
cur.close()
conn.close()
