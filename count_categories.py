import os
import re
import time
from collections import defaultdict, Counter
from typing import Dict, List, Tuple, Optional

def parse_processing_logs(log_file: str) -> Dict[str, int]:
    """
    解析处理日志文件，返回一个字典，键为文件名（不含扩展名），值为提取的数据行数
    """
    row_counts = {}
    
    if not os.path.exists(log_file):
        print(f"警告：未找到日志文件 {log_file}")
        return row_counts
    
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            for line in f:
                # 匹配形如 "2025-12-04 14:51:57,049 - INFO - 菌藻类-菌类3-nutrient.png 成功提取了 11 行数据" 的行
                if "成功提取了" in line and "行数据" in line and ".png" in line:
                    # 分割行以提取文件名和行数
                    parts = line.strip().split()
                    # 找到包含.png的部分作为文件名
                    for part in parts:
                        if part.endswith('.png'):
                            filename = os.path.splitext(part)[0]  # 移除.png扩展名
                            # 在同一行中查找行数
                            for i, p in enumerate(parts):
                                if p == "行数据" and i > 0 and parts[i-1].isdigit():
                                    row_count = int(parts[i-1])
                                    row_counts[filename] = row_count
                                    break
                            break
    except Exception as e:
        print(f"解析日志文件时出错: {e}")
    
    return row_counts

def count_categories(base_dir: str, log_file: Optional[str] = None) -> Tuple[Dict, Dict]:
    # 存储大分类及其对应的小分类集合
    category_stats = defaultdict(set)
    # 存储每个文件的详细信息
    file_details = defaultdict(list)
    
    # 解析日志文件获取行数信息
    row_counts = {}
    if log_file:
        row_counts = parse_processing_logs(log_file)
    
    # 遍历营养素截图文件夹
    for root, dirs, files in os.walk(base_dir):
        # 获取相对路径，用于判断是否是一级子文件夹
        rel_path = os.path.relpath(root, base_dir)
        
        # 只处理一级子文件夹
        if rel_path == '.':
            continue
        if os.sep in rel_path:  # 跳过多级子目录
            continue
            
        # 处理当前大分类下的所有文件
        for filename in files:
            if not (filename.lower().endswith('.png') or filename.lower().endswith('.jpg') or filename.lower().endswith('.jpeg')):
                continue
                
            # 移除文件扩展名
            name_without_ext = os.path.splitext(filename)[0]
            
            # 获取文件的行数（如果日志中有）
            rows = row_counts.get(name_without_ext, None)
            
            # 移除-energy或-nutrient后缀
            name_cleaned = re.sub(r'-(?:energy|nutrient)(-\d+)?$', '', name_without_ext)
            
            # 获取小分类名（取第一个"-"之后的部分）
            if '-' in name_cleaned:
                # 分割大分类名和小分类名
                main_part, sub_part = name_cleaned.split('-', 1)
                # 只保留小分类名的基本部分（去掉数字）
                subcategory = re.sub(r'\d+$', '', sub_part)
                if subcategory:  # 确保小分类名不为空
                    category_key = f"{main_part}-{subcategory}"
                    category_stats[rel_path].add(category_key)
                    file_details[rel_path].append({
                        'filename': filename,
                        'main_category': rel_path,
                        'subcategory': subcategory,
                        'full_name': name_cleaned,
                        'rows': rows  # 添加行数信息
                    })
            else:
                # 如果没有"-"，直接使用文件名（不含扩展名）
                subcategory = re.sub(r'\d+$', '', name_cleaned)
                category_stats[rel_path].add(subcategory)
                file_details[rel_path].append({
                    'filename': filename,
                    'main_category': rel_path,
                    'subcategory': subcategory,
                    'full_name': name_cleaned
                })
    
    return category_stats, file_details

