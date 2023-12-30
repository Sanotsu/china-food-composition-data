import os
import pandas as pd
import json

# 把只有数据的excel文件转为指定属性名称的json文件

energy_cols_cn = [
    "食物编码",
    "食物名称",
    "食部%",
    "水分g",
    "能量kcal",
    "能量kJ",
    "蛋白质g",
    "脂肪g",
    "碳水化合物g",
    "不溶性膳食纤维g",
    "胆固醇mg",
    "灰分g",
    "总维生素A(μgRAE)",
    "胡萝卜素μg",
    "视黄醇μg",
    "硫胺素mg",
    "核黄素mg",
]
energy_cols = [
    "foodCode",
    "foodName",
    "edible",
    "water",
    "energyKCal",
    "energyKJ",
    "protein",
    "fat",
    "CHO",
    "dietaryFiber",
    "cholesterol",
    "ash",
    "vitaminA",
    "carotene",
    "retinol",
    "thiamin",
    "riboflavin",
]

nutrient_cols_cn = [
    "食物编码",
    "食物名称",
    "烟酸",
    "维生素C",
    "维生素E(总)",
    "维生素E(1)",
    "维生素E(2)",
    "维生素E(3)",
    "钙",
    "磷",
    "钾",
    "钠",
    "镁",
    "铁",
    "锌",
    "硒",
    "铜",
    "锰",
    "备注",
]
nutrient_cols = [
    "foodCode",
    "foodName",
    "niacin",
    "vitaminC",
    "vitaminETotal",
    "vitaminE1",
    "vitaminE2",
    "vitaminE3",
    "Ca",
    "P",
    "K",
    "Na",
    "Mg",
    "Fe",
    "Zn",
    "Se",
    "Cu",
    "Mn",
    "remark",
]

"""
我要遍历一个文件夹中所有的excel文件，excel没有标题，全是数据，然后来转换成json,把文件夹的名称作为json文件的名称，需要自定义属性名称。
excel表格中某些列的栏位数据可能为空或者横线-，遇到这种数据时，转换为json时统一改为'NaN'。在使用python的pandas情况下，编写一个脚本
"""


def convert_excel_to_json(file: str, col_names: list[str]):
    """指定文件夹中每个excel文件转换为单独的json文件,json名称和excel文件同名

    Args:
        folder_path (str):指定存放.xlsx文件的文件夹路径
        nutrient_cols (list): json文件自定义属性名称列表
    """

    data = []
    if file.endswith(".xlsx"):
        json_filename = os.path.splitext(file)[0] + ".json"

        # 读取 Excel 文件
        df = pd.read_excel(file, header=None)  # 假设没有标题行

        # 将空数据或横线替换为 0.0
        df = df.applymap(
            lambda x: "NaN" if pd.isna(x) or x == "-" or x == "一" else f"{x}"
        )
        data.append(df)

        if len(data) > 0:
            merged_data = pd.concat(data)

            # 将数据保存为 JSON 文件
            json_data = merged_data.to_dict(orient="records")

            custom_json_data = [
                {
                    custom_attr_name: row[attr_name]
                    for custom_attr_name, attr_name in zip(
                        col_names, merged_data.columns
                    )
                }
                for row in json_data
            ]

            with open(
                json_filename,
                "w",
            ) as json_file:
                json.dump(custom_json_data, json_file, ensure_ascii=False)

            print(f"转换完成: {file} -> {json_filename}")


