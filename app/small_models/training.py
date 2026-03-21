from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Literal, Optional

from app.core.logging import get_logger
from app.small_models.registry import SmallModelRegistry

logger = get_logger(__name__)

TrainingStatus = Literal["pending", "running", "succeeded", "failed"]


@dataclass
class SmallModelTrainingConfig:
    """
    小模型训练配置。

    说明：
    - 当前实现以最小可运行的 PyTorch 训练循环为例，未强绑定具体算法；
    - 生产环境可根据不同 task_type（helmet/phone_call 等）接入真实模型与数据加载逻辑。
    """

    model_name: str
    dataset_path: str
    epochs: int = 1
    batch_size: int = 4
    learning_rate: float = 1e-3
    log_dir: Optional[str] = None
    output_dir: Optional[str] = None


@dataclass
class SmallModelTrainingJob:
    job_id: str
    config: SmallModelTrainingConfig
    status: TrainingStatus = "pending"
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    log_dir: Optional[str] = None
    output_dir: Optional[str] = None
    error: Optional[str] = None


class SmallModelTrainingService:
    """
    小模型训练服务（企业级骨架实现）。

    能力：
    - 接收训练任务配置并在后台线程中执行训练循环；
    - 使用 TensorBoard SummaryWriter 记录 loss 等基础指标；
    - 在内存中维护训练任务元数据（状态、时间、日志路径、错误信息）；
    - 后续可在此基础上接入真实算法与 train_admin API。
    """

    def __init__(self) -> None:
        self._jobs: Dict[str, SmallModelTrainingJob] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._registry = SmallModelRegistry()

    def start_training(self, job_id: str, cfg: SmallModelTrainingConfig) -> None:
        with self._lock:
            if job_id in self._threads:
                logger.warning("small model training job already exists: %s", job_id)
                return

            # 解析默认 log_dir 与 output_dir
            log_dir = cfg.log_dir or f"runs/small_models/{job_id}"
            # 若未显式指定 output_dir，则优先使用注册表中的 weights_path 所在目录
            output_dir = cfg.output_dir
            if not output_dir:
                meta = self._registry.get(cfg.model_name)
                if meta and meta.weights_path:
                    output_dir = str(Path(meta.weights_path).parent)
                else:
                    output_dir = f"models/small/{cfg.model_name}"

            job = SmallModelTrainingJob(
                job_id=job_id,
                config=cfg,
                status="pending",
                log_dir=log_dir,
                output_dir=output_dir,
            )
            self._jobs[job_id] = job

            th = threading.Thread(target=self._run_job, args=(job_id,), daemon=True)
            self._threads[job_id] = th
            th.start()

    def get_job(self, job_id: str) -> Optional[SmallModelTrainingJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> Dict[str, SmallModelTrainingJob]:
        with self._lock:
            return dict(self._jobs)

    def _run_job(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if not job:
            return

        cfg = job.config
        logger.info(
            "start small model training job=%s, model=%s, dataset=%s",
            job_id,
            cfg.model_name,
            cfg.dataset_path,
        )

        job.status = "running"
        job.started_at = time.time()

        try:
            # 延迟导入重依赖，避免在未安装时影响其他模块
            try:
                import torch  # type: ignore[import-not-found]
                from torch import nn, optim  # type: ignore[import-not-found]
                from torch.utils.data import DataLoader, Dataset  # type: ignore[import-not-found]
                from torch.utils.tensorboard import SummaryWriter  # type: ignore[import-not-found]
            except Exception as exc:  # noqa: BLE001
                msg = f"PyTorch/TensorBoard not available, skip training. error={exc}"
                logger.warning(msg)
                job.status = "failed"
                job.error = msg
                return

            # 简单占位数据集与模型，确保训练循环可运行
            class DummyDataset(Dataset):  # type: ignore[misc]
                def __len__(self) -> int:
                    return 32

                def __getitem__(self, idx):  # type: ignore[override]
                    x = torch.randn(3, 64, 64)
                    y = torch.randint(0, 2, ()).float()
                    return x, y

            class DummyModel(nn.Module):  # type: ignore[misc]
                def __init__(self) -> None:
                    super().__init__()
                    self.flatten = nn.Flatten()
                    self.fc = nn.Linear(3 * 64 * 64, 1)

                def forward(self, x):  # type: ignore[override]
                    x = self.flatten(x)
                    return torch.sigmoid(self.fc(x))

            ds = DummyDataset()
            dl = DataLoader(ds, batch_size=cfg.batch_size, shuffle=True)
            model = DummyModel()
            criterion = nn.BCELoss()
            optimizer = optim.Adam(model.parameters(), lr=cfg.learning_rate)

            log_dir_path = Path(job.log_dir or ".")
            log_dir_path.mkdir(parents=True, exist_ok=True)
            writer = SummaryWriter(log_dir=str(log_dir_path))

            global_step = 0
            for epoch in range(cfg.epochs):
                epoch_loss = 0.0
                for batch_idx, (x, y) in enumerate(dl):
                    optimizer.zero_grad()
                    logits = model(x)
                    loss = criterion(logits.view_as(y), y)
                    loss.backward()
                    optimizer.step()

                    loss_value = float(loss.detach().item())
                    epoch_loss += loss_value
                    writer.add_scalar("dajia/loss", loss_value, global_step)
                    global_step += 1

                avg_loss = epoch_loss / max(len(dl), 1)
                logger.info(
                    "job=%s epoch=%s/%s avg_loss=%.4f",
                    job_id,
                    epoch + 1,
                    cfg.epochs,
                    avg_loss,
                )

            writer.flush()
            writer.close()

            # 保存示例权重
            output_dir_path = Path(job.output_dir or ".")
            output_dir_path.mkdir(parents=True, exist_ok=True)
            weights_path = output_dir_path / "best.pt"
            try:
                torch.save(model.state_dict(), weights_path)  # type: ignore[arg-type]
                logger.info("small model weights saved to %s", weights_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("failed to save small model weights for job=%s: %s", job_id, exc)

            job.status = "succeeded"
        except Exception as exc:  # noqa: BLE001
            logger.exception("small model training job failed: job_id=%s error=%s", job_id, exc)
            job.status = "failed"
            job.error = str(exc)
        finally:
            job.finished_at = time.time()
            logger.info("finish small model training job=%s status=%s", job_id, job.status)

