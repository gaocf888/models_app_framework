#!/usr/bin/env python3
"""
vLLM 企业级启动脚本
支持任意目录部署，自动定位项目根目录
支持英伟达和国产加速卡（昇腾、寒武纪、沐曦、燧原）
"""

import json
import os
import sys
import signal
import argparse
import logging
import subprocess
import time
import yaml
import socket
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime


class VLLMService:
    """vLLM 服务管理器 - 支持任意位置部署"""

    # models.yaml 预设里的扁平字段与 vllm.yaml 嵌套结构对应关系
    _PRESET_FLAT_FIELDS = {
        "path": ("model", "path"),
        "dtype": ("model", "dtype"),
        "max_model_len": ("model", "max_model_len"),
        "trust_remote_code": ("model", "trust_remote_code"),
        "served_model_name": ("server", "served_model_name"),
        "tensor_parallel_size": ("hardware", "tensor_parallel_size"),
        "gpu_memory_utilization": ("hardware", "gpu_memory_utilization"),
        "block_size": ("hardware", "block_size"),
        "max_num_seqs": ("performance", "max_num_seqs"),
        "max_num_batched_tokens": ("performance", "max_num_batched_tokens"),
        "enable_prefix_caching": ("performance", "enable_prefix_caching"),
        "enforce_eager": ("hardware", "enforce_eager"),
    }

    def __init__(self, config_dir: str = None):
        # 获取脚本所在目录，自动定位项目根目录
        self.script_dir = Path(__file__).parent.resolve()
        self.project_root = self.script_dir.parent.resolve()

        # 配置文件目录
        if config_dir:
            self.config_dir = Path(config_dir).resolve()
        else:
            self.config_dir = self.project_root / "config"

        # 日志目录
        log_dir_env = os.getenv("LOG_DIR")
        if log_dir_env:
            self.log_dir = Path(log_dir_env).resolve()
        else:
            self.log_dir = self.project_root / "logs"

        # 工作目录
        self.workspace = Path(os.getenv("WORKSPACE", str(self.project_root))).resolve()

        # 加载配置
        self.vllm_config = self._load_yaml("vllm.yaml")
        self.models_config = self._load_yaml("models.yaml")
        self.logging_config = self._load_yaml("logging.yaml")

        # 应用模型预设
        self._apply_model_preset()

        # 应用环境变量覆盖
        self._apply_env_overrides()

        # 解析路径
        self._resolve_paths()

        # 初始化日志
        self._setup_logging()

        if self._applied_model_preset:
            self.logger.info(f"应用模型预设: {self._applied_model_preset}")

        # 进程管理
        self.process: Optional[subprocess.Popen] = None
        self.pid_file = Path("/tmp/vllm.pid")

        self.logger.info(f"项目根目录: {self.project_root}")
        self.logger.info(f"配置文件目录: {self.config_dir}")
        self.logger.info(f"日志目录: {self.log_dir}")

    def _load_yaml(self, filename: str) -> Dict:
        """加载 YAML 配置"""
        config_path = self.config_dir / filename

        # 尝试其他可能的路径
        if not config_path.exists():
            alt_config_dir = os.getenv("VLLM_CONFIG_DIR")
            if alt_config_dir:
                alt_path = Path(alt_config_dir) / filename
                if alt_path.exists():
                    config_path = alt_path

        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    return yaml.safe_load(f) or {}
            except Exception as e:
                print(f"警告: 加载配置文件 {config_path} 失败: {e}")
                return {}

        return {}

    def _resolve_paths(self):
        """解析所有路径为绝对路径"""
        # 处理模型路径
        if "model" in self.vllm_config and "path" in self.vllm_config["model"]:
            model_path = self.vllm_config["model"]["path"]
            if model_path and not Path(model_path).is_absolute():
                self.vllm_config["model"]["path"] = str(self.workspace / model_path)

        # 处理多模态媒体路径（multimodal 可能来自预设中的 false，需为 dict 才解析）
        mm = self.vllm_config.get("multimodal")
        if isinstance(mm, dict):
            media_paths = mm.get("media_paths", [])
            resolved_paths = []
            for path in media_paths:
                if path and not Path(path).is_absolute():
                    resolved_paths.append(str(self.workspace / path))
                else:
                    resolved_paths.append(path)
            self.vllm_config["multimodal"]["media_paths"] = resolved_paths

        # 处理日志路径
        log_dir = self.logging_config.get("log_dir", "./logs")
        if log_dir and not Path(log_dir).is_absolute():
            self.logging_config["log_dir"] = str(self.workspace / log_dir)

    def _apply_model_preset(self):
        """应用模型预设配置（在 _setup_logging 之前调用，不可使用 self.logger）"""
        self._applied_model_preset = None
        preset = os.getenv("MODEL_PRESET", self.models_config.get("active", ""))

        if preset and preset in self.models_config.get("presets", {}):
            preset_config = self.models_config["presets"][preset]

            for key, value in preset_config.items():
                if key == "multimodal":
                    if isinstance(value, dict):
                        if "multimodal" not in self.vllm_config:
                            self.vllm_config["multimodal"] = {}
                        self.vllm_config["multimodal"].update(value)
                    else:
                        # presets 中 multimodal: false 仅表示关闭多模态，需保持 dict 结构
                        if "multimodal" not in self.vllm_config:
                            self.vllm_config["multimodal"] = {}
                        self.vllm_config["multimodal"]["enabled"] = bool(value)
                elif key == "description":
                    continue
                elif key in self._PRESET_FLAT_FIELDS:
                    section, subkey = self._PRESET_FLAT_FIELDS[key]
                    if section not in self.vllm_config:
                        self.vllm_config[section] = {}
                    self.vllm_config[section][subkey] = value
                elif key in ("server", "model", "hardware", "performance") and isinstance(value, dict):
                    if key not in self.vllm_config:
                        self.vllm_config[key] = {}
                    self.vllm_config[key].update(value)
                else:
                    self.vllm_config[key] = value

            self._applied_model_preset = preset

    def _apply_env_overrides(self):
        """应用环境变量覆盖"""
        mappings = {
            "VLLM_HOST": ("server", "host"),
            "VLLM_PORT": ("server", "port"),
            "MODEL_PATH": ("model", "path"),
            "SERVED_MODEL_NAME": ("server", "served_model_name"),
            "TENSOR_PARALLEL_SIZE": ("hardware", "tensor_parallel_size"),
            "GPU_MEMORY_UTILIZATION": ("hardware", "gpu_memory_utilization"),
            "MAX_MODEL_LEN": ("model", "max_model_len"),
            "MAX_NUM_SEQS": ("performance", "max_num_seqs"),
        }

        for env_var, (section, key) in mappings.items():
            value = os.getenv(env_var)
            if value is not None:
                if section not in self.vllm_config:
                    self.vllm_config[section] = {}
                try:
                    if value.isdigit():
                        self.vllm_config[section][key] = int(value)
                    elif value.replace('.', '').replace('-', '').isdigit():
                        self.vllm_config[section][key] = float(value)
                    else:
                        self.vllm_config[section][key] = value
                except ValueError:
                    self.vllm_config[section][key] = value

    def _setup_logging(self):
        """设置日志"""
        try:
            log_dir = Path(self.logging_config.get("log_dir", str(self.log_dir)))
            log_dir.mkdir(parents=True, exist_ok=True)
            self.log_dir = log_dir
        except PermissionError:
            self.log_dir = Path("/tmp/vllm_logs")
            self.log_dir.mkdir(parents=True, exist_ok=True)
            print(f"警告: 使用备用日志目录 {self.log_dir}")

        log_level = os.getenv("LOG_LEVEL", self.logging_config.get("level", "INFO"))
        log_file = self.log_dir / "vllm_manager.log"

        logging.basicConfig(
            level=getattr(logging, log_level),
            format=self.logging_config.get("format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s"),
            datefmt=self.logging_config.get("date_format", "%Y-%m-%d %H:%M:%S"),
            handlers=[
                logging.StreamHandler(sys.stdout),
                logging.FileHandler(log_file)
            ]
        )
        self.logger = logging.getLogger(__name__)

    def detect_device(self) -> str:
        """检测加速卡类型"""
        device = self.vllm_config.get("hardware", {}).get("device", "auto")
        if device != "auto":
            return device

        import shutil

        if shutil.which("nvidia-smi"):
            return "cuda"
        elif shutil.which("npu-smi"):
            return "npu"
        elif Path("/proc/driver/cambricon").exists():
            return "mlu"
        elif shutil.which("mxsmi"):
            return "gcu"
        elif shutil.which("xpu-smi"):
            return "xpu"

        return "cpu"

    def build_command(self) -> list:
        """构建 vLLM 启动命令"""
        cmd = ["vllm", "serve"]

        # 模型路径
        model_path = self.vllm_config.get("model", {}).get("path", "")
        if not model_path:
            raise ValueError("model.path 未配置，请在 config/vllm.yaml 中设置")

        if not Path(model_path).exists():
            raise ValueError(f"模型路径不存在: {model_path}")

        cmd.append(model_path)

        # 服务配置
        server = self.vllm_config.get("server", {})
        cmd.extend(["--host", server.get("host", "0.0.0.0")])
        cmd.extend(["--port", str(server.get("port", 8000))])

        if server.get("served_model_name"):
            cmd.extend(["--served-model-name", server["served_model_name"]])

        # 模型配置
        model = self.vllm_config.get("model", {})
        cmd.extend(["--dtype", model.get("dtype", "float16")])
        if model.get("trust_remote_code"):
            cmd.append("--trust-remote-code")
        if model.get("max_model_len"):
            cmd.extend(["--max-model-len", str(model["max_model_len"])])

        # 硬件配置
        hardware = self.vllm_config.get("hardware", {})
        tp_size = hardware.get("tensor_parallel_size", 1)
        if tp_size > 1:
            cmd.extend(["--tensor-parallel-size", str(tp_size)])
        cmd.extend(["--gpu-memory-utilization", str(hardware.get("gpu_memory_utilization", 0.85))])
        cmd.extend(["--block-size", str(hardware.get("block_size", 32))])
        if hardware.get("enforce_eager"):
            cmd.append("--enforce-eager")

        # 性能配置
        perf = self.vllm_config.get("performance", {})
        if perf.get("max_num_seqs"):
            cmd.extend(["--max-num-seqs", str(perf["max_num_seqs"])])
        if perf.get("max_num_batched_tokens"):
            cmd.extend(["--max-num-batched-tokens", str(perf["max_num_batched_tokens"])])
        if perf.get("enable_prefix_caching"):
            cmd.append("--enable-prefix-caching")

        # 多模态配置
        mm = self.vllm_config.get("multimodal", {})
        if not isinstance(mm, dict):
            mm = {}
        if mm.get("enabled"):
            # vLLM CLI 对该参数使用 json.loads，须为 JSON 对象，而非 image=4,video=1
            limits = {}
            if mm.get("limit_images") is not None:
                limits["image"] = mm["limit_images"]
            if mm.get("limit_videos") is not None:
                limits["video"] = mm["limit_videos"]
            if limits:
                cmd.extend(["--limit-mm-per-prompt", json.dumps(limits, separators=(",", ":"))])

            for media_path in mm.get("media_paths", []):
                if media_path and Path(media_path).exists():
                    cmd.extend(["--allowed-local-media-path", media_path])

        return cmd

    def start(self) -> bool:
        """启动服务"""
        self.logger.info("=" * 50)
        self.logger.info("启动 vLLM 服务...")

        # 检测设备
        device = self.detect_device()
        self.logger.info(f"检测到加速卡: {device}")

        # 设置设备环境变量
        device_env = {
            "cuda": "CUDA_VISIBLE_DEVICES",
            "npu": "ASCEND_RT_VISIBLE_DEVICES",
            "mlu": "MLU_VISIBLE_DEVICES",
            "gcu": "MX_VISIBLE_DEVICES",
            "xpu": "XPU_VISIBLE_DEVICES",
        }
        if device in device_env:
            gpu_ids = os.getenv(device_env[device], "0")
            os.environ[device_env[device]] = gpu_ids
            self.logger.info(f"设置 {device_env[device]}={gpu_ids}")

        # 构建命令
        try:
            cmd = self.build_command()
        except ValueError as e:
            self.logger.error(f"配置错误: {e}")
            return False

        self.logger.info(f"启动命令: {' '.join(cmd)}")

        # 创建日志文件
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        hostname = socket.gethostname()
        log_file = self.log_dir / f"vllm_{hostname}_{timestamp}.log"

        # 启动进程
        try:
            with open(log_file, 'w') as f:
                self.process = subprocess.Popen(
                    cmd,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    text=True,
                    preexec_fn=os.setsid if hasattr(os, 'setsid') else None
                )

            self.pid_file.write_text(str(self.process.pid))
            self.logger.info(f"服务已启动，PID: {self.process.pid}")
            self.logger.info(f"日志文件: {log_file}")

            # 等待服务就绪
            if self._wait_ready():
                self.logger.info("=" * 50)
                self.logger.info("服务启动成功！")
                host = self.vllm_config.get("server", {}).get("host", "0.0.0.0")
                port = self.vllm_config.get("server", {}).get("port", 8000)
                self.logger.info(f"API 地址: http://{host}:{port}/v1")
                self.logger.info(f"健康检查: http://{host}:{port}/health")
                self.logger.info("=" * 50)

                # 方案二：前台阻塞，直到子进程退出（适合 Docker 主进程）
                self.logger.info("vLLM 服务已就绪，前台等待子进程退出（Ctrl+C 或 docker stop 结束容器）")
                try:
                    self.process.wait()
                    exit_code = self.process.returncode
                    if exit_code == 0:
                        self.logger.info("vLLM 子进程正常退出")
                    else:
                        self.logger.error(f"vLLM 子进程异常退出，退出码: {exit_code}")
                    return exit_code == 0
                except KeyboardInterrupt:
                    self.logger.info("收到中断信号，准备停止 vLLM 服务...")
                    self.stop()
                    return False
            else:
                self.logger.error("服务启动超时")
                self.stop()
                return False

        except Exception as e:
            self.logger.error(f"启动失败: {e}")
            return False

    def _wait_ready(self, timeout: Optional[int] = None) -> bool:
        """等待 /health 返回 200。超时由环境变量 STARTUP_WAIT_TIMEOUT（秒）控制；勿用 VLLM_* 前缀，以免被 vLLM 当作自身配置并告警。"""
        if timeout is None:
            raw = os.getenv("STARTUP_WAIT_TIMEOUT", "1200")
            try:
                timeout = max(60, int(raw))
            except ValueError:
                timeout = 1200

        host = self.vllm_config.get("server", {}).get("host", "0.0.0.0")
        port = self.vllm_config.get("server", {}).get("port", 8000)
        url = f"http://{host}:{port}/health"

        self.logger.info(f"等待服务就绪（最长 {timeout} 秒），大模型/多模态首次加载可能较慢")
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                import requests
                response = requests.get(url, timeout=5)
                if response.status_code == 200:
                    return True
            except:
                pass

            if self.process and self.process.poll() is not None:
                self.logger.error(f"进程异常退出，退出码: {self.process.returncode}")
                return False

            time.sleep(5)

        return False

    def stop(self):
        """停止服务"""
        self.logger.info("停止 vLLM 服务...")

        if self.process:
            try:
                if hasattr(os, 'killpg') and self.process.pid:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                else:
                    self.process.terminate()
                self.process.wait(timeout=30)
                self.logger.info("服务已停止")
            except (subprocess.TimeoutExpired, ProcessLookupError):
                try:
                    self.process.kill()
                    self.process.wait()
                    self.logger.info("服务已强制停止")
                except:
                    pass
            finally:
                self.process = None

        if self.pid_file.exists():
            self.pid_file.unlink()

    def status(self) -> dict:
        """获取状态"""
        status = {
            "running": False,
            "pid": None,
            "healthy": False,
            "url": None,
            "project_root": str(self.project_root),
            "config_dir": str(self.config_dir),
            "log_dir": str(self.log_dir)
        }

        if self.pid_file.exists():
            try:
                pid = int(self.pid_file.read_text().strip())
                try:
                    os.kill(pid, 0)
                    status["running"] = True
                    status["pid"] = pid
                except (ProcessLookupError, OSError):
                    pass
            except:
                pass

        if status["running"]:
            host = self.vllm_config.get("server", {}).get("host", "0.0.0.0")
            port = self.vllm_config.get("server", {}).get("port", 8000)
            status["url"] = f"http://{host}:{port}"

            try:
                import requests
                response = requests.get(f"{status['url']}/health", timeout=5)
                status["healthy"] = response.status_code == 200
            except:
                pass

        return status


