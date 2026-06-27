# 小模型（Small Models）模块说明

本目录为**视觉小模型**的核心实现：算法策略、推理编排、通道运行时、预训练权重与训练骨架。HTTP 接口在 `app/api/small_models/`，业务封装在 `app/services/small_models/`。

---

## 1. 目录结构

```
app/small_models/
├── README.md                    # 本文档
├── algorithm_registry.py        # 算法配置：加载 configs/small_model_algorithms.yaml
├── inference_engine.py          # 推理编排：合并配置 → 选策略 → 证据/回调
├── registry.py                  # 模型元数据：加载 configs/small_models.yaml
│
├── runtime/                     # 多通道运行时
│   ├── channel_manager.py       # 通道生命周期、消息队列
│   └── workers.py               # 解码线程 + 推理线程
│
├── common/                      # 跨算法通用能力
│   ├── roi.py                   # ROI 过滤（检测框 / 人脸共用）
│   ├── evidence.py              # 触发后截图、短视频片段
│   └── callback_client.py       # 告警 HTTP 回调
│
├── training/                    # 训练任务骨架（可扩展为真实训练管线）
│   └── training.py
│
├── pretrained/                  # 离线预训练 / 自训权重（.pt、InsightFace 等）
│   └── insightface/
│
└── strategy/                    # 算法策略（按分层组织）
    ├── base/                    # 接口 + 底层工具
    │   ├── base.py              # Detection / StrategyResult / SmallModelStrategy
    │   ├── _yolo_utils.py       # Ultralytics YOLOv8 加载与推理
    │   ├── _insightface_utils.py
    │   └── _spatial_rules.py    # 人-机空间约束等几何规则
    ├── l1/                      # L1 常规目标检测
    ├── l2/                      # L2 常规行为检测
    ├── l3/                      # L3 复杂时空行为
    ├── l4/                      # L4 人脸识别
    ├── specialized/             # 独立专项策略
    └── face/                    # 人脸领域模块（库、比对、pipeline）
```

**配置与外部依赖**

| 资源 | 路径 |
|------|------|
| 算法主表（`algor_type` → 策略/权重/阈值） | `configs/small_model_algorithms.yaml` |
| 模型注册表（可选） | `configs/small_models.yaml` |
| 权重放置约定 | `pretrained/README.md` |
| 通道 API | `app/api/small_models/small_model.py` → `/small-model/*` |
| 人脸库 API | `app/api/small_models/face_gallery.py` → `/face/*` |

---

## 2. 调用链路（从 API 到算法）

```
POST /small-model/channel/start
    → SmallModelChannelService（app/services/small_models/）
    → ChannelManager.start_channel（runtime/）
    → workers：解码线程入队 BGR 帧 → 推理线程
    → SmallModelInferenceEngine.infer（inference_engine.py）
        → merge_algorithm_config（YAML + API 覆盖）
        → 按 strategy 字段实例化 *Strategy
        → strategy.infer(frame, config, context)
        → 冷却 / 证据保存 / callback
```

新增算法时，通常**只需改配置 + 必要时新增策略类**，不必动通道与引擎主流程。

---

## 3. 策略分层与市面常见算法对照

框架将业务算法分为 **L1～L4** 与 **独立专项**。下表说明各层职责、典型模型栈，以及市面上常见的对应算法/产品形态。

### 3.1 L1 — 常规目标检测（`strategy/l1/`）

| 项目 | 说明 |
|------|------|
| **策略类** | `ObjectDetectionStrategy` |
| **实现文件** | `strategy/l1/object_detection.py` |
| **技术路径** | YOLOv8（Ultralytics）单阶段检测 → `class_filter` + `roi` |
| **配置编号建议** | `40xxx` |
| **触发条件** | 检出目标即触发（经类别过滤、ROI 过滤后） |

**市面常见算法 / 场景**

| 场景 | 常见模型 / 方案 | 在本框架中的用法 |
|------|-----------------|------------------|
| 通用 80 类目标（人、车、猫狗等） | YOLOv8/v5 COCO 预训练（`yolov8s.pt`） | `40104`，不设或按需设 `class_filter` |
| 安全帽 / 反光衣 / 灭火器（PPE） | 社区或自训 YOLO（Roboflow、自标注数据集） | `40101`–`40103`，换 `weights_path` + `class_filter` |
| 行人 / 车辆统计 | COCO person(0)、vehicle 类，或专用 Re-ID 前检测 | `40111`–`40113` 等，过滤 `class_ids` |
| 工业缺陷 / 异物 | 自训 YOLO、YOLO-OBB（旋转框） | 新增 `40xxx` 条目；OBB 需确认框格式兼容 |
| 可选：实例分割 / 姿态 | `yolov8s-seg.pt`、`yolov8s-pose.pt` | `40115`/`40116`；**当前仅消费检测框**，mask/keypoints 未进业务逻辑 |

