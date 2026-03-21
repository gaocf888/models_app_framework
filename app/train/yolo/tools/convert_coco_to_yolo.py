"""coco数据集转yolo格式"""
"""
该Ultralytics官方转换工具，转换后会生成标注的YOLO结构(如下)，图片和标注数据会按照80%训练集和20%验证集进行自动划分
coco_converted/
├── images/          # 注意：此文件夹默认是空的，需要你手动将图片复制过来
└── labels/          # 核心输出目录，包含所有转换后的标签文件
    └── (你的标签txt文件会在这里，文件名与对应图片相同)
"""

# !/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
完整的COCO转YOLO脚本，包含：
1. 将COCO标注转换为YOLO格式标签
2. 按80/20比例划分训练集和验证集
3. 自动移动对应的图片和标签文件到正确位置
"""

import os
import json
import shutil
import random
from pathlib import Path
from tqdm import tqdm
from ultralytics.data.converter import convert_coco


def _load_coco_stem_to_image_id(coco_annotations_dir: str) -> dict[str, str]:
    """
    从 COCO json 中构建映射：
    - key：图片文件名 stem（Path(file_name).stem）
    - value：该图片在 COCO 的 image id（一般是数字）

    用于兼容 Ultralytics convert_coco 输出 label 文件名可能是 stem 或 image_id 的情况。
    """
    coco_dir = Path(coco_annotations_dir)
    json_candidates = [
        coco_dir / "_annotations.coco.json",
        coco_dir / "instances.json",
        coco_dir / "instances_val.json",
        coco_dir / "annotations.json",
    ]
    json_file = None
    for c in json_candidates:
        if c.exists():
            json_file = c
            break
    if json_file is None:
        # 兜底：取目录下第一个 json
        json_files = list(coco_dir.glob("*.json"))
        if not json_files:
            return {}
        json_file = json_files[0]

    try:
        coco_data = json.loads(json_file.read_text(encoding="utf-8"))
    except Exception:
        return {}

    mapping: dict[str, str] = {}
    for img in coco_data.get("images", []) or []:
        file_name = img.get("file_name")
        if not file_name:
            continue
        stem = Path(file_name).stem
        image_id = img.get("id")
        if stem and image_id is not None:
            mapping[stem] = str(image_id)
    return mapping


def _resolve_label_path(img_path: Path, labels_dir: Path, stem_to_image_id: dict[str, str]) -> Path | None:
    """
    给定磁盘图片路径，尝试在 labels_dir 中找到对应 label：
    - 首选：{img_stem}.txt
    - 其次：{coco_image_id}.txt（如果 stem->id 映射存在）
    """
    stem = img_path.stem
    p1 = labels_dir / f"{stem}.txt"
    if p1.exists():
        return p1

    if stem in stem_to_image_id:
        p2 = labels_dir / f"{stem_to_image_id[stem]}.txt"
        if p2.exists():
            return p2

    return None


def setup_directories(base_dir):
    """
    创建必要的目录结构

    Args:
        base_dir: 基础目录（coco_converted）
    """
    dirs = [
        base_dir / 'images' / 'train',
        base_dir / 'images' / 'val',
        base_dir / 'labels' / 'train',
        base_dir / 'labels' / 'val',
    ]

    for dir_path in dirs:
        dir_path.mkdir(parents=True, exist_ok=True)
        print(f"创建目录: {dir_path}")

    return dirs


def get_image_files(images_dir):
    """
    递归获取images_dir下的所有图片文件

    Args:
        images_dir: 图片目录路径

    Returns:
        图片文件路径列表
    """
    image_extensions = ['.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff']
    image_files = []

    images_dir = Path(images_dir)
    for ext in image_extensions:
        image_files.extend(images_dir.rglob(f'*{ext}'))
        image_files.extend(images_dir.rglob(f'*{ext.upper()}'))

    return image_files


def split_dataset(image_files, train_ratio=0.8, random_seed=42):
    """
    将图片文件列表划分为训练集和验证集

    Args:
        image_files: 图片文件路径列表
        train_ratio: 训练集比例
        random_seed: 随机种子

    Returns:
        训练集文件列表，验证集文件列表
    """
    random.seed(random_seed)
    shuffled_files = image_files.copy()
    random.shuffle(shuffled_files)

    split_idx = int(len(shuffled_files) * train_ratio)
    train_files = shuffled_files[:split_idx]
    val_files = shuffled_files[split_idx:]

    return train_files, val_files


def move_files(file_list, dest_dir, file_type="文件"):
    """
    移动文件到目标目录

    Args:
        file_list: 要移动的文件路径列表
        dest_dir: 目标目录
        file_type: 文件类型描述（用于显示）

    Returns:
        成功移动的文件数量
    """
    success_count = 0
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    for src_path in tqdm(file_list, desc=f"移动{file_type}"):
        try:
            src_path = Path(src_path)
            if not src_path.exists():
                print(f"警告: 文件不存在 {src_path}")
                continue

            dest_path = dest_dir / src_path.name

            # 如果目标文件已存在，添加序号避免覆盖
            if dest_path.exists():
                base_name = src_path.stem
                extension = src_path.suffix
                counter = 1
                while dest_path.exists():
                    new_name = f"{base_name}_{counter}{extension}"
                    dest_path = dest_dir / new_name
                    counter += 1

            shutil.move(str(src_path), str(dest_path))
            success_count += 1

        except Exception as e:
            print(f"移动失败 {src_path}: {e}")

    return success_count


def verify_correspondence(image_files, labels_dir, stem_to_image_id=None):
    """
    验证图片和标签文件的对应关系

    Args:
        image_files: 图片文件列表
        labels_dir: 标签文件目录

    Returns:
        有效的图片文件列表（有对应标签的图片）
    """
    valid_images = []
    missing_labels = []

    labels_dir = Path(labels_dir)
    stem_to_image_id = stem_to_image_id or {}

    for img_path in tqdm(image_files, desc="验证对应关系"):
        img_path = Path(img_path)
        label_path = _resolve_label_path(img_path=img_path, labels_dir=labels_dir, stem_to_image_id=stem_to_image_id)
        if label_path is not None:
            valid_images.append(img_path)
        else:
            missing_labels.append(img_path)

    if missing_labels:
        print(f"警告: 发现 {len(missing_labels)} 张图片没有对应的标签文件")
        if len(missing_labels) <= 10:
            for img in missing_labels:
                print(f"  - {img.name}")

    return valid_images


def create_dataset_yaml(base_dir, class_names, train_images_dir, val_images_dir):
    """
    创建dataset.yaml配置文件

    Args:
        base_dir: 基础目录
        class_names: 类别名称列表
        train_images_dir: 训练图片目录
        val_images_dir: 验证图片目录

    Returns:
        yaml文件路径
    """
    yaml_path = base_dir / 'dataset.yaml'

    # 获取相对路径
    train_path = Path(train_images_dir).relative_to(base_dir)
    val_path = Path(val_images_dir).relative_to(base_dir)

    with open(yaml_path, 'w', encoding='utf-8') as f:
        f.write(f"# 数据集配置文件\n")
        f.write(f"path: {base_dir.absolute()}  # 数据集根目录\n")
        f.write(f"train: {train_path}  # 训练图片目录\n")
        f.write(f"val: {val_path}  # 验证图片目录\n\n")

        f.write("# 类别名称\n")
        f.write("names:\n")
        for i, name in enumerate(class_names):
            f.write(f"  {i}: {name}\n")

    print(f"已创建配置文件: {yaml_path}")
    return yaml_path


def convert_coco_to_yolo_complete(
        coco_annotations_dir,
        images_source_dir,
        output_dir=None,
        train_ratio=0.8,
        use_segments=False,
        use_keypoints=False
):
    """
    完整的COCO转YOLO流程

    Args:
        coco_annotations_dir: COCO标注文件所在目录（包含instances_*.json）
        images_source_dir: 原始图片所在目录
        output_dir: 输出目录（默认在当前目录下的coco_converted）
        train_ratio: 训练集比例
        use_segments: 是否转换分割标注
        use_keypoints: 是否转换关键点标注
    """
    # 设置输出目录
    if output_dir is None:
        output_dir = Path.cwd() / 'coco_converted'
    else:
        output_dir = Path(output_dir)

    print("=" * 60)
    print("COCO转YOLO完整转换工具")
    print("=" * 60)
    print(f"COCO标注目录: {coco_annotations_dir}")
    print(f"原始图片目录: {images_source_dir}")
    print(f"输出目录: {output_dir}")
    print(f"训练集比例: {train_ratio * 100}%")
    print("-" * 60)

    # 步骤1: 创建目录结构
    print("\n[步骤1] 创建目录结构...")
    dirs = setup_directories(output_dir)
    labels_train_dir, labels_val_dir = dirs[2], dirs[3]  # labels/train, labels/val

    # 步骤2: 转换COCO标注到YOLO格式
    print("\n[步骤2] 转换COCO标注到YOLO格式...")
    convert_coco(
        labels_dir=str(coco_annotations_dir),
        use_segments=use_segments,
        use_keypoints=use_keypoints
    )

    # 转换后的标签目录：
    # Ultralytics 在不同版本下可能把 txt 放在：
    # - coco_converted/labels/*.txt
    # - 或 coco_converted/labels/_annotations.coco/*.txt
    # 这里自动探测实际的 labels 根目录，确保后续查找/移动不会漏掉 txt。
    temp_labels_dir = Path.cwd() / 'coco_converted' / 'labels'
    if not temp_labels_dir.exists():
        print("错误: 转换后的标签文件未找到")
        return False

    # 优先识别常见子目录 _annotations.coco
    anno_dir = temp_labels_dir / '_annotations.coco'
    if anno_dir.exists() and any(anno_dir.glob('*.txt')):
        temp_labels_dir = anno_dir
    else:
        # 否则寻找任意包含 txt 的第一层子目录
        found = False
        for sub in temp_labels_dir.iterdir():
            if sub.is_dir() and any(sub.glob('*.txt')):
                temp_labels_dir = sub
                found = True
                break
        if not found and not any(temp_labels_dir.glob('*.txt')):
            print(f"错误: 在 {temp_labels_dir} 下未找到任何 .txt 标签文件")
            return False

    # 步骤3: 获取所有图片文件
    print("\n[步骤3] 扫描原始图片...")
    all_images = get_image_files(images_source_dir)
    print(f"找到 {len(all_images)} 张图片")

    # 从 COCO json 推断“图片文件名 stem -> image_id”映射
    stem_to_image_id = _load_coco_stem_to_image_id(coco_annotations_dir)

    # 步骤4: 验证图片和标签的对应关系
    print("\n[步骤4] 验证图片和标签对应关系...")
    valid_images = verify_correspondence(all_images, temp_labels_dir, stem_to_image_id=stem_to_image_id)
    print(f"有效图片（有对应标签）: {len(valid_images)} 张")

    if len(valid_images) == 0:
        print("错误: 没有找到有效的图片-标签对应关系")
        return False

    # 步骤5: 划分数据集
    print("\n[步骤5] 划分训练集和验证集...")
    train_images, val_images = split_dataset(valid_images, train_ratio)
    print(f"训练集: {len(train_images)} 张图片")
    print(f"验证集: {len(val_images)} 张图片")

    # 步骤6: 获取对应的标签文件路径
    train_labels = []
    for img_path in train_images:
        resolved = _resolve_label_path(img_path=img_path, labels_dir=temp_labels_dir, stem_to_image_id=stem_to_image_id)
        if resolved is not None:
            train_labels.append(resolved)

    val_labels = []
    for img_path in val_images:
        resolved = _resolve_label_path(img_path=img_path, labels_dir=temp_labels_dir, stem_to_image_id=stem_to_image_id)
        if resolved is not None:
            val_labels.append(resolved)

    # 步骤7: 移动训练集文件
    print("\n[步骤6] 移动训练集文件...")
    img_train_dest = output_dir / 'images' / 'train'
    label_train_dest = output_dir / 'labels' / 'train'

    moved_train_imgs = move_files(train_images, img_train_dest, "训练图片")
    moved_train_labels = move_files(train_labels, label_train_dest, "训练标签")

    # 步骤8: 移动验证集文件
    print("\n[步骤7] 移动验证集文件...")
    img_val_dest = output_dir / 'images' / 'val'
    label_val_dest = output_dir / 'labels' / 'val'

    moved_val_imgs = move_files(val_images, img_val_dest, "验证图片")
    moved_val_labels = move_files(val_labels, label_val_dest, "验证标签")

    # 步骤9: 获取类别名称
    print("\n[步骤8] 创建配置文件...")

    # 尝试从COCO标注文件中读取类别名称
    class_names = []
    json_files = list(Path(coco_annotations_dir).glob('*.json'))
    if json_files:
        try:
            with open(json_files[0], 'r', encoding='utf-8') as f:
                coco_data = json.load(f)
                if 'categories' in coco_data:
                    # 按category_id排序
                    categories = sorted(coco_data['categories'], key=lambda x: x['id'])
                    class_names = [cat['name'] for cat in categories]
                    print(f"从标注文件读取到 {len(class_names)} 个类别")
        except Exception as e:
            print(f"读取类别名称失败: {e}")

    # 如果没有读取到类别名称，使用默认名称
    if not class_names:
        # 从标签文件中推断类别数量
        max_class_id = -1
        for label_file in list(label_train_dest.glob('*.txt')) + list(label_val_dest.glob('*.txt')):
            try:
                with open(label_file, 'r') as f:
                    for line in f:
                        if line.strip():
                            class_id = int(line.strip().split()[0])
                            max_class_id = max(max_class_id, class_id)
            except:
                pass

        if max_class_id >= 0:
            class_names = [f"class_{i}" for i in range(max_class_id + 1)]
        else:
            class_names = ["object"]  # 默认

    # 创建dataset.yaml
    yaml_path = create_dataset_yaml(
        output_dir,
        class_names,
        img_train_dest,
        img_val_dest
    )

    # 步骤10: 清理临时文件
    print("\n[步骤9] 清理临时文件...")
    try:
        if temp_labels_dir.exists() and temp_labels_dir != output_dir / 'labels':
            shutil.rmtree(temp_labels_dir)
            print(f"已删除临时标签目录: {temp_labels_dir}")
    except Exception as e:
        print(f"清理临时文件失败: {e}")

    # 输出统计信息
    print("\n" + "=" * 60)
    print("转换完成！统计信息：")
    print("=" * 60)
    print(f"训练集图片: {moved_train_imgs} 张")
    print(f"训练集标签: {moved_train_labels} 个")
    print(f"验证集图片: {moved_val_imgs} 张")
    print(f"验证集标签: {moved_val_labels} 个")
    print(f"总有效图片: {moved_train_imgs + moved_val_imgs} 张")
    print(f"类别数量: {len(class_names)}")
    print("-" * 60)
    print(f"输出目录: {output_dir}")
    print(f"配置文件: {yaml_path}")
    print("=" * 60)

    # 显示使用说明
    print("\n训练命令示例：")
    print(f"yolo train data={yaml_path} model=yolov8n.pt epochs=100 imgsz=640")

    return True


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="COCO转YOLO完整工具")
    parser.add_argument(
        '--coco-dir',
        required=True,
        help='COCO标注文件所在目录（包含instances_*.json）'
    )
    parser.add_argument(
        '--images-dir',
        required=True,
        help='原始图片所在目录'
    )
    parser.add_argument(
        '--output-dir',
        default='./coco_converted',
        help='输出目录 (默认: ./coco_converted)'
    )
    parser.add_argument(
        '--train-ratio',
        type=float,
        default=0.8,
        help='训练集比例 (默认: 0.8)'
    )
    parser.add_argument(
        '--segments',
        action='store_true',
        help='转换分割标注'
    )
    parser.add_argument(
        '--keypoints',
        action='store_true',
        help='转换关键点标注'
    )

    args = parser.parse_args()

    # 验证参数
    if args.train_ratio <= 0 or args.train_ratio >= 1:
        print("错误: train-ratio 必须在0到1之间")
        return

    # 执行转换
    convert_coco_to_yolo_complete(
        coco_annotations_dir=args.coco_dir,
        images_source_dir=args.images_dir,
        output_dir=args.output_dir,
        train_ratio=args.train_ratio,
        use_segments=args.segments,
        use_keypoints=args.keypoints
    )


if __name__ == "__main__":
    main()