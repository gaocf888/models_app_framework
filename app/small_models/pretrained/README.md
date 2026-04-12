# 小模型预训练权重（离线部署）

推理链使用 **Ultralytics YOLOv8**（`.pt`）。请将权重放在本目录（`app/small_models/pretrained/`），并在 `configs/small_model_algorithms.yaml` 的 `weights_path` 中引用（相对项目根目录）。

**重要（与 pose/seg 相关）**：`app/small_models/strategy/_yolo_utils.py` 的 `predict_detections` **只读取 `results.boxes`**，不解析实例掩码（seg）或关键点（pose）。因此 pose/seg 权重在现有框架里**仍可跑通推理**，但 **mask / keypoints 不会被用于业务逻辑**；要按轮廓或骨架做规则，需要后续扩展策略代码。

## 1. 建议准备的文件（按优先级）

| 文件（示例路径） | 用途 | 获取方式 |
|------------------|------|----------|
| `yolov8s.pt` | COCO **检测** 预训练；通用/行人/车辆、L3 规则、L2 占位（与当前 YAML 默认一致） | [Ultralytics assets releases](https://github.com/ultralytics/assets/releases) 搜索 `yolov8s.pt`；或 `YOLO("yolov8s.pt")` 自动缓存后拷贝 |
| `call.pt` | 接打电话等行为（仓库示例；可自训覆盖） | 本目录已有或自训 |
| `yolov8s-seg.pt` | COCO **实例分割**（官方）；可选，见 §3 | 同上，搜索 `yolov8s-seg.pt` |
| `yolov8s-pose.pt` | COCO **姿态**（人体框 + 关键点，官方）；可选，见 §3 | 同上，搜索 `yolov8s-pose.pt` |
| `ppe.pt`（或自命名） | **非 Ultralytics 官方统一发布**；多指社区/自训的 PPE（安全帽、反光衣等）检测权重 | 见 §4 |

同系列还有 `yolov8n.pt`、`yolov8m.pt` 等：体积与精度权衡，YAML 里改 `weights_path` 即可。

## 2. 离线下载（通用）

- **官方 Release（适合纯离线拷贝）**  
  <https://github.com/ultralytics/assets/releases> — 在附件列表中搜索所需文件名（如 `yolov8s.pt`、`yolov8s-seg.pt`、`yolov8s-pose.pt`）。

- **有外网的环境预拉取**  
  安装 `ultralytics` 后执行 `YOLO("yolov8s-seg.pt")` 等，再从缓存目录拷贝到本目录。

## 3. 官方「多任务」权重：效果定位与是否要换进常见算法

以下均为 Ultralytics **公开、通用认知度高**的预训练（COCO 上预训练），命名在官方文档与 Hub 中一致。

| 权重示例 | 任务 | 相对 `yolov8s.pt` 检测 | 当前框架是否「能用」 | 是否建议替换现有「常见算法」默认 |
|----------|------|-------------------------|----------------------|----------------------------------|
| `yolov8s.pt` | 目标检测 | 基线 | 完全支持 | **保持** — `40104` / `40111`–`40113` / `42xxx` 等已适用 |
| `yolov8s-seg.pt` | 实例分割 | 同类目标，多输出 mask | 仅 **框** 参与逻辑，**掩码浪费** | **一般不替换** `40104`/`40111`：算力更高、收益主要在像素级；若未来要做「按轮廓占比/越界」再考虑并改代码 |
| `yolov8s-pose.pt` | 姿态（人 + 关键点） | 行人检测可用框 | 仅 **框** 参与，**关键点浪费** | **不必替换** `40111`：若无跌倒/动作分析，用检测版即可；有姿态需求应 **扩展策略消费 keypoints** 后再默认 pose |
| `yolov8s-obb.pt` | 旋转框（常见遥感/航拍） | 任务域不同 | 需确认导出框形式；当前按轴对齐 `xyxy` 解析 | **不**作为通用监控默认；场景匹配时再单独配一条 `algor_type` 并测兼容性 |
| `yolov8s-cls.pt` | 整图分类 | 无目标框 | **不兼容** 当前 `predict_detections` | **不要**接入现有 L1/L2/L3 检测策略 |

**结论**：在**不扩展代码**的前提下，**继续用 `yolov8s.pt` 作为通用与行人/车辆/L3 的默认**最合适；`yolov8s-seg.pt` / `yolov8s-pose.pt` 属于「可选增强」，已在 YAML 中增加独立编号 **`40115` / `40116`** 便于试用，**无需**把原有 `40104`、`40111` 批量改成 seg/pose。

## 4. PPE / `ppe.pt`：开源共识与配置方式

- **没有**与 `yolov8n.pt` 同级别的、单一全球统一的 **`ppe.pt` 官方文件名**。工业界常见做法是：
  - 使用 **Roboflow Universe** 等平台上公开的 PPE 数据集与导出权重（YOLOv8），注意 **许可证与商用条款**；
  - 或使用论文/开源仓库附带的 `best.pt`，自行重命名为 `ppe.pt` 放入本目录。
- 若你的 `ppe.pt` 是 **多类**（如 `helmet`、`vest`、`person`、`no-helmet` 等）：
  - **推荐**：用 **一条** `algor_type` 指向该权重，通过 **`class_filter`**（`class_names` 或 `class_ids`）区分通道要看的类别；  
  - 也可仍用 `40101`–`40103` 三条，但通常 **三条共用一个 `ppe.pt` + 不同 `class_filter`** 比三个独立小模型更易维护。
- **需要替换配置吗**：**要。** 把 `40101`–`40103`（或合并为一条新 ID）的 `weights_path` 改为你的 `app/small_models/pretrained/ppe.pt`（或实际路径），并 **按该模型的 `names` 调整 `class_filter`**；否则 COCO 检测无法稳定产出安全帽/反光衣等工业类别。

## 5. 自训权重放置约定

训练产出（如 `runs/detect/train/weights/best.pt`）可复制到本目录并命名例如 `helmet.pt`、`ppe.pt`，然后在 `small_model_algorithms.yaml` 中修改对应 `algor_type` 的 `weights_path`，并保证 `class_filter` 与训练时的类别名或 id 一致。

## 6. 与配置的对应关系

算法编号、策略分层与默认 `weights_path` 以 **`configs/small_model_algorithms.yaml`** 为准；其中 **`40115`（seg）/ `40116`（pose）** 为可选官方多任务示例条目。
