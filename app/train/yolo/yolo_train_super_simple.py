from pathlib import Path

from ultralytics import YOLO

try:
    import torch_mlu
    from torch_mlu.utils.model_transfer import transfer
except ImportError:
    pass

if __name__ == "__main__":
    project_root = Path(__file__).resolve().parents[3]
    model = YOLO(str(project_root / "app/train/yolo/weights/pretrained/yolov8s.pt"))
    model.train(
        data=str(project_root / "app/train/yolo/datasets/processed/dajia/dataset.yaml"),
        imgsz=640,
        epochs=150,
        batch=30,
        workers=16,
        device="0,1,2,3,4,5",
        optimizer="SGD",
        close_mosaic=10,
        resume=False,
        project="runs/train",
        name="exp",
        single_cls=False,
        cache=False,
        amp=False,
    )

    model.val()
