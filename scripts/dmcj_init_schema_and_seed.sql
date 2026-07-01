-- 沉降标准库 dmcj 表结构 + 北京监测站测试数据
-- 依据：docs/地降所需求及数据相关/数据库结构及逻辑/沉降标准数据库表结构说明260325.docx

-- ========== DDL ==========

CREATE TABLE IF NOT EXISTS public.t_data_dxswj (
    id           VARCHAR(64) PRIMARY KEY,
    data_id      VARCHAR(64),
    station_id   VARCHAR(32),
    station_name VARCHAR(128),
    data_time    TIMESTAMP,
    insert_time  TIMESTAMP,
    deep         NUMERIC,
    elevation    NUMERIC,
    project_name VARCHAR(128),
    expand       VARCHAR(256)
);

CREATE TABLE IF NOT EXISTS public.t_data_fcb (
    id           VARCHAR(64) PRIMARY KEY,
    data_id      VARCHAR(64),
    station_id   VARCHAR(32),
    station_name VARCHAR(128),
    data_time    TIMESTAMP,
    insert_time  TIMESTAMP,
    total_settle NUMERIC,
    project_name VARCHAR(128),
    expand       VARCHAR(256)
);

CREATE TABLE IF NOT EXISTS public.t_data_gnss (
    id              VARCHAR(64) PRIMARY KEY,
    data_id         VARCHAR(64),
    station_id      VARCHAR(32),
    station_name    VARCHAR(128),
    data_time       TIMESTAMP,
    insert_time     TIMESTAMP,
    gps_total_x     NUMERIC,
    gps_total_y     NUMERIC,
    gps_total_z     NUMERIC,
    displacement_2d NUMERIC,
    displacement_3d NUMERIC,
    project_name    VARCHAR(128),
    expand          VARCHAR(256)
);

CREATE TABLE IF NOT EXISTS public.t_data_gq (
    id           VARCHAR(64) PRIMARY KEY,
    data_id      VARCHAR(64),
    station_id   VARCHAR(32),
    station_name VARCHAR(128),
    data_time    TIMESTAMP,
    insert_time  TIMESTAMP,
    total_settle NUMERIC,
    project_name VARCHAR(128),
    expand       VARCHAR(256)
);

CREATE TABLE IF NOT EXISTS public.t_data_jyb (
    id           VARCHAR(64) PRIMARY KEY,
    data_id      VARCHAR(64),
    station_id   VARCHAR(32),
    station_name VARCHAR(128),
    data_time    TIMESTAMP,
    insert_time  TIMESTAMP,
    total_settle NUMERIC,
    project_name VARCHAR(128),
    expand       VARCHAR(256)
);

CREATE TABLE IF NOT EXISTS public.t_data_kxsylj (
    id           VARCHAR(64) PRIMARY KEY,
    data_id      VARCHAR(64),
    station_id   VARCHAR(32),
    station_name VARCHAR(128),
    data_time    TIMESTAMP,
    insert_time  TIMESTAMP,
    pressure     NUMERIC,
    project_name VARCHAR(128),
    expand       VARCHAR(256)
);

CREATE TABLE IF NOT EXISTS public.t_data_qxz (
    id             VARCHAR(64) PRIMARY KEY,
    data_id        VARCHAR(64),
    station_id     VARCHAR(32),
    station_name   VARCHAR(128),
    data_time      TIMESTAMP,
    insert_time    TIMESTAMP,
    temp           NUMERIC,
    real_time_rain NUMERIC,
    humidity       NUMERIC,
    pressure       NUMERIC,
    wind_speed     NUMERIC,
    wind_dirt      NUMERIC,
    project_name   VARCHAR(128),
    expand         VARCHAR(256)
);

CREATE INDEX IF NOT EXISTS idx_t_data_dxswj_data_time ON public.t_data_dxswj (data_time);
CREATE INDEX IF NOT EXISTS idx_t_data_fcb_data_time ON public.t_data_fcb (data_time);
CREATE INDEX IF NOT EXISTS idx_t_data_gnss_data_time ON public.t_data_gnss (data_time);
CREATE INDEX IF NOT EXISTS idx_t_data_gq_data_time ON public.t_data_gq (data_time);
CREATE INDEX IF NOT EXISTS idx_t_data_jyb_data_time ON public.t_data_jyb (data_time);
CREATE INDEX IF NOT EXISTS idx_t_data_kxsylj_data_time ON public.t_data_kxsylj (data_time);
CREATE INDEX IF NOT EXISTS idx_t_data_qxz_data_time ON public.t_data_qxz (data_time);
