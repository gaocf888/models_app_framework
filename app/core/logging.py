import json
import logging
import sys
from logging import Handler, LogRecord

from app.core.config import get_app_config


class JsonStreamHandler(Handler):
    """
    简单的 JSON 日志输出 handler，后续可与 Loki/ELK 对接。
    """

    def emit(self, record: LogRecord) -> None:
        log_entry = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
        }
        sys.stdout.write(json.dumps(log_entry, ensure_ascii=False) + "\n")


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

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        handlers=handlers,
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

