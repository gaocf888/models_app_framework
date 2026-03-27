#!/usr/bin/env python3
"""
健康检查脚本 - 支持任意位置部署
"""

import sys
import os
import yaml
import requests
from pathlib import Path


def find_config():
    """自动查找配置文件"""
    possible_paths = [
        Path(__file__).parent.parent / "config" / "vllm.yaml",
        Path.cwd() / "config" / "vllm.yaml",
        Path("/workspace/config/vllm.yaml"),
        Path(os.getenv("VLLM_CONFIG_DIR", "")) / "vllm.yaml" if os.getenv("VLLM_CONFIG_DIR") else None,
    ]

    for path in possible_paths:
        if path and path.exists():
            return path

    return None


def load_config():
    """加载配置"""
    config_path = find_config()
    if config_path:
        with open(config_path) as f:
            return yaml.safe_load(f)
    return {}


def check_health():
    """健康检查"""
    config = load_config()
    host = config.get("server", {}).get("host", "localhost")
    port = config.get("server", {}).get("port", 8000)

    endpoints = [
        f"http://{host}:{port}/health",
        f"http://{host}:{port}/v1/models"
    ]

    for endpoint in endpoints:
        try:
            resp = requests.get(endpoint, timeout=5)
            if resp.status_code == 200:
                print(f"OK - {endpoint}")
                return 0
        except Exception:
            continue

    print("ERROR - 健康检查失败")
    return 1


if __name__ == "__main__":
    sys.exit(check_health())