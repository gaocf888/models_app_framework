# InsightFace 模型目录

InsightFace 首次运行时会将模型下载到此目录（或通过 `face_model_root` 指定）。

推荐模型包：**buffalo_l**（检测 SCRFD + 识别 ArcFace，精度与速度均衡）。

## 部署步骤

1. 安装依赖：`pip install -r requirements-小模型应用.txt`（GPU 镜像内已含 `onnxruntime-gpu`）
2. 首次调用录入/识别 API 时自动下载模型到本目录（或设置 `INSIGHTFACE_MODELS_HOST_PATH` 预挂载）
3. 创建人脸库：`POST /face/gallery` → `gallery_id=default`
4. 录入：`POST /face/gallery/default/enroll`（multipart 图片）
5. 视频识别：`POST /small-model/channel/start`，`algor_type=43101`

## Docker（small-model-gpu profile）

- 人脸库卷：`face-galleries-data` → `/workspace/data/face_galleries`
- 模型卷：`INSIGHTFACE_MODELS_HOST_PATH` 或占位卷 `insightface-models-dummy`
- 详见 `app/app-deploy/README-simple-deploy.md` §3.1

## 大规模人脸库

样本数 ≥ 32 且已安装 `faiss-cpu` 时，1:N 检索自动切换 Faiss `IndexFlatIP`；可通过 `GET /face/gallery/{id}/stats` 查看 `backend`。

## 与 YOLO 权重区别

人脸识别 **不使用** `weights_path` / `.pt`；配置项为 `face_model_pack` + `face_model_root`。

录入与识别必须使用同一 `model_pack`，否则 embedding 不可比。
