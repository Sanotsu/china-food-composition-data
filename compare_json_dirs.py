import os
import json
from pathlib import Path
from collections import defaultdict
import time

def get_food_items(directory, prefix):
    """获取指定目录中所有JSON文件中的食品项"""
    food_items = {}
    
    for filename in os.listdir(directory):
        if not filename.startswith(prefix) or not filename.endswith('.json'):
            continue
            
        filepath = os.path.join(directory, filename)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for item in data:
                    if 'foodCode' in item and item['foodCode'].strip():
                        food_code = item['foodCode'].strip()
                        food_items[food_code] = {
                            'file': filename,
                            'foodName': item.get('foodName', '').strip(),
                            'source_file': filename
                        }
        except (json.JSONDecodeError, KeyError) as e:
            print(f"警告: 无法解析文件 {filename}: {e}")
            continue
    
    return food_items

def get_file_list(directory, prefix):
    """获取指定目录中所有JSON文件的文件名（不含前缀和后缀）"""
    files = set()
    for filename in os.listdir(directory):
        if filename.startswith(prefix) and filename.endswith('.json'):
            # 去掉前缀和后缀，保留中间部分作为标识
            base_name = filename[len(prefix):-5]  # 去掉前缀和.json
            files.add(base_name)
    return files

def normalize_food_name(name):
    """标准化食品名称：去除所有空格和括号"""
    if not name:
        return ""
    # 替换所有括号和空格
    import re
    return re.sub(r'[\s()（）]', '', name)

def get_detailed_comparison(data_items, vision_items):
    """获取详细的比较结果，包括食品项和所在文件"""
    result = []
    for code, data_info in data_items.items():
        vision_info = vision_items.get(code)
        if not vision_info:
            result.append({
                'foodCode': code,
                'foodName': data_info['foodName'],
                'status': 'only_in_data',
                'data_file': data_info['source_file'],
                'vision_file': None
            })
        else:
            # 标准化食品名称后比较
            data_name = normalize_food_name(data_info['foodName'])
            vision_name = normalize_food_name(vision_info['foodName'])
            if data_name != vision_name:
                result.append({
                    'foodCode': code,
                    'data_foodName': data_info['foodName'],
                    'vision_foodName': vision_info['foodName'],
                    'status': 'name_differs',
                    'data_file': data_info['source_file'],
                    'vision_file': vision_info['source_file']
                })
    
    # 添加仅在vision中存在的项目
    for code, vision_info in vision_items.items():
        if code not in data_items:
            result.append({
                'foodCode': code,
                'foodName': vision_info['foodName'],
                'status': 'only_in_vision',
                'data_file': None,
                'vision_file': vision_info['source_file']
            })
    
    return result