def main():
    parser = argparse.ArgumentParser(description="vLLM 服务管理")
    parser.add_argument("action", choices=["start", "stop", "restart", "status"])
    parser.add_argument("--config-dir", help="配置文件目录（可选）")
    parser.add_argument("--log-dir", help="日志目录（可选）")

    args = parser.parse_args()

    if args.log_dir:
        os.environ["LOG_DIR"] = args.log_dir

    service = VLLMService(config_dir=args.config_dir)

    if args.action == "start":
        sys.exit(0 if service.start() else 1)
    elif args.action == "stop":
        service.stop()
        sys.exit(0)
    elif args.action == "restart":
        service.stop()
        time.sleep(2)
        sys.exit(0 if service.start() else 1)
    elif args.action == "status":
        status = service.status()
        print(f"\n=== vLLM 服务状态 ===")
        print(f"状态: {'运行中' if status['running'] else '未运行'}")
        if status['running']:
            print(f"PID: {status['pid']}")
            print(f"健康: {'正常 ✓' if status['healthy'] else '异常 ✗'}")
            print(f"地址: {status['url']}")
        print(f"\n部署信息:")
        print(f"  项目根目录: {status['project_root']}")
        print(f"  配置目录: {status['config_dir']}")
        print(f"  日志目录: {status['log_dir']}")
        print()
        sys.exit(0)


if __name__ == "__main__":
    main()