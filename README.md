## 框架实现说明

### 小模型训练
目前已实现yolo所有模型的训练工程化(app/train/yolo)

### 视频解码、任务队列、图像识别的多通道线程安全业务流
目前已实现，入口：app/api/small_model.py