def generate_markdown_report(stats, file_details, output_file):
    """生成Markdown格式的统计报告"""
    with open(output_file, 'w', encoding='utf-8') as md_file:
        # 计算总数据行数
        total_rows = 0
        for files in file_details.values():
            for file_info in files:
                if file_info.get('rows') is not None:
                    total_rows += file_info['rows']
        # 标题和基本信息
        md_file.write("# 营养素截图分类统计报告\n\n")
        md_file.write(f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        # 1. 总体统计
        md_file.write("## 1. 总体统计\n\n")
        md_file.write("| 统计项 | 数量 |\n")
        md_file.write("|--------|------|\n")
        md_file.write(f"| 大分类总数 | {len(stats)} |\n")
        
        # 计算总文件数
        total_files = sum(len(files) for files in file_details.values())
        md_file.write(f"| 总文件数 | {total_files} |\n")
        
        # 添加总数据行数
        md_file.write(f"| 处理日志中数据 | |\n")
        md_file.write(f"| 总食品行数 | {total_rows/2} |\n")
        md_file.write(f"| 总数据行数 | {total_rows} |\n\n")
        
        # 2. 各分类详细统计
        md_file.write("## 2. 各分类详细统计\n\n")
        
        for main_category, subcategories in sorted(stats.items()):
            # 大分类标题
            md_file.write(f"### {main_category}\n\n")
            
            # 文件统计
            files = file_details[main_category]
            subcategory_counter = Counter(file_info['subcategory'] for file_info in files)
            
            # 小分类统计表
            md_file.write("#### 小分类统计\n")
            # md_file.write("| 小分类 | 文件数 | 合并前子分类 | 合并后子分类 |\n")
            # md_file.write("|--------|--------|--------------|--------------|\n")
            md_file.write("| 小分类 | 文件数 | 合并前子分类 | \n")
            md_file.write("|--------|--------|--------------|\n")
            
            # 获取所有原始子分类名（未合并的）
            original_subcategories = set()
            for file_info in files:
                # 从文件名中提取原始子分类名（包含数字）
                name_without_ext = os.path.splitext(file_info['filename'])[0]
                name_cleaned = re.sub(r'-(?:energy|nutrient)(-\d+)?$', '', name_without_ext)
                if '-' in name_cleaned:
                    sub_part = name_cleaned.split('-', 1)[1]
                    original_subcategories.add(sub_part)
            
            # 写入小分类统计
            for subcategory, count in sorted(subcategory_counter.items()):
                # 找出合并前的小分类
                original_subs = [s for s in original_subcategories if s.startswith(subcategory)]
                # md_file.write(f"| {subcategory} | {count} | {', '.join(original_subs)} | {subcategory} |\n")
                md_file.write(f"| {subcategory} | {count} | {', '.join(original_subs)} |\n")

            
            # 文件列表
            md_file.write("\n#### 文件列表\n")
            md_file.write("| 文件名 | 子分类 | 数据行数 |\n")
            md_file.write("|--------|--------|----------|\n")
            for file_info in sorted(files, key=lambda x: x['filename']):
                rows = file_info.get('rows', '')
                rows_display = str(rows) if rows is not None else 'N/A'
                md_file.write(f"| {file_info['filename']} | {file_info['subcategory']} | {rows_display} |\n")
            
            md_file.write("\n---\n\n")
        
        # 3. 总结表格
        md_file.write("## 3. 分类统计总结\n\n")
        
        # 准备总结表格数据
        summary_data = []
        for main_category, subcategories in sorted(stats.items()):
            files = file_details[main_category]
            subcategory_counter = Counter(file_info['subcategory'] for file_info in files)
            
            # 获取合并前的子分类
            original_subcategories = set()
            for file_info in files:
                name_without_ext = os.path.splitext(file_info['filename'])[0]
                name_cleaned = re.sub(r'-(?:energy|nutrient)(-\d+)?$', '', name_without_ext)
                if '-' in name_cleaned:
                    sub_part = name_cleaned.split('-', 1)[1]
                    original_subcategories.add(sub_part)
            
            # 添加每个小分类的数据
            for subcategory, count in sorted(subcategory_counter.items()):
                # 找出合并前的小分类
                original_subs = [s for s in original_subcategories if s.startswith(subcategory)]
                summary_data.append({
                    'main_category': main_category,
                    'subcategory': subcategory,
                    'file_count': count,
                    'original_subs': original_subs
                })
        
        # 按大分类和小分类排序
        summary_data.sort(key=lambda x: (x['main_category'], x['subcategory']))
        
        # 写入总结表格
        md_file.write("| 大分类 | 小分类 | 文件数  |\n")
        md_file.write("|--------|--------|--------|\n")
        
        for item in summary_data:
            md_file.write(f"| {item['main_category']} | {item['subcategory']} | {item['file_count']} |\n")
        
        # 4. 统计摘要
        md_file.write("\n## 4. 统计摘要\n\n")
        md_file.write(f"- 共统计 {len(stats)} 个大分类\n")
        md_file.write(f"- 共 {sum(len(subs) for subs in stats.values())} 个合并后的小分类\n")
        md_file.write(f"- 共 {total_files} 个文件\n\n")
        
        md_file.write("---\n\n")
        md_file.write("*本报告由脚本自动生成*\n")

def main():
    # 设置文件路径
    base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '营养素截图')
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    output_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), f'分类统计报告_{timestamp}.md')
    log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index_vision_llm_processor.log')
    
    if not os.path.exists(base_dir):
        print(f"错误：找不到目录 {base_dir}")
        return
    
    # 获取统计结果
    stats, file_details = count_categories(base_dir, log_file)
    
    # 生成Markdown报告
    generate_markdown_report(stats, file_details, output_file)
    print(f"统计报告已生成: {output_file}")
    
    # 控制台输出简略统计
    print("\n{:<15} {:<15} {}".format("大分类", "小分类数量", "文件数"))
    print("-" * 50)
    
    for main_category, subcategories in sorted(stats.items()):
        file_count = len(file_details[main_category])
        print("{:<15} {:<15} {}".format(
            main_category[:14],  # 限制大分类名称长度
            len(subcategories),
            file_count
        ))
    
    print("-" * 50)
    print("{:<15} {:<15} {}".format(
        "合计", 
        sum(len(s) for s in stats.values()),
        sum(len(files) for files in file_details.values())
    ))

if __name__ == "__main__":
    main()
