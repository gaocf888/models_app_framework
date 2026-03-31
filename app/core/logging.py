import json
import logging
import sys
import gzip
import shutil
from pathlib import Path
from logging import Handler, LogRecord
from logging.handlers import RotatingFileHandler

from app.core.config import get_app_config


class JsonStreamHandler(Handler):
    """
    简单的 JSON 日志输出 handler，后续可与 Loki/ELK 对接。
    """

    def emit(self, record: LogRecord) -> None:
        ts = self.formatter.formatTime(record, "%Y-%m-%dT%H:%M:%S") if self.formatter else ""
        log_entry = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "time": ts,
        }
        sys.stdout.write(json.dumps(log_entry, ensure_ascii=False) + "\n")


def _build_file_handler(cfg) -> RotatingFileHandler | None:
    if not cfg.file_enabled:
        return None
    log_path = cfg.log_file or "./logs/app.log"
    p = Path(log_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        filename=str(p),
        maxBytes=cfg.file_max_bytes,
        backupCount=cfg.file_backup_count,
        encoding="utf-8",
    )
    if cfg.file_compress:
        handler.namer = lambda name: f"{name}.gz"

        def _rotator(source: str, dest: str) -> None:
            with open(source, "rb") as sf, gzip.open(dest, "wb") as df:
                shutil.copyfileobj(sf, df)
            Path(source).unlink(missing_ok=True)

        handler.rotator = _rotator
    return handler


def setup_logging() -> None:
    """
    初始化全局日志配置。
    从 AppConfig 读取日志级别与输出格式配置。
    """
    cfg = get_app_config().logging

    level = cfg.level.upper()
    handlers: list[Handler] = []

    if cfg.json_format:
        handlers.append(JsonStreamHandler())
    else:
        handlers.append(logging.StreamHandler(sys.stdout))
    file_handler = _build_file_handler(cfg)
    if file_handler is not None:
        handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        handlers=handlers,
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