> L1 是**最常用入口**：多数「有没有某物体/某类别」的需求，换权重 + YAML 即可，无需写代码。

---

### 3.2 L2 — 常规行为检测（`strategy/l2/`）

| 项目 | 说明 |
|------|------|
| **策略类** | `RegularBehaviorDetectionStrategy` |
| **实现文件** | `strategy/l2/regular_behavior_detection.py` |
| **技术路径** | 与 L1 **相同 YOLO 管线**（内部复用 `run_yolo_detection_pipeline`） |
| **配置编号建议** | `41xxx`（行为类，非接打电话专项） |
| **与 L1 区别** | **业务语义**与默认证据策略；适合「单帧可判定的行为」专用检测头 |

**市面常见算法 / 场景**

| 场景 | 常见模型 / 方案 | 在本框架中的用法 |
|------|-----------------|------------------|
| 口罩佩戴 | 自训 YOLO 二类（戴口罩/未戴） | `41102`，`mask.pt` + `class_filter` |
| 吸烟 | 自训 YOLO 或烟火检测小模型 | `41103` |
| 打架 / 异常姿态 | 自训行为检测、SlowFast/I3D（视频级，需另扩策略） | `41104` 占位为 YOLO 单帧头 |
| 睡岗 / 离岗（单帧辅助） | 自训「躺/趴/空岗」检测 | `41105`；跨帧时长可叠加 L3 `dwell` |
| 打电话（纯端到端） | 自训 `call.pt` 单类行为头 | 可用 L2，但更推荐 **独立专项** `CallingDetectionStrategy`（见 3.5） |

> L2 适合：**一个自训权重、一帧内能出结论**的行为。底层与 L1 一致，便于审计与运维统一。

---

### 3.3 L3 — 复杂时空行为（`strategy/l3/`）

| 项目 | 说明 |
|------|------|
| **策略类** | `ComplexBehaviorDetectionStrategy` |
| **实现文件** | `strategy/l3/complex_behavior_detection.py` |
| **技术路径** | YOLO 检测 + **跨帧规则**（通道内维护轻量状态） |
| **配置编号建议** | `42xxx` |
| **`complex_mode`** | `none` / `dwell` / `line_cross` / `zone_intrusion` |

**市面常见算法 / 场景**

| 场景 | 市面典型方案 | 本框架 `complex_mode` |
|------|--------------|------------------------|
| 区域滞留 / 停留超时 | 传统 CV 背景建模 + 轨迹；或检测 + 轨迹关联（DeepSORT 等） | `dwell`：`dwell_polygon` + `dwell_seconds` |
| 绊线 / 越界计数 | 电子围栏、IVS 绊线分析 | `line_cross`：`line_cross_line` 两点 |
| 禁区入侵 | 周界防范、ROI 入侵 | `zone_intrusion`：多边形顶点 |
| 仅检测不涉及时序 | 同 L1 | `complex_mode: none` |

**市面常见但需扩展代码的能力**（当前未内置，需新策略或改 L3）：

- 跌倒检测：姿态估计（OpenPose、MediaPipe）+ 时序分类  
- 人群密度 / 聚集：密度图、P2PNet 等  
- 多目标跟踪 + 行为：ByteTrack + 行为分类  

> L3 适合：**在 L1 检测基础上，加「在哪、待多久、过线了吗」**。权重仍多为 YOLO 行人/车辆检测。

---

### 3.4 L4 — 人脸识别（`strategy/l4/` + `strategy/face/`）

| 项目 | 说明 |
|------|------|
| **策略类** | `FaceRecognitionStrategy` |
| **实现文件** | `strategy/l4/face_recognition.py` |
| **领域模块** | `strategy/face/`（库 CRUD、1:N 比对、pipeline） |
| **技术路径** | InsightFace（检测 + 对齐 + embedding）+ 人脸库 1:N |
| **配置编号建议** | `43xxx` |
| **关联 API** | `/face/*` 录入、识别、核验 |

**市面常见算法 / 场景**

