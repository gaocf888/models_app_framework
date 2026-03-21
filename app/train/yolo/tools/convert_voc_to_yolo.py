"""数据集标注文件转换VOC转yolo（xml转txt）-- 临时"""

import os
import xml.etree.ElementTree as ET

# ================= 配置区域 =================

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))


# 数据集根目录 (就是你截图中的那个大文件夹名字)
DATASET_ROOT = 'handup_yolo_dataset'
DATASET_ROOT = os.path.join(BASE_DIR, "app", "dajia", "yolo", "datasets", "processed", DATASET_ROOT)

# 类别映射 (必须与你 XML 文件里的 <name> 标签一致)
CLASS_MAP = {
    'handsup': 0,
    'other': 1
}


# ===========================================

def convert_box(size, box):
    """将 [xmin, ymin, xmax, ymax] 转换为 YOLO 归一化格式"""
    w_img, h_img = size

    # 【关键修复】防止除以零
    if w_img == 0 or h_img == 0:
        return None

    dw = 1.0 / w_img
    dh = 1.0 / h_img

    xmin, ymin, xmax, ymax = box
    x_center = (xmin + xmax) / 2.0
    y_center = (ymin + ymax) / 2.0
    w_box = xmax - xmin
    h_box = ymax - ymin

    # 归一化
    x_norm = x_center * dw
    y_norm = y_center * dh
    w_norm = w_box * dw
    h_norm = h_box * dh

    # 边界裁剪 (防止超出 0-1)
    x_norm = max(0.0, min(1.0, x_norm))
    y_norm = max(0.0, min(1.0, y_norm))
    w_norm = max(0.0, min(1.0, w_norm))
    h_norm = max(0.0, min(1.0, h_norm))

    return (x_norm, y_norm, w_norm, h_norm)


def parse_xml(xml_path):
    """解析 XML 文件"""
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()

        size_node = root.find('size')
        if size_node is None:
            return None, []

        w = int(size_node.find('width').text)
        h = int(size_node.find('height').text)

        # 检查尺寸有效性
        if w <= 0 or h <= 0:
            print(f"⚠️ 警告: {os.path.basename(xml_path)} 尺寸为 0 ({w}x{h})，跳过。")
            return None, []

        boxes = []
        for obj in root.iter('object'):
            cls_name = obj.find('name')
            if cls_name is None or cls_name.text not in CLASS_MAP:
                continue

            cls_id = CLASS_MAP[cls_name.text]
            bndbox = obj.find('bndbox')

            if bndbox is not None:
                try:
                    xmin = float(bndbox.find('xmin').text)
                    ymin = float(bndbox.find('ymin').text)
                    xmax = float(bndbox.find('xmax').text)
                    ymax = float(bndbox.find('ymax').text)

                    if xmax > xmin and ymax > ymin:
                        boxes.append((cls_id, (xmin, ymin, xmax, ymax)))
                except ValueError:
                    continue
        return (w, h), boxes
    except Exception as e:
        print(f"❌ 解析失败 {os.path.basename(xml_path)}: {e}")
        return None, []


def main():
    splits = ['dajia', 'val']

    print(f"🚀 开始转换数据集: {DATASET_ROOT}")

    for split in splits:
        # 1. 定义输入输出路径 (完全匹配你的截图结构)
        src_xml_dir = os.path.join(DATASET_ROOT, 'xml_temp', split)
        dst_lbl_dir = os.path.join(DATASET_ROOT, 'labels', split)

        # 确保 labels 目录存在
        os.makedirs(dst_lbl_dir, exist_ok=True)

        if not os.path.exists(src_xml_dir):
            print(f"⚠️ 未找到源目录: {src_xml_dir}，跳过 {split}")
            continue

        xml_files = [f for f in os.listdir(src_xml_dir) if f.endswith('.xml')]
        print(f"📂 处理 [{split}] 集合，共 {len(xml_files)} 个标注文件...")

        success_count = 0
        error_count = 0

        for xml_name in xml_files:
            xml_path = os.path.join(src_xml_dir, xml_name)
            txt_name = xml_name.replace('.xml', '.txt')
            txt_path = os.path.join(dst_lbl_dir, txt_name)

            size, boxes = parse_xml(xml_path)

            if size is None:
                error_count += 1
                continue

            # 写入 TXT
            with open(txt_path, 'w') as f:
                for cls_id, box in boxes:
                    result = convert_box(size, box)
                    if result:
                        x, y, w, h = result
                        f.write(f"{cls_id} {x:.6f} {y:.6f} {w:.6f} {h:.6f}\n")

            success_count += 1

        print(f"✅ [{split}] 完成: 成功 {success_count}, 失败/跳过 {error_count}")

    print("\n🎉 所有转换完成！")
    print(f"📁 请检查目录: {os.path.abspath(os.path.join(DATASET_ROOT, 'labels'))}")


if __name__ == '__main__':
    main()