"""
权重推理/评估快速测试脚本（Ultralytics）。

用途：训练结束后，拿到 `best.pt` 或 `last.pt`，快速验证效果是否可用。

支持两种模式（按参数触发）：
1) 验证集评估（推荐）
   - 调用：model.val(data=..., imgsz=...)
   - 输出：mAP50 / mAP50-95 等指标

2) 单次推理（图片/目录/视频）
   - 调用：model.predict(source=..., save=True, conf=..., iou=...)
   - 输出：保存预测结果图片到 runs 目录

用法示例：
1. 跑验证集评估
   python app/train/yolo/weights_infer_test.py \
     --val \
     --weights runs/detect/runs/yolo/helmet_exp/weights/best.pt \
     --data app/train/yolo/datasets/processed/dajia/dataset.yaml \
     --device 0

2. 跑对某个图片/目录推理并保存结果
   python app/train/yolo/weights_infer_test.py \
     --weights runs/detect/runs/yolo/helmet_exp/weights/best.pt \
     --source /path/to/images/val \
     --save
     --device 0
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser("YOLO weights infer/val quick test")
    p.add_argument("--weights", type=str, required=True, help="Trained weights path, e.g. .../best.pt")
    p.add_argument("--data", type=str, default=None, help="data.yaml path (required for --val)")
    p.add_argument("--source", type=str, default=None, help="Image/dir/video path for prediction")

    p.add_argument("--device", type=str, default="0", help="Device, e.g. '0' or 'cpu'")
    p.add_argument("--imgsz", type=int, default=640, help="Inference/val image size")
    p.add_argument("--batch", type=int, default=16, help="Batch size for val")

    p.add_argument("--conf", type=float, default=0.25, help="Confidence threshold for predict")
    p.add_argument("--iou", type=float, default=0.7, help="IoU threshold for NMS")

    p.add_argument("--val", action="store_true", help="Run validation evaluation (model.val)")
    p.add_argument("--save", action="store_true", help="Save prediction results (model.predict)")
    p.add_argument("--project", type=str, default="runs/weights_test", help="Ultralytics runs project dir")
    p.add_argument("--name", type=str, default="exp", help="Ultralytics runs name")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    try:
        from ultralytics import YOLO  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Missing dependency: ultralytics. Install with: pip install ultralytics") from exc

    weights_path = str(Path(args.weights).resolve())
    if not Path(weights_path).exists():
        raise FileNotFoundError(f"--weights not found: {weights_path}")

    model = YOLO(weights_path)

    # 1) Validation evaluation
    if args.val:
        if not args.data:
            raise ValueError("--val requires --data (data.yaml)")
        data_path = str(Path(args.data).resolve())
        if not Path(data_path).exists():
            raise FileNotFoundError(f"--data not found: {data_path}")

        # Ultralytics 会自动在内部选择 val split（基于 data.yaml 的 val 字段）
        model.val(data=data_path, imgsz=args.imgsz, device=args.device, batch=args.batch)

    # 2) Prediction
    if args.source:
        source_path = str(Path(args.source).resolve())
        if not Path(source_path).exists():
            raise FileNotFoundError(f"--source not found: {source_path}")

        model.predict(
            source=source_path,
            imgsz=args.imgsz,
            device=args.device,
            conf=args.conf,
            iou=args.iou,
            save=args.save,
            project=args.project,
            name=args.name,
        )


if __name__ == "__main__":
    main()

