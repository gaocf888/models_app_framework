"""
超级简单：RTSP 解码 -> 队列 -> YOLO 行为识别（测试脚本）

用法（在项目根目录执行）：
  python app/train/yolo/video_rtsp_yolo_queue_test.py \\
    --rtsp rtsp://user:pass@ip:554/stream \\
    --model runs_bak_dajia

说明：
  - --model 为 trained_models 下的子目录名；脚本会在该目录下递归查找 weights/best.pt
  - 必须从同目录下 dataset.yaml 读取 names，日志中的类别名一律用 yaml 中的定义
  - 依赖：pip install ultralytics opencv-python pyyaml
"""

from __future__ import annotations

import argparse
import logging
import queue
import threading
import time
from pathlib import Path

# 本脚本所在目录 = app/train/yolo
_HERE = Path(__file__).resolve().parent
_TRAINED_ROOT = _HERE / "trained_models"


def setup_log() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_class_names_from_dataset_yaml(yaml_path: Path) -> dict[int, str]:
    """
    从 dataset.yaml 读取 names，转为 {class_id: 显示名}。
    支持 Ultralytics 常见两种写法：
      names:
        0: foo
      或
      names: [foo, bar]
    """
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError("需要 pyyaml：pip install pyyaml") from e

    text = yaml_path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    raw = data.get("names")
    if raw is None:
        raise ValueError(f"{yaml_path} 中缺少 names 字段")

    if isinstance(raw, dict):
        out: dict[int, str] = {}
        for k, v in raw.items():
            try:
                ik = int(k)
            except (TypeError, ValueError):
                continue
            out[ik] = str(v).strip()
        if not out:
            raise ValueError(f"{yaml_path} 中 names 字典无法解析为类别 id")
        return out

    if isinstance(raw, list):
        return {i: str(n).strip() for i, n in enumerate(raw)}

    raise ValueError(f"{yaml_path} 中 names 格式不支持（应为 dict 或 list）")


def find_weights_in_model_dir(model_dir: Path) -> Path:
    """在行为模型目录下找 best.pt（优先常见 Ultralytics 输出路径）。"""
    candidates = list(model_dir.rglob("weights/best.pt"))
    if not candidates:
        candidates = list(model_dir.rglob("best.pt"))
    if not candidates:
        raise FileNotFoundError(f"在 {model_dir} 下未找到 best.pt，请检查 trained_models 目录结构")
    # 若有多个，取最近修改的一个
    return max(candidates, key=lambda p: p.stat().st_mtime)


def decoder_thread(rtsp_url: str, frame_queue: queue.Queue, stop: threading.Event) -> None:
    """从 RTSP 拉流，解码成帧后放入队列（队列满则丢弃最旧，只保留最新）。"""
    import cv2

    log = logging.getLogger("decoder")
    log.info("解码线程启动，连接 RTSP: %s", rtsp_url)
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        log.error("无法打开 RTSP，请检查地址与网络")
        stop.set()
        return

    frame_id = 0
    while not stop.is_set():
        ok, frame = cap.read()
        if not ok:
            log.warning("读帧失败，1 秒后重试")
            time.sleep(1)
            continue
        frame_id += 1
        try:
            # 队列满时丢弃旧帧，避免内存堆积、延迟越来越大
            while frame_queue.full():
                try:
                    frame_queue.get_nowait()
                except queue.Empty:
                    break
            frame_queue.put_nowait((frame_id, frame))
            if frame_id % 100 == 0:
                log.info("已解码入队 frame_id=%s queue_size=%s", frame_id, frame_queue.qsize())
        except Exception as e:
            log.exception("入队异常: %s", e)

    cap.release()
    log.info("解码线程结束")