| 场景 | 常见方案 | 在本框架中的用法 |
|------|----------|------------------|
| 门禁 / 考勤白名单 | InsightFace、ArcFace、商汤/旷视 SDK | `gallery_id` + `match_threshold` |
| 陌生人告警 | 1:N 未命中 + `face_alert_mode: unknown` | `43102` 等 |
| 1:1 人证核验 | 双图 cosine 相似度 | `FaceGalleryService.verify_*` |
| 大规模 1:N | Faiss / Milvus 向量库 | `FACE_VECTOR_BACKEND=milvus`（见 `face_db-deploy/`）；默认 `local`（numpy/faiss） |

> L4 与 YOLO 分层**并列**，不走 `weights_path` 的 YOLO 链路，而走 `face_model_pack` / `face_model_root`。

---

### 3.5 独立专项 — 接打电话（`strategy/specialized/`）

| 项目 | 说明 |
|------|------|
| **策略类** | `CallingDetectionStrategy` |
| **实现文件** | `strategy/specialized/calling_detection.py` |
| **为何独立** | 融合 **L1 多类检测 + 空间规则 + 可选 L2 回落**，单文件内模式切换，不适合强行归入 L2 |
| **配置编号** | `40417`（历史）、`41101`、`41201` 等 |

**两种模式**

| 模式 | 市面思路 | 配置 |
|------|----------|------|
| `spatial`（推荐） | 先检人 + 手机（COCO 67 类 cell phone），再判手机是否在人体上半身区域 | 默认；可 `calling_fallback_end_to_end: true` |
| `end_to_end` | 端到端行为检测（自训 call 类） | `calling_mode: end_to_end`，权重 `call.pt` |

**类似「独立专项」、未来可新增的策略类型**

- 双模型融合（人 + 物 + 规则），如「手持危险品」  
- 需专用后处理 pipeline（非单帧 YOLO）  
- 与 L3 状态机强耦合的复合行为  

新增时放在 `strategy/specialized/`，并在 `inference_engine._STRATEGY_CLASSES` 注册。

---

## 4. 新增算法开发指南

### 4.1 选型：我该用哪一层？

```
需要检「有没有某物体/类别」？          → L1（多数情况只改 YAML）
需要检「吸烟/口罩」等单帧行为头？      → L2 或 自训权重 + L1/L2 均可
需要「滞留/绊线/禁区」？              → L3 + complex_mode
需要「是谁 / 是否陌生人」？            → L4 + 人脸库 API
需要「人+手机+空间规则+回落」？        → 独立专项（参考 calling_detection）
需要全新模型栈（如纯 Transformer）？  → 新 Strategy 类 + 注册
```

### 4.2 仅配置即可（L1 / L2 / L3 常规）

1. 将权重放入 `pretrained/`（或挂载目录），参见 `pretrained/README.md`。  
2. 在 `configs/small_model_algorithms.yaml` 增加 `algor_type` 条目，例如：

```yaml
"40199":
  name: 我的新检测
  strategy: ObjectDetectionStrategy   # 或 RegularBehaviorDetectionStrategy / ComplexBehaviorDetectionStrategy
  weights_path: app/small_models/pretrained/my_model.pt
  device: "cpu"
  imgsz: 640
  conf: 0.35
  class_filter:
    class_names: ["my_class"]
  # L3 额外示例：
  # complex_mode: dwell
  # dwell_seconds: 15
  # dwell_polygon: [[x1,y1], ...]
```

3. 通道启动时在 API 传 `algor_type: "40199"`（及可选覆盖项）。  
4. 跑通：`pytest tests/test_small_models_enterprise.py`。

### 4.3 新增策略类（新算法栈或复杂逻辑）

1. **选目录**  
   - 常规分层：放入 `strategy/l1|l2|l3|l4/`  
   - 复合/专项：放入 `strategy/specialized/`  
   - 可复用工具：放入 `strategy/base/`（如新的 `_xxx_utils.py`）

2. **实现接口**（`strategy/base/base.py`）

```python
class MyStrategy(SmallModelStrategy):
    def infer(self, frame_bgr, *, config: dict, context: dict | None = None) -> StrategyResult:
        # ...
        return StrategyResult(
            triggered=True,
            detections=[Detection(label="...", score=0.9, bbox_xyxy=(...))],
            extra={"algorithm": "my_algo"},
        )
```

3. **注册到引擎** — 编辑 `inference_engine.py`：

```python
_STRATEGY_CLASSES: Dict[str, Type[SmallModelStrategy]] = {
    ...
    "MyStrategy": MyStrategy,
}
```

