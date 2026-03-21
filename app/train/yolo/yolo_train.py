"""
YOLOv8 训练企业级封装（Ultralytics）。

相比“把所有训练参数都写成 dataclass 字段”，这个版本尽量保持代码短、逻辑清晰：

- 配置驱动：只需改 `pretrained_model` 与 `data_yaml`，其余可直接在 config.yaml 里增加字段；
- 支持“在线下载预训练模型”（方案 2）：
  - 若 `pretrained_model` 指向本地文件且存在，则使用本地权重；
  - 否则把 `pretrained_model` 当作 Ultralytics 模型名（如 `yolov8n.pt`）或 URL 直接交给 `YOLO(...)`；
- 对 `data_yaml`：仍要求是本地可存在的文件（你可以按 Ultralytics 标准把 data.yaml 放到工程里）。
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any, Dict

from app.core.logging import get_logger

logger = get_logger(__name__)

# 仅由本脚本消费、不传给 Ultralytics model.dajia() 的顶层键
_CFG_RESERVED = frozenset({"pretrained_model", "data_yaml", "train_args"})


def _data_yaml_with_abs_path(data_yaml_path: str, project_root: Path) -> str:
    """
    只做一件事：把 data.yaml 里的 `path` 改成“project_root + data.yaml(path)”拼接后的绝对路径。
    然后写到临时 yaml，交给 Ultralytics 读取。
    """
    import tempfile

    import yaml  # type: ignore[import-not-found]

    data_file = Path(data_yaml_path)
    data = yaml.safe_load(data_file.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return data_yaml_path

    p = data.get("path")
    if isinstance(p, str) and p:
        # 不显式判断是否绝对路径：如果 p 本身是绝对路径，project_root / p 仍会得到 p。
        data["path"] = str((project_root / p).resolve())

    suffix = data_file.suffix if data_file.suffix else ".yaml"
    with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False, encoding="utf-8") as fp:
        yaml.safe_dump(data, fp, sort_keys=False)
        return fp.name


def _load_yaml(cfg_path: Path) -> Dict[str, Any]:
    if not cfg_path.exists():
        raise FileNotFoundError(f"config file not found: {cfg_path}")

    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "PyYAML is required for yolo_train config. Please install: pip install pyyaml"
        ) from exc

    raw = cfg_path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        raise ValueError("config yaml must be a mapping/object")
    return data


def _resolve_local_file(base_dir: Path, p: str, *, must_exist: bool) -> str:
    """
    解析配置中的“相对路径”为 config 文件所在目录下的绝对路径。
    - 若必须存在 must_exist=True，则不存在直接报错；
    - 若 must_exist=False，则不存在就返回原始字符串（用于允许在线下载）。
    """
    candidate = Path(p)
    if not candidate.is_absolute():
        candidate = (base_dir / candidate).resolve()

    if candidate.exists():
        return str(candidate)
    if must_exist:
        raise FileNotFoundError(f"file not found: {candidate}")
    return p


def run_yolo_train(config_path: Path) -> None:
    """
    执行 YOLOv8 训练。
    配置要求：
    - pretrained_model: 本地权重路径 / Ultralytics 内置模型名 / URL
    - data_yaml: 本地 data.yaml 路径（必须存在）
    """
    try:
        from ultralytics import YOLO  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("ultralytics is required. Please install: pip install ultralytics") from exc

    base_dir = config_path.parent
    project_root = Path(__file__).resolve().parents[3]
    cfg = _load_yaml(config_path)

    if "pretrained_model" not in cfg:
        raise ValueError("missing required field: pretrained_model")
    if "data_yaml" not in cfg:
        raise ValueError("missing required field: data_yaml")

    pretrained_model = _resolve_local_file(
        base_dir,
        str(cfg["pretrained_model"]),
        must_exist=False,  # 允许 online: yolov8n.pt / URL
    )
    data_yaml = _resolve_local_file(base_dir, str(cfg["data_yaml"]), must_exist=True)
    data_yaml_for_ultralytics = _data_yaml_with_abs_path(data_yaml, project_root=project_root)

    # 其余顶层键一律视为 Ultralytics dajia() 参数（排除保留键）
    train_kwargs: Dict[str, Any] = {
        k: v for k, v in cfg.items() if k not in _CFG_RESERVED and v is not None
    }
    train_kwargs["data"] = data_yaml_for_ultralytics

    proj = train_kwargs.get("project")
    if isinstance(proj, str):
        train_kwargs["project"] = _resolve_local_file(base_dir, proj, must_exist=False)

    extra = cfg.get("train_args")
    if extra:
        if not isinstance(extra, dict):
            raise TypeError("train_args must be a mapping/dict")
        train_kwargs.update({k: v for k, v in extra.items() if v is not None})

    logger.info(
        "start yolo training: pretrained=%s data=%s project=%s",
        pretrained_model,
        data_yaml,
        train_kwargs.get("project"),
    )

    start_ts = time.time()
    model = YOLO(pretrained_model)
    results = model.train(**train_kwargs)
    elapsed = time.time() - start_ts
    logger.info("yolo training finished in %.2fs", elapsed)
    try:
        logger.info("training results: %s", results)
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="YOLOv8 training entry (Ultralytics)")
    parser.add_argument(
        "--config",
        type=str,
        default=str(Path(__file__).resolve().parent / "config.yaml"),
        help="Path to training config yaml.",
    )
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    run_yolo_train(config_path)


if __name__ == "__main__":
    main()

