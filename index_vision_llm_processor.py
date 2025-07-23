#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import json
import time
import base64
import asyncio
import aiofiles
import logging
import argparse
import re
from openai import AsyncOpenAI
from pathlib import Path
from typing import List, Dict
from datetime import datetime, timedelta
from collections import defaultdict
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("index_vision_llm_processor.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 能量页的列定义
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

# 营养素页的列定义
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


def extract_markdown_table(response_text: str) -> str:
    """
    从可能被代码块标记包裹的文本中提取纯markdown表格内容
    
    参数:
        response_text: 大模型API返回的文本，可能被各种形式的```标记包裹
        
    返回:
        纯markdown表格内容，已去除包裹标记
    """
    # 增强的正则表达式，处理各种边界情况：
    # 1. 允许标记后紧跟内容(没有换行)
    # 2. 允许标记前后有或没有换行
    # 3. 处理各种大小写的标记
    pattern = r'^\s*```(?:markdown|MARKDOWN|md|MD)?\s*[\n]?(.*?)\s*```\s*$'
    
    # 使用re.DOTALL标志让.匹配包括换行符在内的所有字符
    match = re.fullmatch(pattern, response_text, re.DOTALL)
    
    if match:
        # 提取内容并去除可能的首尾空白
        content = match.group(1).strip()
        return content
    else:
        # 如果没有匹配到代码块标记，则返回原文本(去除首尾空白)
        return response_text.strip()

def sort_food_data_by_code(data: List[Dict]) -> List[Dict]:
    """
    按照foodCode对食品数据进行升序排序
    
    参数:
        data: 食品数据列表
        
    返回:
        排序后的食品数据列表
    """
    # 首先处理foodCode中可能包含的字母后缀
    def get_sort_key(item):
        food_code = item.get("foodCode", "0")
        # 如果foodCode是数字，直接返回整数值
        if food_code.isdigit():
            return (int(food_code), "")
        # 如果有字母后缀，如"124201x"，分离数字和字母部分
        match = re.match(r'(\d+)([a-zA-Z]*)', food_code)
        if match:
            return (int(match.group(1)), match.group(2))
        # 默认情况
        return (0, food_code)
    
    # 执行排序
    return sorted(data, key=get_sort_key)

def sort_json_files_in_directory(directory: str):
    """
    对指定目录中的所有JSON文件内的食品数据按foodCode进行排序并保存
    
    参数:
        directory: JSON文件所在目录路径
    """
    directory_path = Path(directory)
    if not directory_path.exists() or not directory_path.is_dir():
        print(f"错误: 目录 {directory} 不存在或不是有效目录")
        return
    
    json_files = list(directory_path.glob("*.json"))
    if not json_files:
        print(f"警告: 目录 {directory} 中没有找到JSON文件")
        return
    
    print(f"开始处理 {len(json_files)} 个JSON文件...")
    
    for json_file in json_files:
        try:
            # 读取JSON文件
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 如果不是列表，跳过
            if not isinstance(data, list):
                print(f"警告: {json_file} 不是JSON数组，跳过")
                continue
            
            # 排序数据
            sorted_data = sort_food_data_by_code(data)
            
            # 保存排序后的数据
            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump(sorted_data, ensure_ascii=False, indent=2, fp=f)
            
            print(f"已排序 {json_file} 中的 {len(data)} 条数据")
            
        except Exception as e:
            print(f"处理 {json_file} 时出错: {str(e)}")
    
    print(f"排序完成! 已处理 {len(json_files)} 个JSON文件")


class ImageProcessor:
    def __init__(self, input_dir: str, output_dir: str, max_workers: int = 3, 
                 api_key: str = None, api_base: str = None, max_retries: int = 3,
                 model_name: str = None, max_tokens: int = None, temperature: float = None):
        """初始化图像处理器

        Args:
            input_dir: 输入图片目录
            output_dir: 输出JSON目录
            max_workers: 最大并发工作线程数
            api_key: API密钥
            api_base: API基础URL
            max_retries: 最大重试次数
            model_name: 模型名称
            max_tokens: 最大响应token数
            temperature: 温度参数
        """
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True, parents=True)
        self.max_workers = max_workers
        self.max_retries = max_retries
        
        # 模型参数
        self.model_name = model_name or os.getenv("MODEL_NAME") or "Qwen/Qwen2.5-VL-72B-Instruct"
        self.max_tokens = int(max_tokens or os.getenv("MAX_TOKENS", "4000"))
        self.temperature = float(temperature or os.getenv("TEMPERATURE", "0.1"))
        
        ### 中间产物全都放到临时文件夹下
        ### 最终结果目录在merge_similar_categories()方法中
        self.temp_dir = Path("temp")
        self.temp_dir.mkdir(exist_ok=True, parents=True)

        # 创建失败记录目录
        self.failed_dir = self.temp_dir / "temp_failed_responses"
        self.failed_dir.mkdir(exist_ok=True)

        # 创建临时响应目录，记录原始响应以便调试
        self.temp_responses_dir = self.temp_dir / "temp_successful_responses"
        self.temp_responses_dir.mkdir(exist_ok=True)

        # 创建中间结果目录，一个energy+一个nutrient为一对截图，合并成一个中间结果json，
        # 如果该分类有多对截图，到最后会合并成一个最终的json
        self.temp_middle_dir = self.temp_dir / "temp_middle_jsons"
        self.temp_middle_dir.mkdir(exist_ok=True)
        
        # 初始化API客户端
        api_base = api_base or os.getenv("API_BASE") or "https://api.siliconflow.cn/v1"
        api_key = api_key or os.getenv("API_KEY") or "your-key-here"
        self.client = AsyncOpenAI(api_key=api_key, base_url=api_base)
        
        # 统计信息
        self.total_images = 0
        self.processed_images = 0
        self.failed_images = 0
        self.start_time = 0
        self.successful_pairs = set()
        self.failed_pairs = set()
        
        # 处理结果存储，用于最后合并
        self.results_by_category = defaultdict(list)
        
        # 进度锁，防止多线程同时更新进度导致输出混乱
        self.progress_lock = asyncio.Lock()
    
    async def encode_image_from_url(self, image_path: Path) -> str:
        """将本地图片编码为base64

        Args:
            image_path: 图片路径

        Returns:
            base64编码后的图片字符串
        """
        try:
            async with aiofiles.open(image_path, "rb") as image_file:
                return base64.b64encode(await image_file.read()).decode('utf-8')
        except Exception as e:
            logger.error(f"编码图片时出错 {image_path}: {str(e)}")
            raise
    
    async def process_image_with_retry(self, image_path: Path, is_energy: bool) -> List[Dict]:
        """带重试机制的图像处理

        Args:
            image_path: 图片路径
            is_energy: 是否为能量属性图片

        Returns:
            处理后的数据
        """
        retries = 0
        last_error = None
        
        while retries <= self.max_retries:
            try:
                if retries > 0:
                    wait_time = 5 ** retries  # 指数退避
                    logger.info(f"重试 ({retries}/{self.max_retries}) 处理 {image_path.name} 等待 {wait_time} 秒...")
                    await asyncio.sleep(wait_time)
                
                result = await self.process_image(image_path, is_energy)
                
                # 如果处理成功，删除可能存在的错误记录
                error_path = self.failed_dir / f"{image_path.stem}_error.txt"
                if error_path.exists():
                    try:
                        os.remove(error_path)
                        logger.info(f"重试成功，已删除错误记录: {error_path}")
                    except Exception as e:
                        logger.warning(f"无法删除错误记录 {error_path}: {str(e)}")
                
                return result
            
            except Exception as e:
                retries += 1
                last_error = e
                logger.warning(f"处理图片 {image_path.name} 时出错 (尝试 {retries}/{self.max_retries}): {str(e)}")
        
        # 所有重试都失败，这时才记录错误
        logger.error(f"在多次尝试后无法处理图片 {image_path.name}: {str(last_error)}")
        
        # 记录最终错误以便后续分析
        error_path = self.failed_dir / f"{image_path.stem}_error.txt"
        try:
            async with aiofiles.open(error_path, 'w', encoding='utf-8') as f:
                await f.write(f"Error after {self.max_retries} retries: {str(last_error)}")
        except Exception as e:
            logger.error(f"无法记录错误信息到 {error_path}: {str(e)}")
            
        return []
    
    async def process_image(self, image_path: Path, is_energy: bool) -> List[Dict]:
        """处理单个图片

        Args:
            image_path: 图片路径
            is_energy: 是否为能量属性图片

        Returns:
            处理后的数据
        """
        # 获取图片base64编码
        image_base64 = await self.encode_image_from_url(image_path)
        
        # 确定列名
        columns = energy_cols if is_energy else nutrient_cols
        
        # 构建提示
        prompt = f"""
        请识别下面表格中的所有数据，并按照markdown表格格式输出。这是中国食物成分表中的数据。
        
        表格列名为：{columns}
        
        请注意:
        1. 表格中的"—"、"-"或"Tr"表示微量或未检测到，应当保留这些特殊值。
        2. 请保持数值的原始格式。
        3. 请确保提取表格中的所有行，包括不完整的行。
        4. 请使用标准的markdown表格格式输出，第一行为表头（列名），然后是分隔行，之后是数据行。
        5. 不要输出JSON格式，只需输出markdown表格数据。
        6. 不要在表格外添加任何额外的文本、代码块标记或解释。
        
        例如:
        | foodCode | foodName | ... 其他列名 |
        | --- | --- | ... |
        | 121101 | 白条鱼（裸鱼） | ... 其他数据 |
        | 121102 | 草鱼 | ... 其他数据 |
        """
        
        # 调用API进行图像理解
        # 这个操作故意不放在try catch中，为的就是报错后抛到上层process_image_with_retry触发重试
        # 然后在重试过程中如果成功了，就删除报错的日志文件。在所有重试结束后都失败了，才记录最后失败报错
        start_time = time.time()
        completion = await self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}}
                    ],
                }
            ],
            max_tokens=self.max_tokens,
            temperature=self.temperature
        )
        
        processing_time = time.time() - start_time
        logger.info(f"处理 {image_path.name} 用时 {processing_time:.2f}秒")
        
        # 提取响应文本
        response_text = completion.choices[0].message.content
        response_text = extract_markdown_table(response_text)

        # 记录原始响应以便调试
        response_log_path = self.temp_responses_dir / f"{image_path.stem}_response.md"
        response_log_path.parent.mkdir(exist_ok=True)
        async with aiofiles.open(response_log_path, 'w', encoding='utf-8') as f:
            await f.write(response_text)
        
        # 解析表格数据
        data = self.parse_markdown_table(response_text, columns)
        
        # 验证数据
        if not data:
            logger.warning(f"{image_path.name} 未能提取到有效数据")
            raise ValueError(f"无法从 {image_path.name} 提取有效数据")
        else:
            logger.info(f"{image_path.name} 成功提取了 {len(data)} 行数据")
            
            # 检查数据完整性
            for item in data:
                if "foodCode" not in item or "foodName" not in item:
                    logger.warning(f"{image_path.name} 中有记录缺少foodCode或foodName: {item}")
        
        return data

    def parse_markdown_table(self, text: str, columns: List[str]) -> List[Dict]:
        """从Markdown表格文本解析数据

        Args:
            text: Markdown表格文本
            columns: 列名

        Returns:
            解析后的数据列表
        """
        result = []
        
        # 尝试提取markdown表格（会处理表格前后有空白行、最后一行可能没有换行符）
        table_pattern = r'^\s*\|(.*?)\|\s*\n\|([-:\s|]+)\|\s*\n((?:\|.*?\|\s*(?:\n|$))+)'
        table_match = re.search(table_pattern, text, re.DOTALL)
        
        if table_match:
            # 提取表头和数据行
            header_row = table_match.group(1)
            data_rows = table_match.group(3).strip().split('\n')
          
            # 解析表头
            headers = [h.strip() for h in header_row.split('|') if h.strip()]
            
            # 解析数据行
            for row in data_rows:
                cells = [cell.strip() for cell in row.split('|')[1:-1]]  # 去掉首尾的空字符串
                if len(cells) < 2:  # 至少需要foodCode和foodName
                    continue
                
                item = {}
                for i, cell in enumerate(cells):
                    if i < len(columns):
                        item[columns[i]] = cell
                
                if "foodCode" in item and "foodName" in item:
                    result.append(item)
            
            logger.info(f"使用markdown表格解析成功，找到 {len(result)} 行数据")
            return result
        
        # 如果无法解析markdown表格，尝试使用原始文本解析方法
        logger.info("无法解析markdown表格，尝试使用原始文本解析方法")
        return self.parse_table_data(text, columns)
    
    def parse_table_data(self, text: str, columns: List[str]) -> List[Dict]:
        """从表格文本解析数据

        Args:
            text: 表格文本
            columns: 列名

        Returns:
            解析后的数据列表
        """
        result = []
        
        # 分割文本为行
        lines = text.strip().split('\n')
        
        # 过滤掉空行和可能的表头行
        data_lines = [line.strip() for line in lines if line.strip() and not line.strip().startswith('---') and not line.strip().startswith('|')]
        
        for line in data_lines:
            # 检查行是否包含足够的数据
            if len(line) < 10 or not any(c.isdigit() for c in line):
                continue
                
            # 分割行为单元格
            # 首先尝试提取前两列（通常是foodCode和foodName）
            match = re.match(r'(\d+[a-zA-Z]?)\s+([^\d]+?)(?=\s+\d|\s+Tr|\s+—|\s+-|$)', line)
            if not match:
                continue
                
            food_code = match.group(1).strip()
            food_name = match.group(2).strip()
            
            # 创建数据项
            item = {
                "foodCode": food_code,
                "foodName": food_name
            }
            
            # 移除已处理的部分
            remaining = line[match.end():].strip()
            
            # 分割剩余部分为数值
            values = re.findall(r'((?:\d+\.\d+|\d+|Tr|—|-|–|[A-Za-z]+)(?:\s+|$))', remaining)
            values = [v.strip() for v in values if v.strip()]
            
            # 添加剩余的列
            for i, value in enumerate(values):
                if i + 2 < len(columns):  # +2 因为前两列已经处理
                    item[columns[i + 2]] = value
            
            # 确保至少有foodCode和foodName
            if "foodCode" in item and "foodName" in item:
                result.append(item)
        
        # 如果上面的方法没有产生结果，尝试更简单的分割方法
        if not result:
            logger.info("使用备用解析方法")
            for line in data_lines:
                parts = re.split(r'\s+', line.strip())
                if len(parts) < 3:  # 至少需要foodCode、foodName和一个数据
                    continue
                    
                # 尝试提取foodCode（通常是第一个数字）
                food_code = parts[0] if parts[0].isdigit() or (parts[0][:-1].isdigit() and parts[0][-1].isalpha()) else None
                if not food_code:
                    continue
                    
                # 尝试找到第一个非数字部分作为foodName
                food_name_parts = []
                i = 1
                while i < len(parts) and (not parts[i].replace('.', '', 1).isdigit() and parts[i] not in ['Tr', '—', '-']):
                    food_name_parts.append(parts[i])
                    i += 1
                
                if not food_name_parts:
                    continue
                    
                food_name = ' '.join(food_name_parts)
                
                # 创建数据项
                item = {
                    "foodCode": food_code,
                    "foodName": food_name
                }
                
                # 添加剩余的列
                for j, value in enumerate(parts[i:]):
                    if j + 2 < len(columns):  # +2 因为前两列已经处理
                        item[columns[j + 2]] = value
                
                result.append(item)
                
        return result
    
    async def save_json(self, data: List[Dict], output_path: Path):
        """保存JSON数据到文件

        Args:
            data: 要保存的数据
            output_path: 输出文件路径
        """
        try:
            async with aiofiles.open(output_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(data, ensure_ascii=False, indent=2))
            logger.info(f"保存数据到 {output_path}")
        except Exception as e:
            logger.error(f"保存JSON到 {output_path} 时出错: {str(e)}")
            raise
    
    async def process_image_pair(self, energy_path: Path, nutrient_path: Path, category: str):
        """处理一对图像（能量和营养素）

        Args:
            energy_path: 能量图片路径
            nutrient_path: 营养素图片路径
            category: 食品类别
        """
        # 提取基本名称用于输出JSON文件
        base_name = energy_path.stem.replace('-energy', '')
        
        logger.info(f"开始处理: {base_name}")
        
        try:
            # 并行处理两个图像
            energy_task = self.process_image_with_retry(energy_path, True)
            nutrient_task = self.process_image_with_retry(nutrient_path, False)
            
            energy_data, nutrient_data = await asyncio.gather(energy_task, nutrient_task)
            
            # 合并数据
            merged_data = self.merge_data(energy_data, nutrient_data)
            
            # 检查数据质量
            if not merged_data:
                logger.warning(f"{base_name} 合并后的数据为空")
                async with self.progress_lock:
                    self.failed_pairs.add(base_name)
                    self.failed_images += 2
                    self.processed_images += 2
                    self._report_progress(base_name, failed=True)
                return None
            else:
                # 保存中间JSON文件
                pair_output_path = self.temp_middle_dir / f"{base_name}.json"
                await self.save_json(merged_data, pair_output_path)
                logger.info(f"已保存中间结果到 {pair_output_path}")
                
                # 将结果添加到对应类别
                async with self.progress_lock:
                    self.results_by_category[category].extend(merged_data)
                    self.successful_pairs.add(base_name)
                    self.processed_images += 2
                    self._report_progress(base_name)
                return merged_data
            
        except Exception as e:
            logger.error(f"处理 {base_name} 时出错: {str(e)}")
            async with self.progress_lock:
                self.failed_pairs.add(base_name)
                self.failed_images += 2
                self.processed_images += 2
                self._report_progress(base_name, failed=True)
            return None
    
    def _report_progress(self, base_name: str, failed: bool = False):
        """报告处理进度

        Args:
            base_name: 当前处理的基本名称
            failed: 是否处理失败
        """
        progress = (self.processed_images / self.total_images) * 100
        elapsed_time = time.time() - self.start_time
        
        # 计算预计剩余时间
        if self.processed_images > 0:
            avg_time_per_image = elapsed_time / self.processed_images
            remaining_time = avg_time_per_image * (self.total_images - self.processed_images)
            eta = datetime.now() + timedelta(seconds=remaining_time)
            eta_str = eta.strftime("%H:%M:%S")
        else:
            remaining_time = 0
            eta_str = "未知"
        
        status = "失败" if failed else "完成"
        
        # 计算成功率
        success_rate = ((self.processed_images - self.failed_images) / self.processed_images * 100) if self.processed_images > 0 else 0
        
        logger.info(
            f"进度: {progress:.1f}% ({self.processed_images}/{self.total_images}) - {status}: {base_name}\n"
            f"已用时间: {timedelta(seconds=int(elapsed_time))} - 预计剩余时间: {timedelta(seconds=int(remaining_time))}\n"
            f"预计完成时间: {eta_str} - 成功率: {success_rate:.1f}%"
        )
    
    def merge_data(self, energy_data: List[Dict], nutrient_data: List[Dict]) -> List[Dict]:
        """合并能量和营养素数据

        Args:
            energy_data: 能量数据
            nutrient_data: 营养素数据

        Returns:
            合并后的数据
        """
        if not energy_data and not nutrient_data:
            return []
        
        merged = []
        
        # 使用foodCode作为键进行合并
        energy_dict = {item.get("foodCode", ""): item for item in energy_data if item.get("foodCode")}
        nutrient_dict = {item.get("foodCode", ""): item for item in nutrient_data if item.get("foodCode")}
        
        # 合并所有唯一的foodCode
        all_codes = set(energy_dict.keys()) | set(nutrient_dict.keys())
        
        for code in all_codes:
            energy_item = energy_dict.get(code, {})
            nutrient_item = nutrient_dict.get(code, {})
            
            # 创建合并项
            merged_item = {}
            
            # 添加能量数据
            for key in energy_cols:
                if key in energy_item:
                    merged_item[key] = energy_item[key]
            
            # 添加营养素数据（排除已有的foodCode和foodName）
            for key in nutrient_cols:
                if key not in ["foodCode", "foodName"] and key in nutrient_item:
                    merged_item[key] = nutrient_item[key]
            
            # 确保合并项至少包含foodCode和foodName
            if "foodCode" in merged_item and "foodName" in merged_item:
                merged.append(merged_item)
            else:
                # 尝试从两个源中获取这些关键字段
                if "foodCode" not in merged_item and "foodCode" in nutrient_item:
                    merged_item["foodCode"] = nutrient_item["foodCode"]
                if "foodName" not in merged_item and "foodName" in nutrient_item:
                    merged_item["foodName"] = nutrient_item["foodName"]
                
                # 再次检查
                if "foodCode" in merged_item and "foodName" in merged_item:
                    merged.append(merged_item)
                else:
                    logger.warning(f"丢弃缺少foodCode或foodName的记录: {merged_item}")
        
        return merged
    
    def extract_category_from_filename(self, filename: str) -> str:
        """从文件名中提取食品类别
        
        Args:
            filename: 文件名
            
        Returns:
            食品类别名称
        """
        # 尝试提取类别名称，格式为 "类别-子类"，例如 "鱼虾蟹贝类-鱼1-energy.png" -> "鱼虾蟹贝类-鱼"
        match = re.match(r'(.+?)-\d+(?:-energy|-nutrient)', filename)
        if match:
            return match.group(1)
        
        # 如果上面的模式不匹配，尝试更宽松的模式
        match = re.match(r'(.+?)(?:-energy|-nutrient)', filename)
        if match:
            return match.group(1)
        
        # 如果仍然无法匹配，返回原始文件名（不包括扩展名）
        return Path(filename).stem
    
    def extract_base_category(self, category: str) -> str:
        """提取基础类别名称（去除数字后缀）
        
        Args:
            category: 类别名称，如 "鱼虾蟹贝类-鱼1"
            
        Returns:
            基础类别名称，如 "鱼虾蟹贝类-鱼"
        """
        # 匹配末尾的数字
        match = re.match(r'(.+?)\d+$', category)
        if match:
            return match.group(1)
        return category
    
    async def process_directory(self):
        """处理整个目录中的图像"""
        self.start_time = time.time()
        
        # 收集所有图像文件
        energy_files = {}
        nutrient_files = {}
        
        # 递归查找所有图像文件
        for root, _, files in os.walk(self.input_dir):
            for file in files:
                if file.endswith('-energy.png'):
                    base_name = file.replace('-energy.png', '')
                    energy_files[base_name] = Path(root) / file
                elif file.endswith('-nutrient.png'):
                    base_name = file.replace('-nutrient.png', '')
                    nutrient_files[base_name] = Path(root) / file
        
        # 找到所有匹配的对
        pairs = []
        category_map = {}  # 存储每个base_name对应的类别
        
        for base_name in set(energy_files.keys()) & set(nutrient_files.keys()):
            category = self.extract_category_from_filename(base_name)
            category_map[base_name] = category
            pairs.append((energy_files[base_name], nutrient_files[base_name], base_name))
        
        self.total_images = len(pairs) * 2
        logger.info(f"找到 {len(pairs)} 对图像，共 {self.total_images} 个文件需要处理")
        
        # 统计类别信息
        categories = set(category_map.values())
        # logger.info(f"识别出 {len(categories)} 个食品类别")
        
        # 创建信号量以限制并发任务数
        semaphore = asyncio.Semaphore(self.max_workers)
        
        async def process_with_semaphore(energy_path, nutrient_path, base_name):
            """使用信号量限制并发任务数的图像处理函数"""
            async with semaphore:
                category = category_map[base_name]
                try:
                    return await self.process_image_pair(energy_path, nutrient_path, category)
                except Exception as e:
                    logger.error(f"处理 {base_name} 时发生未处理的异常: {str(e)}")
                    async with self.progress_lock:
                        self.failed_pairs.add(base_name)
                    return None
        
        # 创建所有任务
        tasks = [process_with_semaphore(energy_path, nutrient_path, base_name) 
                for energy_path, nutrient_path, base_name in pairs]
        
        # 并行执行所有任务
        await asyncio.gather(*tasks)
        
        # 处理完成后，保存每个类别的结果
        for category, data in self.results_by_category.items():
            if data:
                output_path = self.temp_middle_dir / f"{category}.json"
                await self.save_json(data, output_path)
                logger.info(f"已将类别 {category} 的数据合并并保存")
        
        # 合并具有相同基础类别的JSON文件并将所有合并文件放到merged目录
        await self.merge_similar_categories()
        
        # 总结处理结果
        total_time = time.time() - self.start_time
        success_count = len(self.successful_pairs)
        fail_count = len(self.failed_pairs)
        
        logger.info(
            f"\n处理完成! 用时 {timedelta(seconds=int(total_time))}\n"
            f"成功处理: {success_count} 对图像\n"
            f"失败处理: {fail_count} 对图像\n"
            f"成功率: {(success_count / (success_count + fail_count) * 100) if (success_count + fail_count) > 0 else 0:.1f}%\n"
            f"平均每对图像处理时间: {(total_time / (success_count + fail_count)) if (success_count + fail_count) > 0 else 0:.2f} 秒"
        )
        
        # 如果有失败的图像对，记录它们
        if self.failed_pairs:
            failed_list_path = self.failed_dir / "failed_pairs.txt"
            async with aiofiles.open(failed_list_path, 'w', encoding='utf-8') as f:
                await f.write("\n".join(sorted(self.failed_pairs)))
            logger.info(f"失败的图像对已记录到 {failed_list_path}")
    
    async def merge_similar_categories(self):
        """合并具有相同基础类别的JSON文件并将所有合并文件放到merged目录"""
        # 创建merged目录（最终结果）
        merged_dir = self.output_dir
        merged_dir.mkdir(exist_ok=True)
        
        # 收集所有已保存的JSON文件（在中间结果文件夹中）
        json_files = list(self.temp_middle_dir.glob("*.json"))
        
        # 按基础类别分组
        category_groups = defaultdict(list)
        standalone_files = []  # 存储没有数字后缀的文件
        
        for json_file in json_files:
            # 跳过已经是合并文件的JSON
            if json_file.stem.startswith("merged_"):
                continue
                
            base_category = self.extract_base_category(json_file.stem)
            
            # 如果基础类别与当前类别相同，说明没有数字后缀，单独处理
            if base_category == json_file.stem:
                standalone_files.append(json_file)
                continue
                
            category_groups[base_category].append(json_file)
        
        # 处理有数字后缀的类别文件
        for base_category, files in category_groups.items():
            if len(files) > 0:
                logger.info(f"合并 {len(files)} 个文件到 merged/merged_{base_category}.json")
                
                # 读取并合并所有数据
                all_data = []
                for file in files:
                    try:
                        async with aiofiles.open(file, 'r', encoding='utf-8') as f:
                            content = await f.read()
                            data = json.loads(content)
                            all_data.extend(data)
                    except Exception as e:
                        logger.error(f"读取 {file} 时出错: {str(e)}")
                
                # 对合并后的数据按foodCode排序
                sorted_data = sort_food_data_by_code(all_data)
                
                # 保存合并后的数据
                if sorted_data:
                    merged_path = merged_dir / f"merged_{base_category}.json"
                    await self.save_json(sorted_data, merged_path)
                    logger.info(f"已将 {len(files)} 个文件合并为 {merged_path} 并按foodCode排序")
        
        # 处理没有数字后缀的独立文件
        for file in standalone_files:
            try:
                async with aiofiles.open(file, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    data = json.loads(content)
                
                # 对数据按foodCode排序
                sorted_data = sort_food_data_by_code(data)
                    
                # 将独立文件也复制到merged目录，添加merged_前缀
                merged_path = merged_dir / f"merged_{file.stem}.json"
                await self.save_json(sorted_data, merged_path)
                logger.info(f"已复制 {file} 到 {merged_path} 并按foodCode排序")
            except Exception as e:
                logger.error(f"处理 {file} 时出错: {str(e)}")
        
        logger.info(f"所有合并后的JSON文件已保存到 {merged_dir} 目录")

async def main():
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="处理食物营养素图片并转换为JSON")
    parser.add_argument("--input", default=os.getenv("INPUT_DIR", "test_images"), help="输入图片目录")
    parser.add_argument("--output", default=os.getenv("OUTPUT_DIR", "_self_json_data"), help="输出JSON目录")
    parser.add_argument("--workers", type=int, default=int(os.getenv("MAX_WORKERS", "3")), help="最大并发工作数")
    parser.add_argument("--api-key", default=os.getenv("API_KEY"), help="API密钥")
    parser.add_argument("--api-base", default=os.getenv("API_BASE"), help="API基础URL")
    parser.add_argument("--retries", type=int, default=int(os.getenv("MAX_RETRIES", "3")), help="最大重试次数")
    parser.add_argument("--model", default=os.getenv("MODEL_NAME"), help="模型名称")
    parser.add_argument("--max-tokens", type=int, default=int(os.getenv("MAX_TOKENS", "4000")), help="最大响应token数")
    parser.add_argument("--temperature", type=float, default=float(os.getenv("TEMPERATURE", "0.1")), help="温度参数")
    parser.add_argument("--sort-only", help="仅对指定目录中的JSON文件进行排序，不进行图片处理")
    args = parser.parse_args()
    
    # 如果只需排序，直接调用排序函数
    if args.sort_only:
        # 由于这是一个独立功能，直接使用同步版本
        sort_json_files_in_directory(args.sort_only)
        return
    
    # 打印参数信息
    logger.info(f"开始处理图像:")
    logger.info(f"输入目录: {args.input}")
    logger.info(f"输出目录: {args.output}")
    logger.info(f"并发工作数: {args.workers}")
    logger.info(f"最大重试次数: {args.retries}")
    logger.info(f"模型名称: {args.model}")
    logger.info(f"最大响应token数: {args.max_tokens}")
    logger.info(f"温度参数: {args.temperature}")
    
    # 创建处理器并运行
    processor = ImageProcessor(
        input_dir=args.input,
        output_dir=args.output,
        max_workers=args.workers,
        api_key=args.api_key,
        api_base=args.api_base,
        max_retries=args.retries,
        model_name=args.model,
        max_tokens=args.max_tokens,
        temperature=args.temperature
    )
    await processor.process_directory()

if __name__ == "__main__":
    asyncio.run(main()) 