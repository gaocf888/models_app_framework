import pandas as pd
import numpy as np
import re

# ========== 1. 读取数据 ==========
monitor_file = r"G:\2026年\运行项目季报\分层标456月份.xlsx"
excel2_file = r"G:\2026年\运行项目季报\分层标层位分析.xlsx"

sheets = pd.read_excel(monitor_file, sheet_name=None)
df_mapping = pd.read_excel(excel2_file)

print("=" * 60)
print("映射表预览（前30行）:")
print("=" * 60)
print(df_mapping.head(30))
print(f"\n映射表共有 {len(df_mapping)} 行")
print("=" * 60)


# ========== 2. 预处理映射表：填充空白的站点名称 ==========
def preprocess_mapping(df):
    """填充映射表中空白的站点名称"""
    df = df.copy()

    # 找到"站点名称"列
    site_col = None
    for col in df.columns:
        if '站点名称' in str(col):
            site_col = col
            break

    if site_col is None:
        site_col = df.columns[2]

    print(f"使用列: '{site_col}' 作为站点名称列")

    # 前向填充空值
    last_valid_site = None
    for idx in range(len(df)):
        current_site = df.iloc[idx][site_col]
        if pd.isna(current_site) or str(current_site).strip() == '' or str(current_site).strip() == 'nan':
            df.iloc[idx, df.columns.get_loc(site_col)] = last_valid_site
        else:
            last_valid_site = current_site

    return df


df_mapping = preprocess_mapping(df_mapping)

print("\n预处理后的映射表（前30行）:")
print(df_mapping[['站点名称', '标编号', '监测层位']].head(30))
print("=" * 60)


# ========== 3. 提取站点编号函数 ==========
def extract_site_number(label_id):
    """
    从标编号中提取站点编号（数字部分）
    例如: J8-1 -> 8, F8-1 -> 8, J7-1 -> 7, F7-1 -> 7
    """
    match = re.search(r'(\d+)', label_id)
    if match:
        return int(match.group(1))
    return None


# ========== 4. 从sheet名称提取站点编号 ==========
def extract_site_number_from_name(sheet_name):
    """
    从sheet名称中提取站点编号
    例如: F8(周村) -> 8, F7(榆垡) -> 7, F28 -> 28
    """
    match = re.search(r'(\d+)', sheet_name)
    if match:
        return int(match.group(1))
    return None


# ========== 5. 提取标编号函数 ==========
def extract_label_id(column_name):
    """从列名中提取标编号"""
    match = re.search(r'([A-Za-z]*\d{3,})', column_name)
    if match:
        return match.group(1)
    match = re.search(r'(\d{3,})', column_name)
    if match:
        return match.group(1)
    return column_name


# ========== 6. 提取层位数字标识函数 ==========
def extract_layer_number(layer_name):
    """从监测层位名称中提取数字标识"""
    if pd.isna(layer_name):
        return None
    if isinstance(layer_name, (int, float)):
        return int(layer_name)
    try:
        return int(float(str(layer_name).strip()))
    except:
        pass
    match = re.search(r'(\d+)', str(layer_name))
    if match:
        return int(match.group(1))
    return None


# ========== 7. 存储所有站点的结果 ==========
all_layer_differences = []
all_time_change_details = []

# ===== 第一步：先构建所有标编号到层位的映射（按站点编号分组） =====
print("\n" + "=" * 60)
print("构建标编号→层位映射（按站点编号分组）")
print("=" * 60)

# 按站点编号分组
site_label_map = {}  # {站点编号: {标编号: 层位数字}}
for _, row in df_mapping.iterrows():
    label = str(row["标编号"]).strip()
    layer = row["监测层位"]

    if pd.isna(label) or label == '' or label == 'nan':
        continue

    # 提取站点编号（数字部分）
    site_num = extract_site_number(label)
    if site_num is None:
        continue

    if site_num not in site_label_map:
        site_label_map[site_num] = {}

    if not pd.isna(layer):
        layer_num = extract_layer_number(layer)
        if layer_num is not None:
            site_label_map[site_num][label] = layer_num

# 打印每个站点的映射
print("\n各站点的标编号→层位映射:")
for site_num in sorted(site_label_map.keys()):
    labels = site_label_map[site_num]
    if labels:
        print(f"  站点{site_num}: {labels}")

print("=" * 60)

