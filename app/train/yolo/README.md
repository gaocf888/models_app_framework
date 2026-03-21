# YOLOv8 训练封装说明

本目录提供了一个简洁但参数覆盖充分的 YOLOv8 训练入口：

- 主代码：`yolo_train.py`
- 训练参数示例：`config.yaml`
- 数据集配置示例：`data.yaml.example`

## 运行步骤

1. 准备数据集目录（按 `data.yaml.example` 的结构）并修改其中的 `path/train/val/names`。
2. 修改 `config.yaml`：
   - `pretrained_model`：填写 `yolov8n.pt` 或你已有的 `best.pt` 路径
   - `data_yaml`：填写你的 data.yaml 路径（可以直接指向 data.yaml.example 或拷贝一份再改）
3. 安装依赖：
   - `pip install ultralytics`
4. 运行：

```bash
python app/dajia/yolo/yolo_train.py --config app/dajia/yolo/config.yaml
```

训练产物（如 `best.pt` / `last.pt`）将由 Ultralytics 写入 `project/name` 下的 runs 目录。