def merge_excel_to_json(folder_path, attribute_names):
    """将指定文件夹中的excel合并成一个json文件

    Args:
        folder_path (str): 文件夹地址
        attribute_names (list): 自定义栏位名称列表
    """

    # 如果输出的文件夹不存在，则创建一个
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)

    # 获取路径最后一个文件夹名称作为合并后json文件名称
    folder_names = folder_path.split(os.path.sep)  # 使用操作系统特定的路径分隔符分割路径
    last_folder_name = folder_names[-1]  # 获取最后一个元素即最后一个文件夹的名称

    data = []  # 存储合并后的数据

    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)
        if filename.endswith(".xlsx"):
            # 读取 Excel 文件
            # 使用 converters 参数，字典的key值就是你要指定保留0的数据列，可以指定列名，如果没有列名可以指定序号
            # 指定第一列是组字符串(已0开头的数值默认会把0去掉)
            df = pd.read_excel(file_path, header=None, converters={0: str})

            df = df.applymap(
                lambda x: "NaN" if pd.isna(x) or x == "-" or x == "一" else f"{x}"
            )

            # 添加到数据列表
            data.append(df)

    # 如果知道文件夹下有excel文件，才能读到数据
    if len(data) > 0:
        # 合并所有数据
        merged_data = pd.concat(data)

        # 转换为 JSON
        json_data = merged_data.to_dict(orient="records")

        # 构造带有自定义属性名的字典
        custom_json_data = [
            {
                custom_attr_name: row[attr_name]
                for custom_attr_name, attr_name in zip(
                    attribute_names, merged_data.columns
                )
            }
            for row in json_data
        ]

        # 将数据保存为 JSON 文件，保存在对应文件夹下面
        json_filename = os.path.join(folder_path, f"{last_folder_name}.json")
        with open(json_filename, "w", encoding="utf-8") as json_file:
            json.dump(custom_json_data, json_file, ensure_ascii=False)

        print(f"合并完成，{last_folder_name} 保存为: {json_filename}")
    else:
        print(f"该文件夹{folder_path}下没有得到excel数据")


def recursively_merge_excel_to_json(folder_path, energy_cols, nutrient_cols):
    """将指定文件夹中的excel合并成一个json文件

    Args:
        folder_path (str): 文件夹地址
        attribute_names (list): 自定义栏位名称列表
    """

    # 如果输出的文件夹不存在，则创建一个
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)

    ## 为了每个文件夹的文件都放在最外层，这里

    # os.walk返回的root是当前文件夹的路径，dirs是当前文件夹中的子文件夹列表，files是当前文件夹中的文件列表。
    for root, dirs, files in os.walk(folder_path):
        ## 获取路径最后一个文件夹名称作为合并后json文件名称
        # 使用操作系统特定的路径分隔符分割路径
        folder_names = root.split(os.path.sep)
        # 获取最后一个元素即最后一个文件夹的名称
        last_folder_name = folder_names[-1]

        # 存储合并后的数据
        data = []

        # 处理当前文件夹中的文件
        for filename in files:
            file_path = os.path.join(root, filename)

            if filename.endswith(".xlsx"):
                # 读取 Excel 文件
                # 使用 converters 参数，字典的key值就是你要指定保留0的数据列，可以指定列名，如果没有列名可以指定序号
                # 指定第一列是组字符串(已0开头的数值默认会把0去掉)
                df = pd.read_excel(file_path, header=None, converters={0: str})

                # df = df.applymap(
                #     lambda x: "NaN" if pd.isna(x) or x == "-" or x == "一" else f"{x}"
                # )

                # X	    代表值，几条相同食物数据计算的中位数或均数
                # Tr	未检出或微量，低于目前应用的检测方法的检出线或未检出
                # (0)	估计0值，理论上为0值或不存在，或测定后为0
                # *	    参考相似食物或原料数据计算而得或参考值
                # ——    未检测，理论上食物中应该存在一定量的该种成分，但未实际检测
                # un	不能计算，或未测定

                # 空格和长横线可能会识别为null或者一字，这里统一转一下
                df = df.applymap(lambda x: "" if pd.isna(x) else f"{x}")
                df = df.applymap(lambda x: "-" if x == "一" else f"{x}")

                # 添加到数据列表
                data.append(df)

        # 如果没有excel文件，就跳过合并json，直接遍历子文件
        if len(data) > 0:
            # 合并所有数据
            merged_data = pd.concat(data)

            attributes = energy_cols if "energy" in root else nutrient_cols

            # 转换为 JSON
            json_data = merged_data.to_dict(orient="records")

            # 构造带有自定义属性名的字典
            custom_json_data = [
                {
                    custom_attr_name: row[attr_name]
                    for custom_attr_name, attr_name in zip(
                        attributes, merged_data.columns
                    )
                }
                for row in json_data
            ]

            # 将数据保存为 JSON 文件，保存在对应文件夹下面
            json_filename = os.path.join(folder_path, f"{last_folder_name}.json")
            with open(json_filename, "w", encoding="utf-8") as json_file:
                json.dump(custom_json_data, json_file, ensure_ascii=False)

            print(f"合并完成，{last_folder_name} 保存为: {json_filename}")

    # 递归处理子文件夹
    for dir in dirs:
        dir_path = os.path.join(root, dir)
        recursively_merge_excel_to_json(dir_path, energy_cols, nutrient_cols)
