#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""中英文对照表阶段1：单图双模型识别模块

对照表每张截图是独立的单页表格（无 energy/nutrient 配对），
每张图片 x 每个模型调用一次视觉大模型 API（response_format json_object），
输出 JSON 结构化响应 {"rows":[...]} 到
{temp_dir}/raw/{model_tag}/{base_name}.json，并保存调用元信息到 .meta.json。

文件命名约定：base_name = "{父文件夹名}-{文件名主干}"，如
"食物名称中英文对照表截图/畜肉类及其制品/猪1.png" -> "畜肉类及其制品-猪1"，
category = 去尾数字 -> "畜肉类及其制品-猪"（猪1/猪2/猪3 合并到同一类别），
与营养素流水线的类别命名完全对齐。使用 base_name 而非 stem 做文件键，
是因为多个子目录存在同名图片（如 6 个 "其他.png"）。

支持断点续跑：已存在的 json 默认跳过，--force 可强制重跑。
识别循环复用 support_v3.recognition.recognize_one（流式+看门狗+重试）。
"""
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List

from openai import AsyncOpenAI

from support_v3.config import ModelConfig, extract_base_category, normalize_name
from support_v3.recognition import RecognizeResult, recognize_one
from support_v3_en.config import EN_COLS, EnAppConfig


@dataclass
class EnImage:
    base_name: str            # 如 "畜肉类及其制品-猪1"
    category: str             # 如 "畜肉类及其制品-猪"（去尾数字后的基础类别）
    rel_dir: str              # 相对 input_dir 的子目录（用于HTML报告定位图片）
    image_path: Path


def build_en_prompt() -> str:
    """构建对照表识别提示词（配合 response_format json_object 使用）

    重点规则：englishName 过长折行时必须拼接为一行完整字符串。
    """
    fields = ", ".join(EN_COLS)
    sample = json.dumps({"rows": [{c: "" for c in EN_COLS}]}, ensure_ascii=False)
    return f"""你是专业的表格数据录入员。请识别这张《中国食物成分表》食物名称中英文对照表截图中的表格数据，以JSON格式输出。

输出格式：一个JSON Object，形如 {sample}（示例中字段值均为空串，仅示意结构）。
rows数组中每个对象对应表格中的一条数据，字段名固定为：{fields}

输出规则：
1. 必须从表格第一行数据到最后一行，从上到下逐行输出，禁止跳过、合并或遗漏任何一行。
2. 所有字段值一律为字符串，单元格内容原样转录：
   - foodCode 为6位数字，可能带小写字母后缀（如 "031301x"），一律原样保留。
   - foodName 为中文食物名，保留原有的全角括号、方括号等标点（如 "黄豆［大豆］"、"豆腐（代表值）"）。
   - englishName 为英文对照名。特别注意：英文过长时在图中会折行为两行，
     必须把折行内容按顺序拼接为一行完整字符串输出（折行断点处在英文单词之间用一个空格连接），
     禁止丢弃第二行内容，禁止在值中输出换行符。
     例如图中两行 "Soybean milk powder，DUOLI，cow's milk powder，egg" 与 "powder and sugar added，calcium fortified"
     必须输出为一行："Soybean milk powder，DUOLI，cow's milk powder，egg powder and sugar added，calcium fortified"。
   - 英文原样保留大小写、星号标记（如 "*Sour soybean drink*"）与原文标点，不要翻译或改写。
   - 空白单元格输出空字符串 ""。
