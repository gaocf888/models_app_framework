"""数据集目录结构调整脚本  -- 临时"""

"""
调整前结构：

└── raw_data/           <-- 数据集跟路径
    ├── jpg/
    └── xml/
"""

"""
调整后结构：

handup_yolo_dataset/
├── images/
│   ├── dajia/  (80% 的图片)
│   └── val/    (20% 的图片)
├── xml_temp/   (对应的 XML 文件，用于下一步转换)
│   ├── dajia/
│   └── val/
└── labels/     (空文件夹，等待填充 TXT)
    ├── dajia/
    └── val/
"""

import os
import shutil
import random

# --- 配置区域 ---
# 原始数据路径 (对应你截图的文件夹)
RAW_DATA_ROOT = 'raw_data'
IMG_DIR = os.path.join(RAW_DATA_ROOT, 'jpg')
ANN_DIR = os.path.join(RAW_DATA_ROOT, 'xml')

# 输出目标路径 (标准 YOLO 结构)
OUTPUT_ROOT = 'handup_yolo_dataset'

# 划分比例
TRAIN_RATIO = 0.8


def prepare_data():
    # 1. 获取所有图片文件名 (去掉后缀)
    # 假设图片都是 .jpg，如果有 .png 需自行调整
    all_files = [f.split('.')[0] for f in os.listdir(IMG_DIR) if f.endswith('.jpg')]

    # 2. 随机打乱顺序
    random.shuffle(all_files)

    # 3. 计算切分点
    split_idx = int(len(all_files) * TRAIN_RATIO)
    train_files = all_files[:split_idx]
    val_files = all_files[split_idx:]

    print(f"总样本数: {len(all_files)}")
    print(f"训练集: {len(train_files)}, 验证集: {len(val_files)}")

    # 4. 定义辅助函数：移动文件
    def move_files(file_list, split_name):
        # 创建目标目录
        target_img_dir = os.path.join(OUTPUT_ROOT, 'images', split_name)
        target_ann_dir = os.path.join(OUTPUT_ROOT, 'labels', split_name)  # 注意：这里先建 labels 目录，稍后转换脚本会填入内容

        os.makedirs(target_img_dir, exist_ok=True)
        os.makedirs(target_ann_dir, exist_ok=True)

        # 为了配合下一个转换脚本，我们先把 XML 也复制过去，或者直接在转换脚本里读原始路径
        # 这里我们选择：只移动图片，XML 留在原地或复制到临时区供转换脚本读取
        # 为了简单，我们把对应的 XML 也复制到 output/labels_temp 供下一步处理
        temp_xml_dir = os.path.join(OUTPUT_ROOT, 'xml_temp', split_name)
        os.makedirs(temp_xml_dir, exist_ok=True)

        for fname in file_list:
            # 移动图片
            src_img = os.path.join(IMG_DIR, f"{fname}.jpg")
            dst_img = os.path.join(target_img_dir, f"{fname}.jpg")
            if os.path.exists(src_img):
                shutil.copy(src_img, dst_img)

            # 复制 XML (供下一步转换使用)
            src_xml = os.path.join(ANN_DIR, f"{fname}.xml")
            dst_xml = os.path.join(temp_xml_dir, f"{fname}.xml")
            if os.path.exists(src_xml):
                shutil.copy(src_xml, dst_xml)
            else:
                print(f"⚠️ 警告: 找不到 {fname} 对应的 XML 文件，跳过。")

    # 5. 执行移动
    move_files(train_files, 'dajia')
    move_files(val_files, 'val')

    print("✅ 数据集划分完成！图片已移至 images/，XML 已暂存至 xml_temp/ 供转换。")


if __name__ == '__main__':
    prepare_data()