def main():
    # 设置路径
    base_dir = os.path.dirname(__file__)
    json_data_dir = os.path.join(base_dir, 'json_data_vision_251204_Qwen2-5-VL-72B-Instruct')
    json_vision_dir = os.path.join(base_dir, 'json_data_vision_251205_glm-4.5v')
    
    # 获取文件列表
    print("正在扫描目录...")
    json_data_files = get_file_list(json_data_dir, 'merged_')
    json_vision_files = get_file_list(json_vision_dir, 'merged_')
    
    # 获取食品项
    print("正在分析食品数据...")
    json_data_items = get_food_items(json_data_dir, 'merged_')
    json_vision_items = get_food_items(json_vision_dir, 'merged_')
    
    # 计算文件差异
    only_in_data_files = json_data_files - json_vision_files
    only_in_vision_files = json_vision_files - json_data_files
    
    # 获取详细的食品项比较
    detailed_comparison = get_detailed_comparison(json_data_items, json_vision_items)
    
    # 分类比较结果
    only_in_data = [item for item in detailed_comparison if item['status'] == 'only_in_data']
    only_in_vision = [item for item in detailed_comparison if item['status'] == 'only_in_vision']
    name_differs = [item for item in detailed_comparison if item['status'] == 'name_differs']
    
    # 输出结果
    print("\n" + "="*80)
    print(f"{'统计信息':^80}")
    print("="*80)
    print(f"{json_data_dir} 目录中的文件数量: {len(json_data_files)}")
    print(f"{json_vision_dir} 目录中的文件数量: {len(json_vision_files)}")
    print(f"{json_data_dir} 目录中的食品数量: {len(json_data_items)}")
    print(f"{json_vision_dir} 目录中的食品数量: {len(json_vision_items)}")
    
    print("\n" + "="*80)
    print(f"{'文件差异':^80}")
    print("="*80)
    
    print(f"\n仅在 {json_data_dir} 中存在的文件 ({len(only_in_data_files)}):")
    for file in sorted(only_in_data_files):
        print(f"  - {file}")
    
    print(f"\n仅在 {json_vision_dir} 中存在的文件 ({len(only_in_vision_files)}):")
    for file in sorted(only_in_vision_files):
        print(f"  - {file}")
    
    print("\n" + "="*80)
    print(f"{'食品项差异':^80}")
    print("="*80)
    
    print(f"\n1. 仅在 json_data 中存在的食品项 ({len(only_in_data)}):")
    for item in sorted(only_in_data, key=lambda x: x.get('foodCode', ''))[:30]:
        print(f"   - 食品代码: {item['foodCode']}")
        print(f"     食品名称: {item['foodName']}")
        print(f"     所在文件: {item['data_file']}\n")
    if len(only_in_data) > 30:
        print(f"   ... 以及另外 {len(only_in_data) - 30} 项")
    
    print(f"\n2. 仅在 json_data_vision 中存在的食品项 ({len(only_in_vision)}):")
    for item in sorted(only_in_vision, key=lambda x: x.get('foodCode', ''))[:30]:
        print(f"   - 食品代码: {item['foodCode']}")
        print(f"     食品名称: {item['foodName']}")
        print(f"     所在文件: {item['vision_file']}\n")
    if len(only_in_vision) > 30:
        print(f"   ... 以及另外 {len(only_in_vision) - 30} 项")
    
    print(f"\n3. 食品名称不一致的项 ({len(name_differs)}):")
    for item in sorted(name_differs, key=lambda x: x.get('foodCode', ''))[:20]:
        print(f"   - 食品代码: {item['foodCode']}")
        print(f"     {json_data_dir} 中的名称: {item['data_foodName']}")
        print(f"     {json_vision_dir} 中的名称: {item['vision_foodName']}")
        print(f"     {json_data_dir} 文件: {item['data_file']}")
        print(f"     {json_vision_dir} 文件: {item['vision_file']}\n")
    
    # 保存结果到文件
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    filename = f'comparison_report_{timestamp}.log'
    with open(filename, 'w', encoding='utf-8') as f:
        f.write("="*80 + "\n")
        f.write(f"{'统计信息':^80}\n")
        f.write("="*80 + "\n\n")
        f.write(f"{json_data_dir} 目录中的文件数量: {len(json_data_files)}\n")
        f.write(f"{json_vision_dir} 目录中的文件数量: {len(json_vision_files)}\n")
        f.write(f"{json_data_dir} 目录中的食品数量: {len(json_data_items)}\n")
        f.write(f"{json_vision_dir} 目录中的食品数量: {len(json_vision_items)}\n\n")
        
        f.write("="*80 + "\n")
        f.write(f"{'文件差异':^80}\n")
        f.write("="*80 + "\n\n")
        
        f.write(f"1. 仅在 json_data 中存在的文件 ({len(only_in_data_files)}):\n")
        for file in sorted(only_in_data_files):
            f.write(f"   - {file}\n")
        
        f.write(f"\n2. 仅在 json_data_vision 中存在的文件 ({len(only_in_vision_files)}):\n")
        for file in sorted(only_in_vision_files):
            f.write(f"   - {file}\n")
        
        f.write("\n" + "="*80 + "\n")
        f.write(f"{'食品项差异':^80}\n")
        f.write("="*80 + "\n\n")
        
        f.write(f"1. 仅在 {json_data_dir} 中存在的食品项 ({len(only_in_data)}):\n")
        for item in sorted(only_in_data, key=lambda x: x.get('foodCode', '')):
            f.write(f"   - 食品代码: {item['foodCode']}\n")
            f.write(f"     食品名称: {item['foodName']}\n")
            f.write(f"     所在文件: {item['data_file']}\n\n")
        
        f.write(f"\n2. 仅在 {json_vision_dir} 中存在的食品项 ({len(only_in_vision)}):\n")
        for item in sorted(only_in_vision, key=lambda x: x.get('foodCode', '')):
            f.write(f"   - 食品代码: {item['foodCode']}\n")
            f.write(f"     食品名称: {item['foodName']}\n")
            f.write(f"     所在文件: {item['vision_file']}\n\n")
        
        f.write(f"\n3. 食品名称不一致的项 ({len(name_differs)}):\n")
        for item in sorted(name_differs, key=lambda x: x.get('foodCode', '')):
            f.write(f"   - 食品代码: {item['foodCode']}\n")
            f.write(f"     {json_data_dir} 中的名称: {item['data_foodName']}\n")
            f.write(f"     {json_vision_dir} 中的名称: {item['vision_foodName']}\n")
            f.write(f"     {json_data_dir} 文件: {item['data_file']}\n")
            f.write(f"     {json_vision_dir} 文件: {item['vision_file']}\n\n")
    
    print("\n详细报告已保存到 {filename}")

if __name__ == "__main__":
    main()