3. 每行对象必须包含全部 {len(EN_COLS)} 个字段（即使值为空字符串），不得省略任何字段，不得添加额外字段。
4. 只输出JSON本身：不要使用```代码块，不要输出任何解释、标题或其他内容。"""


def _norm_pilot_dir(d: str, input_dir: Path) -> str:
    """试点目录配置归一化：去首尾斜杠/空白，剥离 input_dir 前缀，去内部空白"""
    d = str(d).strip().strip("/").replace("\\", "/")
    in_posix = input_dir.as_posix().strip("/")
    if d.startswith(in_posix + "/"):
        d = d[len(in_posix) + 1:]
    return normalize_name(d)


def collect_en_images(cfg: EnAppConfig, respect_pilot: bool = True) -> List[EnImage]:
    """遍历输入目录收集全部截图（单图模式），应用试点过滤

    试点过滤（pilot.enabled 时，respect_pilot=True）：
    - pilot.base_names：仅处理指定 base_name 的图片
    - pilot.dirs：仅处理指定目录（相对input_dir，也可带input_dir前缀的完整路径）下所有图片
    - 两者并存时取并集
    """
    pilot_norm = {normalize_name(n) for n in cfg.pilot_base_names} if cfg.pilot_enabled else None
    pilot_dirset = {_norm_pilot_dir(d, cfg.input_dir) for d in cfg.pilot_dirs} if cfg.pilot_enabled else None

    images: List[EnImage] = []
    for root, _, files in sorted(os_walk(cfg.input_dir)):
        root_path = Path(root)
        rel_dir = root_path.relative_to(cfg.input_dir).as_posix()
        for fname in sorted(files):
            if not fname.lower().endswith(".png"):
                continue
            stem = fname[: -len(".png")]
            base = f"{rel_dir}-{stem}" if rel_dir != "." else stem
            if respect_pilot and (pilot_norm is not None or pilot_dirset is not None):
                matched = False
                if pilot_norm is not None and normalize_name(base) in pilot_norm:
                    matched = True
                if pilot_dirset is not None and normalize_name(rel_dir) in pilot_dirset:
                    matched = True
                if not matched:
                    continue
            images.append(EnImage(
                base_name=base,
                category=extract_base_category(base),
                rel_dir=rel_dir,
                image_path=root_path / fname,
            ))
    return images


def collect_recognized_images(cfg: EnAppConfig) -> List[EnImage]:
    """收集已识别（所有模型 raw JSON 均存在）的全部图片，不受 pilot 限制

    用于阶段2~5：识别范围由 pilot 控制（分批试点省API），而合并/校验/报告
    始终覆盖 raw 中已累积的全部数据——分次跑不同文件夹，输出自动包含两者并集。
    """
    images = collect_en_images(cfg, respect_pilot=False)
    ready = []
    for img in images:
        if all((cfg.raw_dir / m.tag / f"{img.base_name}.json").exists() for m in cfg.models):
            ready.append(img)
    return ready


def os_walk(input_dir: Path):
    import os
    return list(os.walk(input_dir))


def _make_client(model: ModelConfig):
    from support_v3.recognition import _make_client
    return _make_client(model)


async def run_recognition(cfg: EnAppConfig, force: bool = False) -> List[RecognizeResult]:
    """执行阶段1：对每个模型 x 每张图片执行识别"""
    images = collect_en_images(cfg)
    if not images:
        print("没有找到需要处理的图片（请检查 input_dir / pilot 配置）")
        return []

    total_calls = len(images) * len(cfg.models)
    print(f"找到 {len(images)} 张图片，{len(cfg.models)} 个模型，共 {total_calls} 次API调用")
    if cfg.pilot_enabled:
        parts = []
        if cfg.pilot_base_names:
            parts.append(f"base_names: {', '.join(cfg.pilot_base_names)}")
        if cfg.pilot_dirs:
            parts.append(f"dirs: {', '.join(cfg.pilot_dirs)}")
        print("[试点模式] " + "；".join(parts))

    cfg.raw_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(cfg.max_workers)
    results: List[RecognizeResult] = []
    prompt = build_en_prompt()

    for model in cfg.models:
        client = _make_client(model)
        tasks = []
        for img in images:
            tasks.append(recognize_one(client, model, img.image_path, EN_COLS,
                                       cfg.raw_dir, cfg.max_retries, force, sem,
                                       results, cfg.input_dir,
                                       idle_timeout=cfg.stream_idle_timeout,
                                       ttft_hint=cfg.ttft_thinking_hint,
                                       name_key=img.base_name,
                                       prompt=prompt))
        await asyncio.gather(*tasks)
        await client.close()

    ok = sum(1 for r in results if r.ok)
    skipped = sum(1 for r in results if r.skipped)
    failed = [r for r in results if not r.ok]
    print(f"识别完成: 成功 {ok}（其中断点续跑跳过 {skipped}）/ 共 {len(results)}")
    if failed:
        print(f"失败 {len(failed)} 项:")
        for r in failed:
            print(f"  - [{r.model_tag}] {r.image}: {r.error[:200]}")

    summary_path = cfg.raw_dir / "recognize_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump({
            "total_images": len(images),
            "models": [m.tag for m in cfg.models],
            "results": [r.__dict__ for r in results],
        }, f, ensure_ascii=False, indent=2)
    print(f"识别摘要已保存: {summary_path}")
    return results
