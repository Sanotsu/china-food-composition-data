import os
import cv2
from paddleocr import PPStructure, save_structure_res
import concurrent.futures
import time


# 处理单张表格图片
def process_table_image(img_path, save_folder):
    try:
        # 如果输出的文件夹不存在，则创建一个
        if not os.path.exists(save_folder):
            os.makedirs(save_folder)

        # table_engine = PPStructure(layout=False, show_log=True)
        table_engine = PPStructure(layout=False, show_log=False)

        img = cv2.imread(img_path)
        result = table_engine(img)
        print(f"table_engine处理完成---------->{img_path}")
        save_structure_res(
            result, save_folder, os.path.basename(img_path).split(".")[0]
        )

        print(f"【转换完成】<---------- {img_path}")
    except Exception as e:
        print(f"{img_path}转换出错: {str(e)}")


# 文件夹中for循环顺序执行图片转excel
def seq_process_folder_images(image_path, excel_output_path):
    start_time = time.time()  # 开始计时
    # 遍历指定文件夹
    for filename in os.listdir(image_path):
        # 找到里面每个文件
        file_path = os.path.join(image_path, filename)
        # 如果文件是已png结尾的(认为是图片)
        if filename.endswith(".png"):
            process_table_image(file_path, excel_output_path)

    end_time = time.time()  # 结束计时
    total_time = end_time - start_time
    print(f"所有文件转换完成(seq_process_folder_images)，总耗时: {total_time}秒 --")


# 文件夹中多线程处理表格图片(png后缀的文件)
def conc_process_folder_images(image_path, excel_output_path):
    # 遍历指定文件夹
    start_time = time.time()  # 开始计时
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = []
        for filename in os.listdir(image_path):
            # 找到里面每个文件
            file_path = os.path.join(image_path, filename)
            # 如果文件是已png结尾的(认为是图片)
            if filename.endswith(".png"):
                future = executor.submit(
                    process_table_image, file_path, excel_output_path
                )
                futures.append(future)

        # 等待所有任务完成
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()  # 获取任务结果
            except Exception as e:
                print(f"任务执行出错: {str(e)}")

    end_time = time.time()  # 结束计时
    total_time = end_time - start_time
    print(f"所有文件转换完成(conc_process_folder_images)，总耗时: {total_time}秒")


# 多进程递归处理指定文件夹中的表格图片(png后缀的文件)
def conc_recursively_process_folder_images(image_path, excel_output_path):
    # 遍历指定文件夹
    start_time = time.time()  # 开始计时

    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = []
        for root, dirs, files in os.walk(image_path):
            for filename in files:
                file_path = os.path.join(root, filename)
                # 如果文件是已png结尾的(认为是图片)
                if filename.endswith(".png"):
                    future = executor.submit(
                        process_table_image, file_path, excel_output_path
                    )
                    futures.append(future)

        # 等待所有任务完成
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()  # 获取任务结果
            except Exception as e:
                print(f"任务执行出错: {str(e)}")

        # 递归处理子文件夹
        for dir in dirs:
            dir_path = os.path.join(root, dir)
            conc_recursively_process_folder_images(dir_path, excel_output_path)

    end_time = time.time()  # 结束计时
    total_time = end_time - start_time
    print(f"所有文件转换完成(conc_recursively_process_folder_images)，总耗时: {total_time}秒")
