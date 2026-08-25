import os
import re
from pathlib import Path

def find_image_files(base_dir):
    """查找所有图片文件，并返回按类别和食物名称分组的字典"""
    image_files = {}
    
    # 遍历所有分类目录
    for category_dir in os.listdir(base_dir):
        category_path = os.path.join(base_dir, category_dir)
        if not os.path.isdir(category_path):
            continue
            
        # 遍历分类目录下的所有文件
        for filename in os.listdir(category_path):
            if not (filename.endswith('-energy.png') or filename.endswith('-nutrient.png')):
                continue
                
            # 提取食物名称（去掉数字和-energy/-nutrient）
            match = re.match(rf'^{re.escape(category_dir)}-(.*?)(\d*)-(energy|nutrient)\.png$', filename)
            if not match:
                continue
                
            food_name = match.group(1)
            food_key = f"{category_dir}-{food_name}"
            
            if food_key not in image_files:
                image_files[food_key] = {
                    'category': category_dir,
                    'food': food_name,
                    'energy': False,
                    'nutrient': False,
                    'files': []
                }
            
            # 标记找到的文件类型
            if filename.endswith('-energy.png'):
                image_files[food_key]['energy'] = True
            else:
                image_files[food_key]['nutrient'] = True
            
            image_files[food_key]['files'].append(os.path.join(category_dir, filename))
    
    return image_files

def check_json_files(json_dir, image_data):
    """检查JSON文件是否存在"""
    missing = []
    complete = []
    
    for food_key, data in image_data.items():
        json_file = f"merged_{food_key}.json"
        json_path = os.path.join(json_dir, json_file)
        
        if os.path.exists(json_path):
            complete.append({
                'food_key': food_key,
                'json_file': json_file,
                'energy_found': data['energy'],
                'nutrient_found': data['nutrient'],
                'files': data['files']
            })
        else:
            missing.append({
                'food_key': food_key,
                'json_file': json_file,
                'energy_found': data['energy'],
                'nutrient_found': data['nutrient'],
                'files': data['files']
            })
    
    return complete, missing

def main():
    # 设置路径
    base_dir = os.path.join(os.path.dirname(__file__), '营养素截图')
    json_dir = os.path.join(os.path.dirname(__file__), 'json_data_vision')
    
    print(f"正在扫描图片目录: {base_dir}")
    print(f"正在检查JSON目录: {json_dir}")
    
    # 查找所有图片文件
    image_data = find_image_files(base_dir)
    print(f"\n找到 {len(image_data)} 种食物的图片文件")
    
    # 检查JSON文件
    complete, missing = check_json_files(json_dir, image_data)
    
    # 输出结果
    print(f"\n✅ 已完成转换: {len(complete)} 种食物")
    for item in complete:
        print(f"   - {item['food_key']} ({item['json_file']})")
    
    print(f"\n❌ 未完成转换: {len(missing)} 种食物")
    for item in missing:
        print(f"\n   - {item['food_key']} (应生成: {item['json_file']})")
        if not item['energy_found']:
            print(f"     缺少: energy 图片")
        if not item['nutrient_found']:
            print(f"     缺少: nutrient 图片")
    
    # 输出总结
    print("\n" + "="*50)
    print(f"转换完成情况: {len(complete)}/{len(image_data)} ({(len(complete)/len(image_data)*100):.1f}%)")
    print("="*50)

if __name__ == "__main__":
    main()