def yolo_thread(
    weights_path: Path,
    frame_queue: queue.Queue,
    stop: threading.Event,
    device: str,
    conf: float,
    class_names: dict[int, str],
) -> None:
    """从队列取帧，调用 YOLO 推理并打印结果（类别显示名来自 dataset.yaml）。"""
    from ultralytics import YOLO

    log = logging.getLogger("yolo")
    log.info("推理线程启动，加载权重: %s", weights_path)
    log.info("日志展示类别名来自 dataset.yaml，共 %s 类: %s", len(class_names), class_names)
    model = YOLO(str(weights_path))

    infer_count = 0
    while not stop.is_set():
        time.sleep(3)
        try:
            fid, frame = frame_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        t0 = time.perf_counter()
        # Ultralytics 支持 numpy 图像（BGR）
        results = model.predict(
            source=frame,
            device=device,
            conf=conf,
            verbose=False,
        )
        dt_ms = (time.perf_counter() - t0) * 1000
        infer_count += 1

        r0 = results[0] if results else None
        if r0 is None or r0.boxes is None or len(r0.boxes) == 0:
            log.info("frame_id=%s 未检测到目标 耗时=%.1fms", fid, dt_ms)
            continue

        lines = []
        for b in r0.boxes:
            cls_id = int(b.cls[0]) if b.cls is not None else -1
            cname = class_names.get(cls_id, f"id_{cls_id}(yaml无此id)")
            score = float(b.conf[0]) if b.conf is not None else 0.0
            lines.append(f"{cname}:{score:.2f}")
        log.info(
            "frame_id=%s 检测到 %s 个目标 [%s] 耗时=%.1fms",
            fid,
            len(r0.boxes),
            ", ".join(lines),
            dt_ms,
        )

        # 检测到行为后弹框显示图片
        # import cv2
        # vis = r0.plot()  # Ultralytics 画好框的 BGR 图
        # cv2.imshow("behavior_alert", vis)
        # cv2.waitKey(1)  # 必须调用，窗口才会刷新；可按需改成 waitKey(0) 每帧暂停


        if infer_count % 20 == 0:
            log.info("已累计推理 %s 帧", infer_count)



    log.info("推理线程结束")


def main() -> None:
    setup_log()
    log = logging.getLogger("main")

    p = argparse.ArgumentParser(description="RTSP + 队列 + YOLO 简单测试")
    p.add_argument("--rtsp", default="rtsp://192.168.2.45:554/ch01.264", help="RTSP 地址")
    p.add_argument(
        "--model",
        default="runs_bak_jushou",
        help="trained_models 下的子目录名，例如 runs_bak_dajia",
    )
    p.add_argument("--device", default="cpu", help="GPU 编号或 cpu")
    p.add_argument("--conf", type=float, default=0.25, help="置信度阈值")
    p.add_argument("--queue-size", type=int, default=3, help="帧队列长度（满则丢旧帧）")
    p.add_argument("--seconds", type=int, default=0, help="运行秒数，0 表示一直跑（Ctrl+C 结束）")
    args = p.parse_args()

    model_dir = _TRAINED_ROOT / args.model
    if not model_dir.is_dir():
        raise SystemExit(f"模型目录不存在: {model_dir}")

    dataset_yaml = model_dir / "dataset.yaml"
    if not dataset_yaml.is_file():
        raise SystemExit(f"必须存在 dataset.yaml（用于读取 names）: {dataset_yaml}")

    try:
        class_names = load_class_names_from_dataset_yaml(dataset_yaml)
    except Exception as e:
        raise SystemExit(f"读取 dataset.yaml 的 names 失败: {e}") from e

    log.info("已从 dataset.yaml 加载类别名: %s", dataset_yaml)

    weights_path = find_weights_in_model_dir(model_dir)
    log.info("使用权重: %s", weights_path)

    frame_queue: queue.Queue = queue.Queue(maxsize=max(1, args.queue_size))
    stop = threading.Event()

    t_dec = threading.Thread(
        target=decoder_thread,
        args=(args.rtsp, frame_queue, stop),
        daemon=True,
    )
    t_yolo = threading.Thread(
        target=yolo_thread,
        args=(weights_path, frame_queue, stop, args.device, args.conf, class_names),
        daemon=True,
    )

    log.info("启动解码线程与推理线程…")
    t_dec.start()
    t_yolo.start()

    try:
        if args.seconds > 0:
            log.info("将运行 %s 秒后自动停止", args.seconds)
            time.sleep(args.seconds)
        else:
            log.info("持续运行中，按 Ctrl+C 停止")
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        log.info("收到中断信号，正在停止…")
    finally:
        stop.set()
        t_dec.join(timeout=3)
        t_yolo.join(timeout=3)
        log.info("已退出")


if __name__ == "__main__":
    main()
