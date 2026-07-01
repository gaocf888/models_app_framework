#!/usr/bin/env python3
"""Apply dmcj DDL and seed SQL to remote PostgreSQL."""

from __future__ import annotations

from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent
DSN = dict(
    host="124.222.37.179",
    port=5432,
    user="postgres",
    password="1qaz@4321",
    dbname="dmcj",
)
TABLES = [
    "t_data_dxswj",
    "t_data_fcb",
    "t_data_gnss",
    "t_data_gq",
    "t_data_jyb",
    "t_data_kxsylj",
    "t_data_qxz",
]


def main() -> None:
    conn = psycopg2.connect(**DSN)
    conn.autocommit = True
    cur = conn.cursor()
    ddl = (ROOT / "dmcj_init_schema_and_seed.sql").read_text(encoding="utf-8")
    cur.execute(ddl)
    print("DDL applied")
    seed = (ROOT / "dmcj_seed_data.sql").read_text(encoding="utf-8")
    cur.execute(seed)
    print("Seed applied")
    for t in TABLES:
        cur.execute(f"SELECT COUNT(*) FROM public.{t}")
        print(f"{t}: {cur.fetchone()[0]} rows")
    cur.execute(
        """
        SELECT station_name, project_name, COUNT(*) AS cnt
        FROM public.t_data_fcb
        GROUP BY station_name, project_name
        ORDER BY station_name
        """
    )
    print("Sample fcb stations:")
    for row in cur.fetchall():
        print(" ", row)
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
