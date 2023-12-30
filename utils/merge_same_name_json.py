# 通过ocr识别表格图表后，转成excel，excel转为json文件，3个部分都是两个类别:energy 和 nutrient。
# 通过指定的json文件命名，把两份同名json文件的营养素合并成同一份食物的营养素json文件。


"""
现在有多份类似json文件如下:
    驴-energy.json
    驴-nutrient.json
    牛1-energy.json
    牛1-nutrient.json
    牛2-energy.json
    牛2-nutrient.json

我需要合并同名energy.json和nutrient.json结尾的json文件中的列表数据。
    `驴-energy.json`、`驴-nutrient.json` => 合并到`驴.json`；
    `牛1-energy.json`、`牛1-nutrient.json`、`牛2-energy.json`、`牛2-nutrient.json` => 合并到`牛.json`中去。

思路：
    先把所有同名的`-energy.json` 和 `-nutrient.json`合并成`merged-xxx.json`。
    再寻找匹配后的同名`merged-xxx1.json`、`merged-xxx2.json`……等合并成一个`merged-xxx.json`，
        并删除旧的`merged-xxx1.json`、`merged-xxx2.json`……。

"""
import os
import json
import re


def merge_same_name_json(folder_path):
    # 获取文件夹中的所有文件名
    file_names: list[str] = os.listdir(folder_path)

    # 建立字典来存储基础文件名对应的数据列表
    merged_data_dict: dict[str, list] = {}

    # 遍历文件名
    for file_name in file_names:
        # 如果文件是以-energy.json结尾的，
        if file_name.endswith("-energy.json"):
            # 从文件名中获取到食物分类名称
            base_name: str = file_name.replace("-energy.json", "")

            # 判断是否存在对应的 -nutrient.json 文件
            nutrient_file_name = base_name + "-nutrient.json"
            if nutrient_file_name in file_names:
                # 如果找到对应的营养素信息，分别打开能量json和营养素json文件，加载对应的json数据
                with open(os.path.join(folder_path, file_name), "r") as energy_file:
                    energy_data = json.load(energy_file)
                with open(
                    os.path.join(folder_path, nutrient_file_name), "r"
                ) as nutrient_file:
                    nutrient_data = json.load(nutrient_file)

                # 把两个文件中的json数据合并【注意，前提是两个文件数组长度一致】
                merged_data = []
                for i in range(len(energy_data)):
                    merged_item = {**energy_data[i], **nutrient_data[i]}
                    merged_data.append(merged_item)

                # 将合并后的数据添加到字典中(字典中已存在则追加，不存在则新增)
                if base_name in merged_data_dict:
                    merged_data_dict[base_name].extend(merged_data)
                else:
                    merged_data_dict[base_name] = [merged_data]

    # 遍历字典，将数据写入到新文件中
    for base_name, merged_data_list in merged_data_dict.items():
        # 如果只有一个元素，去掉列表的外层括号
        if len(merged_data_list) == 1:
            merged_data = merged_data_list[0]
        else:
            merged_data = []
            for data in merged_data_list:
                merged_data.extend(data)

        # 合并后的文件添加前缀
        merged_file_name = "merged-" + base_name + ".json"
        with open(os.path.join(folder_path, merged_file_name), "w") as merged_file:
            json.dump(merged_data, merged_file, indent=4, ensure_ascii=False)

        print(f"合并完成：{merged_file_name}")

    #### 上面已经合并完所有的energy和nutrient文件到merge.json文件里
    # 这里把带有数字的同一类型的食物合并到一个json文件。

    # 因为是原文件夹，所以继续读
    merged_file_names: list[str] = os.listdir(folder_path)

    # 建立字典来存储基础文件名对应的数据列表
    final_data_dict = {}

    # 遍历文件名
    for file_name in merged_file_names:
        if file_name.startswith("merged-"):
            # 匹配文件名(结合起来就是以 merged-开始，以数字.json结尾的文件)
            match = re.match(r"(.+?)(\d+)\.json", file_name)
            if match:
                base_name = match.group(1)
                file_num = match.group(2)

                # 将同一基础文件名的文件合并到一个字典中
                if base_name not in final_data_dict:
                    final_data_dict[base_name] = {}
                with open(os.path.join(folder_path, file_name), "r") as energy_file:
                    energy_data = json.load(energy_file)
                    final_data_dict[base_name][file_num] = energy_data

                # 带数字分开了的json文件，读取了数据之后，就删除旧文件
                os.remove(os.path.join(folder_path, file_name))
                print(f"删除旧文件：{file_name}")

    # 合并能量数据并写入新文件
    for base_name, file_data in final_data_dict.items():
        merged_data = []

        for file_num in sorted(file_data.keys(), key=lambda x: int(x)):
            energy_data = file_data[file_num]
            merged_data.extend(energy_data)

        merged_file_name = f"{base_name}.json"

        with open(os.path.join(folder_path, merged_file_name), "w") as merged_file:
            json.dump(merged_data, merged_file, indent=4, ensure_ascii=False)
        print(f"合并新文件：{merged_file_name}")


# # 指定文件夹路径
# folder_path = "../合并测试的json"
# merge_same_name_json(folder_path)
