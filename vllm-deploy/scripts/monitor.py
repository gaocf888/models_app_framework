#!/usr/bin/env python3
"""
服务监控脚本 - 定期检查服务状态
"""

import time
import logging
import yaml
import requests
import os
from pathlib import Path
from datetime import datetime


class Monitor:
    def __init__(self):
        self.script_dir = Path(__file__).parent.resolve()
        self.project_root = self.script_dir.parent.resolve()
        self.config_dir = self.project_root / "config"
        self.log_dir = self.project_root / "logs"
        self._load_config()
        self._setup_logging()

    def _load_config(self):
        """加载配置"""
        vllm_config_path = self.config_dir / "vllm.yaml"
        logging_config_path = self.config_dir / "logging.yaml"

        with open(vllm_config_path) as f:
            self.config = yaml.safe_load(f)

        with open(logging_config_path) as f:
            self.log_config = yaml.safe_load(f)

    def _setup_logging(self):
        """设置日志"""
        self.log_dir.mkdir(parents=True, exist_ok=True)

        logging.basicConfig(
            level=getattr(logging, self.log_config.get("level", "INFO")),
            format=self.log_config.get("format", "%(asctime)s - %(levelname)s - %(message)s"),
            handlers=[
                logging.FileHandler(self.log_dir / "monitor.log"),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

    def check_health(self) -> bool:
        """检查健康"""
        host = self.config.get("server", {}).get("host", "localhost")
        port = self.config.get("server", {}).get("port", 8000)

        try:
            resp = requests.get(f"http://{host}:{port}/health", timeout=5)
            return resp.status_code == 200
        except:
            return False

    def check_process(self) -> bool:
        """检查进程"""
        try:
            with open("/tmp/vllm.pid") as f:
                pid = int(f.read().strip())
            return os.kill(pid, 0) == 0
        except:
            return False

    def run(self):
        """运行监控"""
        interval = self.log_config.get("health_check", {}).get("interval", 30)

        self.logger.info("启动监控服务")

        while True:
            try:
                healthy = self.check_health()
                process_running = self.check_process()

                status = "正常" if healthy else "异常"
                self.logger.info(f"服务状态: {status} | 进程: {'运行中' if process_running else '已停止'}")

                if not healthy and not process_running:
                    self.logger.error("服务异常，需要人工介入")

                time.sleep(interval)

            except KeyboardInterrupt:
                self.logger.info("监控停止")
                break
            except Exception as e:
                self.logger.error(f"监控错误: {e}")
                time.sleep(interval)


if __name__ == "__main__":
    monitor = Monitor()
    monitor.run()