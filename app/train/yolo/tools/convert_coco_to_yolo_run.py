"""coco数据集转yolo格式  执行入口文件(调用当前路径下 convert_coco_to_yolo.py)"""
"""
该Ultralytics官方转换工具，转换后会生成标注的YOLO结构(如下)
coco_converted/
├── images/          # 注意：此文件夹默认是空的，需要你手动将图片复制过来
└── labels/          # 核心输出目录，包含所有转换后的标签文件
    └── (你的标签txt文件会在这里，文件名与对应图片相同)
"""

from ultralytics.data.converter import convert_coco

###############################################################################################################################
# 下面是最简单的方式，只转换标注文件，图片不复制或移动
# 转换COCO格式的标注文件到YOLO格式
# labels_dir指向包含COCO标注JSON文件的目录

# convert_coco(labels_dir='F:\workspace\developer\models_app\models_app_framework\app\train\yolo\datasets\source\dajia')



###############################################################################################################################
# 下面是完整的自动转换方式，按照上述结构COCO转成yolo后，还会自动在images中复制图片，并在images和labels按照80%和 20%的比例划分训练集和验证集
from convert_coco_to_yolo import convert_coco_to_yolo_complete

import os
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

# 定义原COCO数据集json标注数据路径(最后的路径为json文件的根路径)
coco_annotations_dir = os.path.join(BASE_DIR, 'app', 'train', 'yolo', 'datasets', 'source', 'dajia')

# 定义原COCO数据集图片所在路径
images_source_dir = os.path.join(BASE_DIR, 'app', 'train', 'yolo', 'datasets', 'source', 'dajia')

# 定义标准yolo格式数据集转换后生成路径
output_dir = os.path.join(BASE_DIR, 'app', 'train', 'yolo', 'datasets', 'processed', 'dajia')

# 输出路径创建
path = Path(output_dir)
path.mkdir(exist_ok=True)

convert_coco_to_yolo_complete(
    coco_annotations_dir=coco_annotations_dir,
    images_source_dir=images_source_dir,
    output_dir=output_dir,
    train_ratio=0.8
)