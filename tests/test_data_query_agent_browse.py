"""浏览路径 GET /tree、GET /browse：mock SQLExecutor，不连 PG。"""

from __future__ import annotations

import pytest

from app.analysis_agent.deterministics.coverage import load_device_projects_by_type
from app.core.config import get_app_config
from app.data_query_agent.browse import (
    BrowseRequestError,
    build_annual_sql,
    build_device_names_sql,
    build_device_time_sql,
    build_device_values_sql,
    build_latest_sql,
    build_station_area_sql,
    fetch_browse_list,
    fetch_perception_tree,
    normalize_page,
    representative_marks,
    resolve_browse_library,
)
from app.data_query_agent.catalog import get_library_catalog
from app.models.data_query_agent import DataQueryAgentBrowseResponse
from app.services.data_query_agent_service import DataQueryAgentService


class _FakeExecutor:
    def __init__(
        self,
        pages: list[list[dict]] | None = None,
        total: int = 2,
        station_rows: list[dict] | None = None,
        device_names: list[dict] | None = None,
        time_rows: list[dict] | None = None,
        value_rows: list[dict] | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._pages = pages or [
            [
                {
                    "station_id": "F28(金盏)",
                    "station_name": "F28(金盏)",
                    "mark_name": "F28-10",
                    "area": "朝阳区",
                    "total_settle_mm": -84.2,
                    "total_settle": -84.2,
                    "data_time": "2026-08-01",
                },
                {
                    "station_id": "F2(望京)",
                    "station_name": "F2(望京)",
                    "mark_name": "F2-8",
                    "area": "朝阳区",
                    "total_settle_mm": -76.5,
                    "total_settle": -76.5,
                    "data_time": "2026-08-01",
                },
            ]
        ]
        self._station_rows = station_rows or [
            {"name": "F2(望京)", "area": "朝阳区"},
            {"name": "F3(天竺）", "area": "顺义区"},
            {"name": "F28(金盏)", "area": "朝阳区"},
            {"name": "F42(牌楼）", "area": "通州区"},
        ]
        self._device_names = device_names or [
            {"station_name": "F9-1T"},
            {"station_name": "F9-2"},
            {"station_name": "F9-1"},
        ]
        self._time_rows = time_rows or [
            {"data_time": "2025-11-30 23:00:00"},
            {"data_time": "2025-11-30 22:00:00"},
        ]
        self._value_rows = value_rows or [
            {"data_time": "2025-11-30 23:00:00", "station_name": "F9-1", "v": 0.0},
            {"data_time": "2025-11-30 23:00:00", "station_name": "F9-1T", "v": -8.691},
            {"data_time": "2025-11-30 23:00:00", "station_name": "F9-2", "v": 11.877},
            {"data_time": "2025-11-30 22:00:00", "station_name": "F9-1", "v": 0.0},
            {"data_time": "2025-11-30 22:00:00", "station_name": "F9-1T", "v": -8.72},
            {"data_time": "2025-11-30 22:00:00", "station_name": "F9-2", "v": 12.002},
        ]
        self._total = total
        self._idx = 0

    async def execute(self, sql: str, params: dict | None = None) -> list[dict]:
        self.calls.append((sql, dict(params or {})))
        low = sql.lower()
        if "from t_station" in low:
            return list(self._station_rows)
        if "count(*)" in low:
            return [{"total": self._total}]
        if "distinct w.station_name" in low:
            return list(self._device_names)
        if "distinct w.data_time" in low:
            return list(self._time_rows)
        if "w.data_time = any(:times)" in low:
            return list(self._value_rows)
        if "any(:marks)" in low or "any(:names)" in low:
            return [
                {"station_id": "F28(金盏)", "t": "2025-08-01", "v": -70.0},
                {"station_id": "F28(金盏)", "t": "2026-08-01", "v": -84.2},
                {"station_id": "F2(望京)", "t": "2025-08-01", "v": -62.3},
                {"station_id": "F2(望京)", "t": "2026-08-01", "v": -76.5},
            ]
        rows = self._pages[min(self._idx, len(self._pages) - 1)]
        self._idx += 1
        return rows


@pytest.fixture(autouse=True)
def _mem_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_QUERY_AGENT_CHECKPOINT_BACKEND", "memory")
    monkeypatch.setenv("DATA_QUERY_AGENT_SESSION_STORE_BACKEND", "memory")
    monkeypatch.setenv("DATA_QUERY_AGENT_TRACE_BACKEND", "memory")
    get_app_config.cache_clear()
    yield
    get_app_config.cache_clear()


def test_resolve_default_library_is_fcb() -> None:
    lib = resolve_browse_library(None, required=False)
    assert lib.id == "fcb"
    assert lib.table == "t_data_wash_fcb"
    with pytest.raises(BrowseRequestError) as exc:
        resolve_browse_library("not-a-lib", required=False)
    assert exc.value.status_code == 404


def test_normalize_page() -> None:
    assert normalize_page(1, 20) == (1, 20, 0)
    assert normalize_page(3, 10) == (3, 10, 20)
    with pytest.raises(BrowseRequestError):
        normalize_page(0, 20)
    with pytest.raises(BrowseRequestError):
        normalize_page(1, 0)
    with pytest.raises(BrowseRequestError):
        normalize_page(1, 101)


def test_latest_sql_projects_physical_not_display_alias() -> None:
    lib = get_library_catalog().get("fcb")
    assert lib is not None
    sql, params = build_latest_sql(lib, marks=("F8-10", "F28-10"), limit=20, offset=0)
    assert "w.total_settle AS total_settle_mm" in sql
    assert "w.total_settle_mm AS" not in sql
    assert "w.station_name = ANY(:rep_marks)" in sql
    assert params["rep_marks"] == ["F8-10", "F28-10"]
    assert "distinct on" not in sql.lower()
    assert "order by w.data_time desc" in sql.lower()
    assert "limit :limit offset :offset" in sql.lower()
    assert "t_data_wash_jyb" not in sql.lower()
    count_sql, count_params = build_latest_sql(lib, marks=("F8-10",), count_only=True)
    assert "count(*)" in count_sql.lower()
    assert "distinct on" not in count_sql.lower()
    assert count_params["rep_marks"] == ["F8-10"]
    assert "w.device_name" not in sql
    assert "w.project_name AS station_name" in sql
    assert "w.station_name AS mark_name" in sql


def test_latest_sql_without_marks_uses_project_name() -> None:
    lib = get_library_catalog().get("dxswj")
    assert lib is not None
    sql, params = build_latest_sql(lib, marks=(), projects=("F2(望京)",), limit=20, offset=0)
    assert "distinct on" not in sql.lower()
    assert "w.project_name = ANY(:projects)" in sql
    assert params["projects"] == ["F2(望京)"]
    assert "order by w.data_time desc" in sql.lower()
    assert "t_data_wash_dxswj" in sql.lower()


def test_fcb_representative_marks_are_layer0() -> None:
    lib = get_library_catalog().get("fcb")
    assert lib is not None
    marks = representative_marks(lib)
    assert "F8-10" in marks
    assert "F8-1" not in marks
    assert len(marks) >= 40


def test_device_matrix_sql_matches_project_and_paginates_time() -> None:
    lib = get_library_catalog().get("fcb")
    assert lib is not None
    names_sql, names_params = build_device_names_sql(lib, "F9(后芮营)")
    assert "t_data_wash_fcb" in names_sql.lower()
    assert names_params["station_id"] == "F9(后芮营)"
    count_sql, _ = build_device_time_sql(lib, "F9(后芮营)", count_only=True)
    assert "count(*)" in count_sql.lower()
    times_sql, times_params = build_device_time_sql(lib, "F9(后芮营)", limit=20, offset=0)
    assert "order by w.data_time desc" in times_sql.lower()
    assert times_params["limit"] == 20
    values_sql, _ = build_device_values_sql(lib, "F9(后芮营)")
    assert "w.total_settle AS v" in values_sql
    assert "w.data_time = ANY(:times)" in values_sql


def test_latest_sql_gnss_filters_station_name_not_project() -> None:
    lib = get_library_catalog().get("gnss")
    assert lib is not None
    sql, params = build_latest_sql(lib, projects=("F15-GNSS", "SLP01"), limit=20, offset=0)
    assert "w.station_name = ANY(:projects)" in sql
    assert "w.project_name = ANY(:projects)" not in sql
    assert params["projects"] == ["F15-GNSS", "SLP01"]


def test_latest_sql_qxz_filters_station_code() -> None:
    lib = get_library_catalog().get("qxz")
    assert lib is not None
    sql, params = build_latest_sql(lib, projects=("F21(西集)", "F33(留士庄)"), limit=20, offset=0)
    assert "split_part" in sql.lower()
    assert "w.project_name = ANY(:projects)" not in sql
    assert params["site_codes"] == ["F21", "F33"]


def test_device_matrix_gnss_matches_station_name() -> None:
    lib = get_library_catalog().get("gnss")
    assert lib is not None
    names_sql, names_params = build_device_names_sql(lib, "F15-GNSS")
    assert "t_data_wash_gnss" in names_sql.lower()
    assert "w.station_name = :station_id" in names_sql
    assert "w.project_name = :station_id" not in names_sql
    assert names_params["station_id"] == "F15-GNSS"


def test_latest_sql_gnss_selects_xyz_and_resultants() -> None:
    lib = get_library_catalog().get("gnss")
    assert lib is not None
    sql, _ = build_latest_sql(lib, projects=("DAD01",), limit=20, offset=0)
    low = sql.lower()
    for col in ("gps_total_x", "gps_total_y", "gps_total_z", "displacement_2d", "displacement_3d"):
        assert f"w.{col} as {col}" in low


def test_latest_sql_qxz_selects_weather_fields() -> None:
    lib = get_library_catalog().get("qxz")
    assert lib is not None
    sql, _ = build_latest_sql(lib, projects=("F21(西集)",), limit=20, offset=0)
    low = sql.lower()
    for col in ("temp", "real_time_rain", "humidity", "pressure", "wind_speed", "wind_dirt"):
        assert f"w.{col} as {col}" in low


def test_device_values_sql_gnss_selects_metrics_not_single_v() -> None:
    lib = get_library_catalog().get("gnss")
    assert lib is not None
    sql, _ = build_device_values_sql(lib, "DAD01")
    low = sql.lower()
    assert " as v " not in low
    assert "w.gps_total_x as gps_total_x" in low
    assert "w.displacement_3d as displacement_3d" in low


def test_device_matrix_qxz_matches_yaml_site_code() -> None:
    lib = get_library_catalog().get("qxz")
    assert lib is not None
    names_sql, names_params = build_device_names_sql(lib, "F21(西集)")
    assert "t_data_wash_qxz" in names_sql.lower()
    assert "split_part" in names_sql.lower()
    assert names_params["site_code"] == "F21"
    assert "w.project_name = :station_id" not in names_sql


def test_annual_sql_uses_any_names() -> None:
    lib = get_library_catalog().get("fcb")
    assert lib is not None
    sql = build_annual_sql(lib)
    assert "t_data_wash_fcb" in sql
    assert "ANY(:names)" in sql
    assert "total_settle" in sql
    mark_sql = build_annual_sql(lib, by_mark=True)
    assert "w.station_name = ANY(:marks)" in mark_sql
    assert "ANY(:names)" not in mark_sql


@pytest.mark.asyncio
async def test_browse_list_default_fcb_is_rep_station() -> None:
    exe = _FakeExecutor()
    payload = await fetch_browse_list(page=1, page_size=20, executor=exe)
    assert payload["library_id"] == "fcb"
    assert payload["page"] == 1
    assert payload["page_size"] == 20
    assert payload["total"] == 2
    assert payload["has_more"] is False
    assert payload["result_grain"] == "station"
    by_id = {row["station_id"]: row for row in payload["list"]}
    assert by_id["F28-10"]["station_name"] == "F28(金盏)"
    assert by_id["F28-10"]["hud_entity_id"] == "F28(金盏)"
    assert by_id["F28-10"]["hud_available"] is True
    assert by_id["F28-10"]["total_settle_mm"] == pytest.approx(-84.2)
    assert by_id["F28-10"]["device_name"] == "F28-10"
    assert by_id["F2-8"]["station_name"] == "F2(望京)"
    assert by_id["F2-8"]["device_name"] == "F2-8"
    assert all("w.device_name" not in sql for sql, _ in exe.calls)
    assert "annual_settle_mm" not in by_id["F28-10"]
    assert "annual_settle_mm" not in by_id["F2-8"]
    assert "period_settle_mm" not in by_id["F28-10"]
    settle_col = next(c for c in payload["columns"] if c["key"] == "total_settle_mm")
    assert settle_col["title"] == "沉降量(mm)"
    assert all(c["key"] != "annual_settle_mm" for c in payload["columns"])
    assert all(c["key"] != "period_settle_mm" for c in payload["columns"])
    assert all(c["key"] != "F9-1" for c in payload["columns"])
    assert all("period_settle_mm" not in sql.lower() for sql, _ in exe.calls)
    assert any("any(:rep_marks)" in sql.lower() for sql, _ in exe.calls)
    assert any("order by w.data_time desc" in sql.lower() for sql, _ in exe.calls)
    assert all("any(:marks)" not in sql.lower() for sql, _ in exe.calls)
    assert all("any(:names)" not in sql.lower() for sql, _ in exe.calls)
    assert all("distinct on" not in sql.lower() for sql, _ in exe.calls)
    assert all("t_data_wash_jyb" not in sql.lower() for sql, _ in exe.calls)


@pytest.mark.asyncio
async def test_browse_list_rep_keeps_all_times_desc() -> None:
    exe = _FakeExecutor(
        total=2,
        pages=[
            [
                {
                    "station_id": "F28(金盏)",
                    "station_name": "F28(金盏)",
                    "mark_name": "F28-10",
                    "area": "朝阳区",
                    "total_settle_mm": -84.2,
                    "total_settle": -84.2,
                    "data_time": "2026-08-01",
                },
                {
                    "station_id": "F28(金盏)",
                    "station_name": "F28(金盏)",
                    "mark_name": "F28-10",
                    "area": "朝阳区",
                    "total_settle_mm": -70.0,
                    "total_settle": -70.0,
                    "data_time": "2025-08-01",
                },
            ]
        ],
    )
    payload = await fetch_browse_list(library_id="fcb", page=1, page_size=20, executor=exe)
    assert payload["total"] == 2
    assert [row["data_time"] for row in payload["list"]] == ["2026-08-01", "2025-08-01"]
    assert payload["list"][0]["station_id"] == "F28-10"
    assert payload["list"][0]["station_name"] == "F28(金盏)"
    assert payload["list"][1]["station_id"] == "F28-10"
    assert payload["list"][1]["hud_entity_id"] == "F28(金盏)"


@pytest.mark.asyncio
async def test_browse_list_district_filters_rep_stations() -> None:
    exe = _FakeExecutor()
    payload = await fetch_browse_list(
        library_id="jyb",
        district="朝阳区",
        page=1,
        page_size=20,
        executor=exe,
    )
    assert payload["library_id"] == "jyb"
    assert payload["district"] == "朝阳区"
    assert payload["station_id"] is None
    assert payload["result_grain"] == "station"
    assert all(c["key"] != "annual_settle_mm" for c in payload["columns"])
    assert all(c["key"] != "period_settle_mm" for c in payload["columns"])
    assert any(c["key"] == "total_settle_mm" and c["title"] == "沉降量(mm)" for c in payload["columns"])
    assert all("annual_settle_mm" not in row for row in payload["list"])
    assert all("period_settle_mm" not in row for row in payload["list"])
    assert all(row["station_name"] != "F28(金盏)" for row in payload["list"])
    assert payload["list"][0]["station_name"] == "F2(望京)"
    assert payload["list"][0]["station_id"] == "F2-8"
    assert payload["list"][0]["hud_entity_id"] == "F2(望京)"
    list_sql = next(sql for sql, _ in exe.calls if "order by w.data_time desc" in sql.lower())
    assert "any(:rep_marks)" in list_sql.lower()
    assert "t_data_wash_jyb" in list_sql.lower()
    assert "distinct on" not in list_sql.lower()


@pytest.mark.asyncio
async def test_browse_list_groundwater_device_id_not_site() -> None:
    exe = _FakeExecutor(
        total=2,
        station_rows=[
            {"name": "F30(孙家营)", "area": "大兴区"},
            {"name": "F2(望京)", "area": "朝阳区"},
        ],
        pages=[
            [
                {
                    "station_id": "F30(孙家营)",
                    "station_name": "F30(孙家营)",
                    "mark_name": "F30-1",
                    "area": "大兴区",
                    "deep": 47.5,
                    "elevation": -25.3,
                    "data_time": "2026-11-12 23:00:00",
                },
                {
                    "station_id": "F30(孙家营)",
                    "station_name": "F30(孙家营)",
                    "mark_name": "F30-2",
                    "area": "大兴区",
                    "deep": 36.3,
                    "elevation": -14.1,
                    "data_time": "2026-11-12 23:00:00",
                },
            ]
        ],
    )
    payload = await fetch_browse_list(
        library_id="dxswj",
        district="大兴区",
        page=1,
        page_size=20,
        executor=exe,
    )
    assert payload["library_id"] == "dxswj"
    assert payload["result_grain"] == "station"
    assert [row["station_id"] for row in payload["list"]] == ["F30-1", "F30-2"]
    assert all(row["station_name"] == "F30(孙家营)" for row in payload["list"])
    assert all(row["hud_entity_id"] == "F30(孙家营)" for row in payload["list"])
    list_sql = next(sql for sql, _ in exe.calls if "order by w.data_time desc" in sql.lower())
    assert "t_data_wash_dxswj" in list_sql.lower()
    assert "any(:projects)" in list_sql.lower()
    assert "any(:rep_marks)" not in list_sql.lower()


@pytest.mark.asyncio
async def test_browse_list_gnss_maps_station_name_not_engineering_project() -> None:
    exe = _FakeExecutor(
        total=1,
        station_rows=[{"name": "F15(闫家营)", "area": "通州区"}],
        pages=[
            [
                {
                    "station_id": "北京地面沉降监测",
                    "station_name": "北京地面沉降监测",
                    "mark_name": "F15-GNSS",
                    "displacement_3d": 1.2,
                    "data_time": "2026-11-12 08:00:00",
                }
            ]
        ],
    )
    payload = await fetch_browse_list(library_id="gnss", page=1, page_size=20, executor=exe)
    assert payload["library_id"] == "gnss"
    assert payload["list"][0]["station_id"] == "F15-GNSS"
    assert payload["list"][0]["station_name"] == "F15-GNSS"
    assert payload["list"][0]["hud_entity_id"] == "F15-GNSS"
    by_key = {c["key"]: c["title"] for c in payload["columns"]}
    assert by_key["gps_total_x"] == "X位移"
    assert by_key["gps_total_y"] == "Y位移"
    assert by_key["gps_total_z"] == "Z位移"
    assert by_key["displacement_2d"] == "水平合位移"
    assert by_key["displacement_3d"] == "合位移"
    assert "二维位移" not in by_key.values()
    assert "三维位移" not in by_key.values()
    list_sql = next(sql for sql, _ in exe.calls if "order by w.data_time desc" in sql.lower())
    assert "t_data_wash_gnss" in list_sql.lower()
    assert "w.station_name = any(:projects)" in list_sql.lower()
    assert "w.project_name = any(:projects)" not in list_sql.lower()


@pytest.mark.asyncio
async def test_browse_list_qxz_maps_code_to_yaml_site() -> None:
    exe = _FakeExecutor(
        total=1,
        station_rows=[{"name": "F21(西集)", "area": "通州区"}],
        pages=[
            [
                {
                    "station_id": "北京地面沉降监测",
                    "station_name": "北京地面沉降监测",
                    "mark_name": "F21-气象站",
                    "temp": 12.3,
                    "real_time_rain": 0.0,
                    "data_time": "2026-11-12 08:00:00",
                }
            ]
        ],
    )
    payload = await fetch_browse_list(library_id="qxz", page=1, page_size=20, executor=exe)
    assert payload["library_id"] == "qxz"
    assert payload["list"][0]["station_id"] == "F21-气象站"
    assert payload["list"][0]["station_name"] == "F21(西集)"
    assert payload["list"][0]["hud_entity_id"] == "F21(西集)"
    by_key = {c["key"]: c["title"] for c in payload["columns"]}
    assert by_key["temp"] == "气温"
    assert by_key["real_time_rain"] == "降水"
    assert by_key["humidity"] == "相对湿度"
    assert by_key["pressure"] == "气压"
    assert by_key["wind_speed"] == "风速"
    assert by_key["wind_dirt"] == "风向"
    list_sql = next(sql for sql, _ in exe.calls if "order by w.data_time desc" in sql.lower())
    assert "t_data_wash_qxz" in list_sql.lower()
    assert "split_part" in list_sql.lower()
    assert "w.project_name = any(:projects)" not in list_sql.lower()


@pytest.mark.asyncio
async def test_browse_list_station_click_is_device_time_matrix() -> None:
    exe = _FakeExecutor(total=48)
    payload = await fetch_browse_list(
        library_id="fcb",
        station_id="F9(后芮营)",
        page=1,
        page_size=20,
        executor=exe,
    )
    assert payload["result_grain"] == "device_time"
    assert payload["station_id"] == "F9(后芮营)"
    assert payload["total"] == 48
    assert payload["has_more"] is True
    assert payload["hud_enabled"] is False
    keys = [c["key"] for c in payload["columns"]]
    assert keys[0] == "data_time"
    assert keys[1:] == ["F9-1", "F9-1T", "F9-2"]
    assert payload["list"][0]["data_time"] == "2025-11-30 23:00:00"
    assert payload["list"][0]["F9-1"] == pytest.approx(0.0)
    assert payload["list"][0]["F9-1T"] == pytest.approx(-8.691)
    assert payload["list"][1]["data_time"] == "2025-11-30 22:00:00"
    assert any("distinct w.data_time" in sql.lower() for sql, _ in exe.calls)
    assert all("distinct on (w.station_name)" not in sql.lower() for sql, _ in exe.calls)
    assert all("children" not in c for c in payload["columns"])


@pytest.mark.asyncio
async def test_browse_gnss_station_click_grouped_metric_header() -> None:
    exe = _FakeExecutor(
        total=2,
        device_names=[{"station_name": "DAD01"}],
        time_rows=[{"data_time": "2026-11-12 08:00:00"}],
        value_rows=[
            {
                "data_time": "2026-11-12 08:00:00",
                "station_name": "DAD01",
                "gps_total_x": 1.591,
                "gps_total_y": 2.47,
                "gps_total_z": 0.89,
                "displacement_2d": 2.94,
                "displacement_3d": 3.07,
            }
        ],
    )
    payload = await fetch_browse_list(
        library_id="gnss",
        station_id="DAD01",
        page=1,
        page_size=20,
        executor=exe,
    )
    assert payload["result_grain"] == "device_time"
    assert payload["columns"][0]["key"] == "data_time"
    group = payload["columns"][1]
    assert group["key"] == "DAD01"
    assert group["title"] == "DAD01"
    child_titles = [c["title"] for c in group["children"]]
    assert child_titles == ["X位移", "Y位移", "Z位移", "水平合位移", "合位移"]
    row = payload["list"][0]
    assert row["DAD01::gps_total_x"] == pytest.approx(1.591)
    assert row["DAD01::displacement_2d"] == pytest.approx(2.94)
    assert row["DAD01::displacement_3d"] == pytest.approx(3.07)
    assert "DAD01" not in row


@pytest.mark.asyncio
async def test_browse_qxz_station_click_grouped_metric_header() -> None:
    exe = _FakeExecutor(
        total=1,
        device_names=[{"station_name": "F21-气象站"}],
        time_rows=[{"data_time": "2026-11-12 08:00:00"}],
        value_rows=[
            {
                "data_time": "2026-11-12 08:00:00",
                "station_name": "F21-气象站",
                "temp": 12.3,
                "real_time_rain": 0.4,
                "humidity": 55.0,
                "pressure": 1012.0,
                "wind_speed": 2.1,
                "wind_dirt": 180.0,
            }
        ],
    )
    payload = await fetch_browse_list(
        library_id="qxz",
        station_id="F21(西集)",
        page=1,
        page_size=20,
        executor=exe,
    )
    group = payload["columns"][1]
    assert group["title"] == "F21-气象站"
    assert [c["title"] for c in group["children"]] == [
        "气温",
        "降水",
        "相对湿度",
        "气压",
        "风速",
        "风向",
    ]
    row = payload["list"][0]
    assert row["F21-气象站::temp"] == pytest.approx(12.3)
    assert row["F21-气象站::humidity"] == pytest.approx(55.0)
    assert row["F21-气象站::wind_dirt"] == pytest.approx(180.0)


def test_station_area_sql_is_t_station_only() -> None:
    sql = build_station_area_sql().lower()
    assert "from t_station" in sql
    assert "t_data_wash" not in sql


@pytest.mark.asyncio
async def test_tree_groups_yaml_stations_by_t_station_area() -> None:
    exe = _FakeExecutor()
    payload = await fetch_perception_tree(library_id="jyb", executor=exe)
    names = load_device_projects_by_type()["jyb"]
    assert payload["default_library_id"] == "fcb"
    assert len(payload["libraries"]) == 1
    node = payload["libraries"][0]
    assert node["library_id"] == "jyb"
    assert node["station_count"] == len(names)
    by_district = {d["district"]: d for d in node["districts"]}
    assert by_district["朝阳区"]["station_count"] == 1
    assert by_district["顺义区"]["station_count"] == 1
    assert by_district["未分区"]["station_count"] == len(names) - 2
    chaoyang = {s["station_id"]: s for s in by_district["朝阳区"]["stations"]}
    shunyi = {s["station_id"]: s for s in by_district["顺义区"]["stations"]}
    assert chaoyang["F2(望京)"]["latest_value"] is None
    assert chaoyang["F2(望京)"]["data_time"] is None
    assert shunyi["F3(天竺)"]["district"] == "顺义区"
    assert shunyi["F3(天竺)"]["station_name"] == "F3(天竺)"
    sql, _ = exe.calls[0]
    assert "from t_station" in sql.lower()
    assert "t_data_wash" not in sql.lower()
    assert len(exe.calls) == 1


@pytest.mark.asyncio
async def test_tree_fcb_f_code_fallback_and_gnss_coverage() -> None:
    exe = _FakeExecutor()
    fcb = await fetch_perception_tree(library_id="fcb", executor=exe)
    names = load_device_projects_by_type()["fcb"]
    node = fcb["libraries"][0]
    assert node["station_count"] == len(names)
    by_district = {d["district"]: d for d in node["districts"]}
    tongzhou = {s["station_id"]: s for s in by_district["通州区"]["stations"]}
    assert "F42(牌楼站)" in tongzhou
    gnss_names = load_device_projects_by_type()["gnss"]
    gnss = await fetch_perception_tree(library_id="gnss", executor=exe)
    assert gnss["libraries"][0]["station_count"] == len(gnss_names) == 92
    assert gnss["libraries"][0]["districts"]
    all_ids = {
        s["station_id"]
        for d in gnss["libraries"][0]["districts"]
        for s in d["stations"]
    }
    assert "F8-GNSS" in all_ids
    assert "通州孙各庄" in all_ids


@pytest.mark.asyncio
async def test_tree_all_libraries_one_station_query() -> None:
    exe = _FakeExecutor()
    payload = await fetch_perception_tree(executor=exe)
    ids = [x["library_id"] for x in payload["libraries"]]
    assert ids == ["fcb", "jyb", "dxswj", "kxsylj", "gnss", "qxz", "gq"]
    assert len(exe.calls) == 1
    assert "from t_station" in exe.calls[0][0].lower()
    by_id = {x["library_id"]: x for x in payload["libraries"]}
    coverage = load_device_projects_by_type()
    assert by_id["fcb"]["station_count"] == len(coverage["fcb"])
    assert by_id["jyb"]["station_count"] == len(coverage["jyb"])
    assert by_id["gnss"]["station_count"] == len(coverage["gnss"]) == 92
    assert by_id["gq"]["station_count"] == len(coverage["gq"])


@pytest.mark.asyncio
async def test_service_browse_and_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    exe = _FakeExecutor(total=2)
    real_tree = fetch_perception_tree
    real_list = fetch_browse_list

    async def _tree(*, library_id=None, executor=None):
        return await real_tree(library_id=library_id, executor=exe)

    async def _list(**kwargs):
        kwargs["executor"] = exe
        return await real_list(**kwargs)

    monkeypatch.setattr("app.data_query_agent.browse.fetch_perception_tree", _tree)
    monkeypatch.setattr("app.data_query_agent.browse.fetch_browse_list", _list)
    svc = DataQueryAgentService()
    tree = await svc.get_tree(library_id="fcb")
    listed = await svc.browse_list(library_id="fcb", page=1, page_size=20)
    assert tree.libraries[0].library_id == "fcb"
    assert tree.libraries[0].station_count == len(load_device_projects_by_type()["fcb"])
    assert listed.ok is True
    assert listed.library_id == "fcb"
    assert listed.total == 2


def test_browse_response_json_uses_list_alias() -> None:
    body = DataQueryAgentBrowseResponse(
        library_id="fcb",
        page=1,
        page_size=20,
        total=1,
        list=[{"station_id": "F8(周村)"}],
    )
    dumped = body.model_dump(by_alias=True)
    assert "list" in dumped
    assert dumped["list"][0]["station_id"] == "F8(周村)"


@pytest.mark.asyncio
async def test_unknown_library_tree() -> None:
    with pytest.raises(BrowseRequestError) as exc:
        await fetch_perception_tree(library_id="nope", executor=_FakeExecutor())
    assert exc.value.status_code == 404
