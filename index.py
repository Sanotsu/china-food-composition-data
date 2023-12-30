import time
from utils.image_to_excel import (
    process_table_image,
    conc_process_folder_images,
    conc_recursively_process_folder_images,
)
from utils.excel_to_json import (
    convert_excel_to_json,
    merge_excel_to_json,
    recursively_merge_excel_to_json,
    nutrient_cols,
    energy_cols,
)

from utils.merge_same_name_json import merge_same_name_json


start_time = time.time()  # 开始计时

# # 需要ocr的图片源文件夹
imags = "./营养素截图"
# # 飞桨OCR识别时候保存为excel文件、转换为json文件等中间文件的文件夹
outputs = "./OUTPUTS"


# # 需要ocr的图片源文件夹
# imags = "./少量测试_营养素截图"
# # 飞桨OCR识别时候保存为excel文件的文件夹
# outputs = "./少量测试_营养素OCR输出"


# 1 截图ocr后转为excel
conc_recursively_process_folder_images(imags, outputs)

# 2 将excel文件转为json文件（转换后的json还是在excel文件夹下）
recursively_merge_excel_to_json(outputs, energy_cols, nutrient_cols)

# 3 合并同一个分类下的json（合并后的json也在excel文件夹下）
merge_same_name_json(outputs)

end_time = time.time()  # 结束计时
total_time = end_time - start_time
print(f"截图ocr总耗时: {total_time}秒")


"""
截图表格中符号及其含义：
符号	意义
X	    代表值，几条相同食物数据计算的中位数或均数
Tr	    未检出或微量，低于目前应用的检测方法的检出线或未检出
(0)	    估计0值，理论上为0值或不存在，或测定后为0
*	    参考相似食物或原料数据计算而得或参考值
——	    未检测，理论上食物中应该存在一定量的该种成分，但未实际检测
un	    不能计算，或未测定
"""