# ===== 第二步：遍历每个数据sheet，计算时间变化量和层位差值 =====
for site_name, df in sheets.items():
    print(f"\n{'=' * 60}")
    print(f"处理站点: {site_name}")
    print(f"{'=' * 60}")

    # 从sheet名称提取站点编号
    site_num = extract_site_number_from_name(site_name)
    if site_num is None:
        print(f"  ❌ 无法从sheet名称提取站点编号: {site_name}")
        continue
    print(f"  站点编号: {site_num}")

    # 前两列是日期和时间，后面每列是一个层位
    layer_cols = df.columns[2:]

    if len(layer_cols) == 0:
        print(f"  警告: 没有找到层位数据")
        continue

    # ===== 步骤1: 计算每个标编号的时间变化量（最先值 - 最后值）=====
    time_changes = []

    for col in layer_cols:
        label_id = extract_label_id(col)
        series = df[col].dropna()

        if len(series) >= 2:
            first_val = series.iloc[0]
            last_val = series.iloc[-1]
            time_change = first_val - last_val
            time_changes.append({
                "标编号": label_id,
                "原始列名": col,
                "第一个非空值": first_val,
                "最后一个非空值": last_val,
                "时间变化量": time_change
            })
            print(f"  {label_id}({col}): 时间变化 = {first_val:.4f} - {last_val:.4f} = {time_change:.4f}")
        else:
            print(f"  {label_id}({col}): 数据不足（{len(series)}个非空值），跳过")
            time_changes.append({
                "标编号": label_id,
                "原始列名": col,
                "第一个非空值": np.nan,
                "最后一个非空值": np.nan,
                "时间变化量": np.nan
            })

    df_time_changes = pd.DataFrame(time_changes)

    # 保存到全局列表
    for _, row in df_time_changes.iterrows():
        all_time_change_details.append({
            "站点名称": site_name,
            "站点编号": site_num,
            "标编号": row['标编号'],
            "原始列名": row['原始列名'],
            "第一个非空值": row['第一个非空值'],
            "最后一个非空值": row['最后一个非空值'],
            "时间变化量": row['时间变化量']
        })

    # ===== 步骤2: 获取该站点编号对应的层位映射 =====
    if site_num not in site_label_map or not site_label_map[site_num]:
        print(f"  ❌ 站点{site_num} 在映射表中没有找到有效的层位映射")
        continue

    site_labels = site_label_map[site_num]
    print(f"  该站点的层位映射: {site_labels}")

    # ===== 步骤3: 筛选有数字标识的标编号，获取其时间变化量 =====
    layer_time_change = {}  # 存储 {层位数字: 时间变化量}
    print(f"\n  匹配标编号与时间变化量:")

    for _, row in df_time_changes.iterrows():
        label_id = row['标编号']
        time_change = row['时间变化量']

        if pd.isna(time_change):
            print(f"    ⚠️  {label_id}: 时间变化量为NaN（数据不足）")
            continue

        if label_id in site_labels:
            layer_num = site_labels[label_id]
            layer_time_change[layer_num] = {
                "标编号": label_id,
                "时间变化量": time_change
            }
            print(f"    ✅ {label_id} -> 层位{layer_num}: 时间变化量 = {time_change:.4f}")
        else:
            print(f"    ❌ {label_id}: 在映射表中未找到")

    if len(layer_time_change) == 0:
        print(f"  ❌ 没有找到有层位映射且数据完整的标编号")
        continue

    # ===== 步骤4: 按层位数字排序，计算相邻层位的差值 =====
    sorted_layers = sorted(layer_time_change.keys())

    print(f"\n  当前站点的层位: {sorted_layers}")

    if len(sorted_layers) < 2:
        print(f"  ❌ 有数字标识的层位少于2个，无法计算差值")
        continue

    print(f"\n  {site_name} 层位时间变化量汇总:")
    for layer_num in sorted_layers:
        info = layer_time_change[layer_num]
        print(f"    层位{layer_num}: {info['标编号']} 时间变化量 = {info['时间变化量']:.4f}")

    # 计算相邻层位差值
    # 差值 = 起始层位时间变化量 - 结束层位时间变化量
    # 例如: 0-1差值 = 层位0时间变化量 - 层位1时间变化量
    print(f"\n  {site_name} 层位差值计算:")
    diff_count = 0
    for i in range(len(sorted_layers) - 1):
        curr_num = sorted_layers[i]  # 起始层位（如0）
        next_num = sorted_layers[i + 1]  # 结束层位（如1）

        if next_num - curr_num == 1:
            curr_info = layer_time_change[curr_num]
            next_info = layer_time_change[next_num]

            curr_change = curr_info["时间变化量"]  # 起始层位时间变化量
            next_change = next_info["时间变化量"]  # 结束层位时间变化量

            # 差值 = 起始层位 - 结束层位
            difference = curr_change - next_change
            diff_label = f"{curr_num}-{next_num}"

            print(
                f"    ✅ {diff_label}: {curr_info['标编号']}({curr_change:.4f}) - {next_info['标编号']}({next_change:.4f}) = {difference:.4f}")

            all_layer_differences.append({
                "站点名称": site_name,
                "站点编号": site_num,
                "差值区间": diff_label,
                "起始层位数字": curr_num,
                "结束层位数字": next_num,
                "起始标编号": curr_info["标编号"],
                "结束标编号": next_info["标编号"],
                "起始时间变化量": curr_change,
                "结束时间变化量": next_change,
                "层位差值": difference
            })
            diff_count += 1
        else:
            print(f"    ⚠️  跳过非连续层位: {curr_num} -> {next_num} (差值={next_num - curr_num})")

    if diff_count == 0:
        print(f"  ⚠️  没有计算出任何层位差值（可能层位不连续）")

# ========== 8. 导出结果 ==========

df_time_change_all = pd.DataFrame(all_time_change_details)
df_time_change_all.to_excel("所有站点_时间变化量明细.xlsx", index=False)

df_layer_differences = pd.DataFrame(all_layer_differences)
df_layer_differences.to_excel("监测层位差值结果.xlsx", index=False)

print(f"\n{'=' * 60}")
print("✅ 处理完成！已导出以下文件:")
print(f"{'=' * 60}")
print("  1. 所有站点_时间变化量明细.xlsx")
print("  2. 监测层位差值结果.xlsx")
print(f"\n共计算了 {len(all_layer_differences)} 个层位差值")

if len(all_layer_differences) == 0:
    print("\n⚠️  提示: 没有计算出任何层位差值")
    print("请检查映射表中标编号的监测层位是否填写了数字（0,1,2,3,4）")
    print("以及数据文件中标编号是否与映射表中的标编号一致")
else:
    print("\n计算结果预览:")
    print(df_layer_differences[['站点名称', '差值区间', '层位差值']].to_string(index=False))

print("\n差值计算说明:")
print("  - 时间变化量 = 第一个非空值 - 最后一个非空值")
print("  - 层位差值 = 起始层位时间变化量 - 结束层位时间变化量")
print("  - 例如: 0-1差值 = 层位0时间变化量 - 层位1时间变化量")