4. **扩展配置字段**（若需要）— 在 `algorithm_registry.AlgorithmConfig` 增加字段，并在 `_config_to_infer_dict` 透传。  

5. **YAML** — `strategy: MyStrategy`。  

6. **测试** — 在 `tests/` 增加策略单测；复杂专项可参考 `tests/test_calling_detection.py`。

### 4.4 新增 L4 能力（人脸库 / 比对）

- 推理策略：改 `strategy/l4/face_recognition.py` 或 `strategy/face/pipeline.py`。  
- 库管理 / 离线识别：改 `strategy/face/gallery_store.py`，API 在 `app/services/small_models/face_gallery_service.py`。  
- 不要与人脸 REST 逻辑耦合进 YOLO 策略文件。

### 4.5 编号与命名约定

| 前缀 | 层级 | 策略类示例 |
|------|------|------------|
| `40xxx` | L1 常规目标 | `ObjectDetectionStrategy` |
| `41xxx` | L2 常规行为 / 部分专项 | `RegularBehaviorDetectionStrategy`、`CallingDetectionStrategy` |
| `42xxx` | L3 复杂时空 | `ComplexBehaviorDetectionStrategy` |
| `43xxx` | L4 人脸 | `FaceRecognitionStrategy` |

同一策略类可对应多条 `algor_type`（不同权重、阈值、ROI），**一条业务场景一条 ID** 便于运维与回调区分。

---

## 5. 关键模块速查

| 模块 | 职责 |
|------|------|
| `algorithm_registry.py` | 加载/合并算法配置；`resolve_path` 解析权重路径 |
| `inference_engine.py` | 策略单例、冷却、证据、回调 |
| `runtime/channel_manager.py` | 多通道并发、队列、启停 |
| `common/roi.py` | 矩形/多边形 ROI，检测框与人脸共用 |
| `strategy/base/_yolo_utils.py` | YOLO 模型缓存、`predict_detections` |
| `strategy/base/_insightface_utils.py` | InsightFace 检测与 embedding |
| `strategy/base/_spatial_rules.py` | 接打电话等人-机空间匹配 |
| `strategy/face/backends/local_index.py` | 默认向量检索（numpy/faiss） |
| `strategy/face/backends/milvus_index.py` | Milvus 向量检索（`FACE_VECTOR_BACKEND=milvus`） |

### 5.1 人脸向量存储

| `FACE_VECTOR_BACKEND` | 向量存哪 | 检索 |
|------------------------|----------|------|
| `local`（默认） | `gallery.json` + 进程内索引 | numpy / faiss（≥32 样本） |
| `milvus` | Milvus collection | ANN（COSINE）；元数据仍 JSON |

部署 Milvus：`face_db-deploy/README.md`；迁移：`scripts/migrate_face_galleries_to_milvus.py`。

---

## 6. 相关文档

- 权重下载与 seg/pose 说明：`pretrained/README.md`  
- InsightFace 模型目录：`pretrained/insightface/README.md`  
- **Milvus 人脸向量库部署**：`face_db-deploy/README.md`  
- 通道 API 与参数透传：`enterprise-level_transformation_docs/小模型应用通道实现策略.md`  
- 企业级策略设计：`framework-guide/小模型企业级算法策略框架实现方案.md`  

---

## 7. 分层总览（速查表）

| 层级 | 策略类 | 目录 | 核心能力 | 典型市面算法 |
|------|--------|------|----------|--------------|
| **L1** | `ObjectDetectionStrategy` | `strategy/l1/` | 目标框 + 类别过滤 + ROI | YOLO 系、PPE、车辆行人 |
| **L2** | `RegularBehaviorDetectionStrategy` | `strategy/l2/` | 单帧行为检测头 | 口罩/吸烟/打架等自训 YOLO |
| **L3** | `ComplexBehaviorDetectionStrategy` | `strategy/l3/` | 检测 + 滞留/绊线/禁区 | 电子围栏、IVS 行为分析 |
| **L4** | `FaceRecognitionStrategy` | `strategy/l4/` + `strategy/face/` | 1:N / 白名单 / 陌生人 | InsightFace、ArcFace 类方案 |
| **专项** | `CallingDetectionStrategy` | `strategy/specialized/` | 人+手机空间规则 + 回落 | 接打电话检测、组合规则类算法 |

新增算法时，**优先复用已有层级**；仅在现有分层无法表达业务逻辑时，再新增 `specialized` 策略或扩展 `base` 工具。
