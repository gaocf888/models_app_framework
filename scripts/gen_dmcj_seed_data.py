#!/usr/bin/env python3
"""Generate Beijing-themed test data INSERTs for dmcj schema tables."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path


def uid() -> str:
    return uuid.uuid4().hex


def q(s: str | None) -> str:
    if s is None:
        return "NULL"
    return "'" + s.replace("'", "''") + "'"


def n(v: float | int | None) -> str:
    if v is None:
        return "NULL"
    return str(v)


STATIONS = [
    ("40101", "朝阳金盏监测站", "BJ01(朝阳金盏)"),
    ("40102", "朝阳双桥监测站", "BJ02(朝阳双桥)"),
    ("40103", "朝阳管庄监测站", "BJ03(朝阳管庄)"),
    ("40104", "海淀清河监测站", "BJ04(海淀清河)"),
]

PROJECT_BEIJING = "北京地面沉降监测"
BASE_DATE = datetime(2026, 1, 7, 0, 0, 0)
INSERT_TIME = datetime(2026, 1, 8, 15, 2, 33)


def gen_dxswj() -> list[str]:
    rows: list[str] = []
    # 地下水：深度/标高随小时略变
    profiles = {
        "40101": (2.85, 21.12),
        "40102": (3.12, 20.86),
    }
    for sid, sname, pname in STATIONS[:2]:
        deep0, elev0 = profiles.get(sid, (2.5, 21.0))
        for h in range(24):
            dt = BASE_DATE + timedelta(hours=h)
            it = INSERT_TIME + timedelta(minutes=h)
            deep = round(deep0 + h * 0.012 + (h % 3) * 0.005, 3)
            elev = round(elev0 - h * 0.008 + (h % 4) * 0.003, 3)
            rows.append(
                f"({q(uid())},{q(uid())},{q(sid)},{q(sname)},"
                f"{q(dt.strftime('%Y-%m-%d %H:%M:%S'))},{q(it.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3])},"
                f"{n(deep)},{n(elev)},{q(pname)},NULL)"
            )
    return rows


def gen_fcb() -> list[str]:
    rows: list[str] = []
    settle_base = {"40101": 6.8, "40102": -0.32, "40103": 1.45, "40104": -2.15}
    for sid, sname, pname in STATIONS:
        base = settle_base.get(sid, 0.5)
        for h in range(12):
            dt = BASE_DATE + timedelta(hours=h * 2)
            it = INSERT_TIME + timedelta(minutes=h * 3)
            val = round(base + h * 0.05 + (h % 2) * 0.02, 3)
            rows.append(
                f"({q(uid())},{q(uid())},{q(sid)},{q(sname)},"
                f"{q(dt.strftime('%Y-%m-%d %H:%M:%S'))},{q(it.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3])},"
                f"{n(val)},{q(pname)},NULL)"
            )
    return rows


def gen_gnss() -> list[str]:
    rows: list[str] = []
    coords = {
        "40101": (10.202146, -0.146945, -12.960908),
        "40102": (-3.467933, 4.042413, -3.933503),
        "40103": (2.118456, -1.876234, -8.445621),
        "40104": (-5.332891, 1.556782, -6.221334),
    }
    for day in range(3):
        dt = datetime(2026, 1, 4 + day, 0, 0, 0)
        it = INSERT_TIME + timedelta(seconds=day * 5)
        for sid, sname, _ in STATIONS:
            x, y, z = coords[sid]
            dx = round(x + day * 0.015, 9)
            dy = round(y + day * 0.008, 9)
            dz = round(z - day * 0.012, 9)
            code = sname.replace("监测站", "-GNSS")
            rows.append(
                f"({q(uid())},{q(uid())},{q(sid)},{q(code)},"
                f"{q(dt.strftime('%Y-%m-%d %H:%M:%S'))},{q(it.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3])},"
                f"{n(dx)},{n(dy)},{n(dz)},NULL,NULL,{q(PROJECT_BEIJING)},NULL)"
            )
    return rows


def gen_gq() -> list[str]:
    rows: list[str] = []
    settle = {"40101": -1.12, "40102": 4.339}
    for sid, sname, pname in STATIONS[:2]:
        base = settle[sid]
        for h in range(24):
            dt = BASE_DATE + timedelta(hours=h)
            it = INSERT_TIME + timedelta(minutes=h + 10)
            val = round(base + (h % 5) * 0.18 - h * 0.01, 3)
            tag = f"GX-{sid[-2:]}"
            rows.append(
                f"({q(uid())},{q(uid())},{q(sid)},{q(tag)},"
                f"{q(dt.strftime('%Y-%m-%d %H:%M:%S'))},{q(it.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3])},"
                f"{n(val)},{q(pname)},NULL)"
            )
    return rows


def gen_jyb() -> list[str]:
    rows: list[str] = []
    settle_base = {"40101": 0.21, "40102": -0.05, "40103": -1.88, "40104": -3.341}
    for sid, sname, pname in STATIONS:
        base = settle_base.get(sid, 0.0)
        for h in range(12):
            dt = BASE_DATE + timedelta(hours=h * 2)
            it = INSERT_TIME + timedelta(minutes=h * 2)
            val = round(base + h * 0.03, 3)
            tag = f"J{sid[-2:]}-1"
            rows.append(
                f"({q(uid())},{q(uid())},{q(sid)},{q(tag)},"
                f"{q(dt.strftime('%Y-%m-%d %H:%M:%S'))},{q(it.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3])},"
                f"{n(val)},{q(pname)},NULL)"
            )
    return rows


def gen_kxsylj() -> list[str]:
    rows: list[str] = []
    # 不同站点压力基线（参照文档截图量级）
    profiles = [
        ("40101", "K1-2KT", "BJ01(朝阳金盏)", 34.4),
        ("40102", "K1-2P", "BJ02(朝阳双桥)", 125.0),
        ("40103", "K6-1T", "BJ03(朝阳管庄)", 14.4),
    ]
    for sid, tag, pname, p0 in profiles:
        for h in range(24):
            dt = BASE_DATE + timedelta(hours=h)
            it = INSERT_TIME + timedelta(minutes=h + 5)
            val = round(p0 + (h % 4) * 0.05 - h * 0.002, 3)
            rows.append(
                f"({q(uid())},{q(uid())},{q(sid)},{q(tag)},"
                f"{q(dt.strftime('%Y-%m-%d %H:%M:%S'))},{q(it.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3])},"
                f"{n(val)},{q(pname)},NULL)"
            )
    return rows


def gen_qxz() -> list[str]:
    rows: list[str] = []
    # 气象站 5 分钟间隔，每站 24 条（约 2 小时窗口）或 48 条
    profiles = [
        ("40101", "朝阳金盏监测站"),
        ("40102", "朝阳双桥监测站"),
    ]
    temps = [(-2.1, 0.3), (-1.8, 0.5)]
    for idx, (sid, sname) in enumerate(profiles):
        t0, t1 = temps[idx]
        for i in range(24):
            dt = BASE_DATE + timedelta(minutes=i * 5)
            it = INSERT_TIME + timedelta(minutes=i)
            temp = round(t0 + (i / 23) * (t1 - t0) + (i % 3) * 0.2, 1)
            rain = 0 if i < 20 else round((i - 19) * 0.1, 1)
            hum = round(28 + (i % 8) * 1.8, 1)
            pres = round(102.4 + (i % 5) * 0.06, 2)
            ws = round((i % 6) * 0.22, 1)
            wd = round((i * 15) % 360, 1)
            rows.append(
                f"({q(uid())},{q(uid())},{q(sid)},{q(sname)},"
                f"{q(dt.strftime('%Y-%m-%d %H:%M:%S'))},{q(it.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3])},"
                f"{n(temp)},{n(rain)},{n(hum)},{n(pres)},{n(ws)},{n(wd)},"
                f"{q(PROJECT_BEIJING)},NULL)"
            )
    return rows


TABLES = [
    ("t_data_dxswj", "id,data_id,station_id,station_name,data_time,insert_time,deep,elevation,project_name,expand", gen_dxswj),
    ("t_data_fcb", "id,data_id,station_id,station_name,data_time,insert_time,total_settle,project_name,expand", gen_fcb),
    ("t_data_gnss", "id,data_id,station_id,station_name,data_time,insert_time,gps_total_x,gps_total_y,gps_total_z,displacement_2d,displacement_3d,project_name,expand", gen_gnss),
    ("t_data_gq", "id,data_id,station_id,station_name,data_time,insert_time,total_settle,project_name,expand", gen_gq),
    ("t_data_jyb", "id,data_id,station_id,station_name,data_time,insert_time,total_settle,project_name,expand", gen_jyb),
    ("t_data_kxsylj", "id,data_id,station_id,station_name,data_time,insert_time,pressure,project_name,expand", gen_kxsylj),
    ("t_data_qxz", "id,data_id,station_id,station_name,data_time,insert_time,temp,real_time_rain,humidity,pressure,wind_speed,wind_dirt,project_name,expand", gen_qxz),
]


def main() -> None:
    out = Path(__file__).with_name("dmcj_seed_data.sql")
    chunks: list[str] = ["-- auto-generated test data\n"]
    for table, cols, gen in TABLES:
        rows = gen()
        chunks.append(f"\n-- {table} ({len(rows)} rows)\n")
        chunks.append(f"TRUNCATE TABLE public.{table};\n")
        # batch insert 50 rows
        for i in range(0, len(rows), 50):
            batch = rows[i : i + 50]
            chunks.append(f"INSERT INTO public.{table} ({cols}) VALUES\n")
            chunks.append(",\n".join(batch))
            chunks.append(";\n")
    out.write_text("".join(chunks), encoding="utf-8")
    print(f"Wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
