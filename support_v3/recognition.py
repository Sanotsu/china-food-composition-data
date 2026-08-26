#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段1：双模型识别模块

每张图片 x 每个模型调用一次视觉大模型 API（response_format json_object），
输出 JSON 结构化响应 {"rows":[...]} 到
{temp_dir}/raw/{model_tag}/{图片名}.json，并保存调用元信息到 .meta.json。

支持断点续跑：已存在的 json 默认跳过，--force 可强制重跑。
"""
import asyncio
import base64
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from .config import ENERGY_COLS, NUTRIENT_COLS, AppConfig, ModelConfig, normalize_name


@dataclass
class ImagePair:
    base_name: str            # 如 "畜肉类及其制品-驴"
    category: str             # 如 "畜肉类及其制品-驴"（去尾数字后的基础类别）
    rel_dir: str              # 相对 input_dir 的子目录（用于HTML报告定位图片）
    energy_path: Path
    nutrient_path: Path


@dataclass
class RecognizeResult:
    image: str
    model_tag: str
    ok: bool
    skipped: bool = False
    retries: int = 0
    finish_reason: str = ""
    elapsed: float = 0.0
    error: str = ""
    rows_hint: int = 0


def build_json_prompt(cols: List[str]) -> str:
    """构建JSON结构化输出提示词（配合 response_format json_object 使用）"""
    fields = ", ".join(cols)
    sample = json.dumps({"rows": [{c: "" for c in cols}]}, ensure_ascii=False)
    return f"""你是专业的表格数据录入员。请识别这张《中国食物成分表》截图中的表格数据，以JSON格式输出。

输出格式：一个JSON Object，形如 {sample}（示例中字段值均为空串，仅示意结构）。
rows数组中每个对象对应表格中的一条数据，字段名固定为：{fields}

输出规则：
1. 必须从表格第一行数据到最后一行，从上到下逐行输出，禁止跳过、合并或遗漏任何一行。
2. 所有字段值一律为字符串，单元格内容原样转录：
   - 数值保留原有小数位数（如 "2.50" 不要写成 "2.5"，"0" 不要写成 "0.0"）。
   - 特殊符号原样输出："Tr"（微量/未检出）、"—"（未检测）、"(0)"（估计0值）、"un"（不能计算）。
   - 数值或编码后可能带 "*"（参考值标记）、"X"（代表值标记）或小写字母后缀（如 "091101x"），一律原样保留。
   - 空白单元格输出空字符串 ""。
3. 每行对象必须包含全部 {len(cols)} 个字段（即使值为空字符串），不得省略任何字段，不得添加额外字段。
4. 只输出JSON本身：不要使用```代码块，不要输出任何解释、标题或其他内容。"""


def _norm_pilot_dir(d: str, input_dir: Path) -> str:
    """试点目录配置归一化：去首尾斜杠/空白，剥离 input_dir 前缀，去内部空白

    支持三种写法等价："/path/项目/营养素截图/蛋类及其制品/"、"营养素截图/蛋类及其制品"、"蛋类及其制品"
    """
    d = str(d).strip().strip("/").replace("\\", "/")
    in_posix = input_dir.as_posix().strip("/")
    if d.startswith(in_posix + "/"):
        d = d[len(in_posix) + 1:]
    return normalize_name(d)


def collect_image_pairs(cfg: AppConfig, respect_pilot: bool = True) -> List[ImagePair]:
    """遍历输入目录收集 energy/nutrient 图片对，应用忽略前缀与试点过滤

    试点过滤（pilot.enabled 时，respect_pilot=True）：
    - pilot.base_names：仅处理指定基础名的图片对
    - pilot.dirs：仅处理指定目录（相对input_dir，也可带input_dir前缀的完整路径）下所有图片对
    - 两者并存时取并集
    """
    from .config import extract_base_category

    pilot_norm = {normalize_name(n) for n in cfg.pilot_base_names} if cfg.pilot_enabled else None
    pilot_dirset = {_norm_pilot_dir(d, cfg.input_dir) for d in cfg.pilot_dirs} if cfg.pilot_enabled else None

    energy_files: Dict[str, Tuple[Path, str]] = {}
    nutrient_files: Dict[str, Tuple[Path, str]] = {}

    for root, _, files in os_walk(cfg.input_dir):
        root_path = Path(root)
        rel_dir = root_path.relative_to(cfg.input_dir).as_posix()
        if any(p.lower() in root_path.as_posix().lower() for p in cfg.ignore_prefixes):
            continue
        for fname in sorted(files):
            if any(p.lower() in fname.lower() for p in cfg.ignore_prefixes):
                continue
            if fname.endswith("-energy.png"):
                base = fname[: -len("-energy.png")]
                energy_files[base] = (root_path / fname, rel_dir)
            elif fname.endswith("-nutrient.png"):
                base = fname[: -len("-nutrient.png")]
                nutrient_files[base] = (root_path / fname, rel_dir)

    pairs = []
    for base in sorted(set(energy_files) & set(nutrient_files)):
        if respect_pilot and (pilot_norm is not None or pilot_dirset is not None):
            e_path, rel_dir = energy_files[base]
            matched = False
            if pilot_norm is not None and normalize_name(base) in pilot_norm:
                matched = True
            if pilot_dirset is not None and normalize_name(rel_dir) in pilot_dirset:
                matched = True
            if not matched:
                continue
        e_path, rel_dir = energy_files[base]
        n_path, _ = nutrient_files[base]
        pairs.append(ImagePair(
            base_name=base,
            category=extract_base_category(base),
            rel_dir=rel_dir,
            energy_path=e_path,
            nutrient_path=n_path,
        ))
    return pairs


def _hint_row_count(text: str) -> int:
    """从响应文本估算行数（优先解析JSON的rows长度，失败回退数大括号出现次数）"""
    try:
        data = json.loads(strip_json_fences(text))
        rows = data.get("rows") if isinstance(data, dict) else None
        if isinstance(rows, list):
            return len(rows)
    except Exception:  # noqa: BLE001
        pass
    return text.count('"foodCode"')


def strip_json_fences(text: str) -> str:
    """剥离可能存在的```json代码块包裹与前后杂散文本"""
    m = re.search(r"```(?:json|JSON)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # 无代码块时：截取首个 { 到最后一个 } 之间的内容
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e > s:
        return text[s:e + 1]
    return text.strip()


def collect_recognized_pairs(cfg: AppConfig) -> List[ImagePair]:
    """收集已识别（所有模型 raw JSON 均存在）的全部图片对，不受 pilot 限制

    用于阶段2~5：识别范围由 pilot 控制（分批试点省API），而合并/校验/报告
    始终覆盖 raw 中已累积的全部数据——分次跑不同文件夹，输出自动包含两者并集。
    """
    pairs = collect_image_pairs(cfg, respect_pilot=False)
    ready = []
    for pair in pairs:
        ok = all((cfg.raw_dir / m.tag / f"{pair.energy_path.stem}.json").exists()
                 and (cfg.raw_dir / m.tag / f"{pair.nutrient_path.stem}.json").exists()
                 for m in cfg.models)
        if ok:
            ready.append(pair)
    return ready


def os_walk(input_dir: Path):
    import os
    return list(os.walk(input_dir))


def _make_client(model: ModelConfig) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=model.api_key,
        base_url=model.api_base,
        timeout=model.timeout,
        max_retries=0,  # 重试由本模块自行控制（含指数退避）
    )


async def recognize_one(
    client: AsyncOpenAI,
    model: ModelConfig,
    image_path: Path,
    is_energy: bool,
    raw_dir: Path,
    max_retries: int,
    force: bool,
    sem: asyncio.Semaphore,
    result_sink: List[RecognizeResult],
    images_rel_root: Path,
    idle_timeout: float = 120.0,
    ttft_hint: float = 15.0,
) -> Optional[Path]:
    """识别单张图片，流式接收+看门狗+过程状态打印，带重试与断点续跑

    Returns:
        保存的json路径（失败返回None）
    """
    cols = ENERGY_COLS if is_energy else NUTRIENT_COLS
    out_path = raw_dir / model.tag / f"{image_path.stem}.json"
    meta_path = raw_dir / model.tag / f"{image_path.stem}.meta.json"

    if out_path.exists() and not force:
        result_sink.append(RecognizeResult(
            image=image_path.name, model_tag=model.tag, ok=True, skipped=True))
        return out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    prompt = build_json_prompt(cols)
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")

    retries = 0
    last_err = ""
    async with sem:
        while retries <= max_retries:
            if retries > 0:
                wait = min(5 ** retries, 60)
                print(f"  [{model.tag}] {image_path.name}: 第{retries}次重试（等{wait}s）。上次错误: {last_err[:150]}")
                await asyncio.sleep(wait)
            start = time.time()
            print(f"  [{model.tag}] {image_path.name}: 请求发出（尝试 {retries + 1}/{max_retries + 1}）...")
            try:
                req_kwargs = dict(
                    model=model.model_name,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url",
                             "image_url": {"url": f"data:image/png;base64,{b64}"}},
                        ],
                    }],
                    max_tokens=model.max_tokens,
                    extra_body=model.extra_payload or None,
                    stream=True,
                )
                if model.temperature is not None:
                    req_kwargs["temperature"] = model.temperature
                if model.response_format:
                    req_kwargs["response_format"] = model.response_format
                stream = await client.chat.completions.create(**req_kwargs)

                parts: List[str] = []
                finish_reason = ""
                chunk_count = 0
                ttft = None
                chars = 0
                it = stream.__aiter__()
                while True:
                    try:
                        chunk = await asyncio.wait_for(it.__anext__(), timeout=idle_timeout)
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        elapsed = time.time() - start
                        raise RuntimeError(
                            f"流式响应停滞 {idle_timeout:.0f}s 无新数据（已产出 {chars} 字符，"
                            f"总耗时 {elapsed:.0f}s）。服务端排队或异常。")
                    chunk_count += 1
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = getattr(choice, "delta", None)
                    if delta is not None and delta.content:
                        if ttft is None:
                            ttft = time.time() - start
                            hint = ""
                            if ttft > ttft_hint:
                                hint = f"（> {ttft_hint:.0f}s 才出首字节，模型可能在思考模式中运行，" \
                                       f"请检查 extra_payload 关闭思考的参数是否对该平台生效）"
                            print(f"  [{model.tag}] {image_path.name}: 首字节 {ttft:.1f}s{hint}")
                        parts.append(delta.content)
                        chars += len(delta.content)
                    fr = getattr(choice, "finish_reason", None)
                    if fr:
                        finish_reason = fr
                text = "".join(parts)
                elapsed = round(time.time() - start, 2)

                if finish_reason == "length":
                    raise RuntimeError(f"响应被截断(finish_reason=length)，max_tokens={model.max_tokens}")
                if not text.strip():
                    raise RuntimeError("模型返回了空内容")

                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(text.strip() + "\n")
                meta = {
                    "model": model.model_name,
                    "finish_reason": finish_reason,
                    "retries": retries,
                    "elapsed_sec": elapsed,
                    "ttft_sec": round(ttft, 2) if ttft is not None else None,
                    "stream_chunks": chunk_count,
                    "chars": len(text),
                    "usage": None,
                }
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)

                rows_hint = _hint_row_count(text)
                print(f"  [{model.tag}] {image_path.name}: 完成，{elapsed:.1f}s，"
                      f"{len(text)}字符，约{rows_hint}行")
                result_sink.append(RecognizeResult(
                    image=image_path.name, model_tag=model.tag, ok=True,
                    retries=retries, finish_reason=finish_reason,
                    elapsed=elapsed, rows_hint=rows_hint))
                return out_path

            except Exception as e:  # noqa: BLE001
                retries += 1
                last_err = str(e)
                # 4xx 客户端错误（参数/鉴权/模型名问题）是确定性失败，重试无意义，直接放弃
                status = getattr(e, "status_code", None)
                if status is not None and 400 <= status < 500 and status != 429:
                    print(f"  [{model.tag}] {image_path.name}: 客户端错误(HTTP {status})，不重试直接失败")
                    break

    result_sink.append(RecognizeResult(
        image=image_path.name, model_tag=model.tag, ok=False,
        retries=retries, error=last_err))
    if meta_path.exists():
        meta_path.unlink(missing_ok=True)
    return None


async def run_recognition(cfg: AppConfig, force: bool = False) -> List[RecognizeResult]:
    """执行阶段1：对每个模型 x 每张图片执行识别"""
    pairs = collect_image_pairs(cfg)
    if not pairs:
        print("没有找到需要处理的图片对（请检查 input_dir / ignore_prefixes / pilot 配置）")
        return []

    total_calls = len(pairs) * 2 * len(cfg.models)
    print(f"找到 {len(pairs)} 对图片，{len(cfg.models)} 个模型，共 {total_calls} 次API调用")
    if cfg.pilot_enabled:
        parts = []
        if cfg.pilot_base_names:
            parts.append(f"base_names: {', '.join(cfg.pilot_base_names)}")
        if cfg.pilot_dirs:
            parts.append(f"dirs: {', '.join(cfg.pilot_dirs)}")
        print(f"[试点模式] " + "；".join(parts))

    cfg.raw_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(cfg.max_workers)
    results: List[RecognizeResult] = []

    for model in cfg.models:
        client = _make_client(model)
        tasks = []
        for pair in pairs:
            tasks.append(recognize_one(client, model, pair.energy_path, True,
                                       cfg.raw_dir, cfg.max_retries, force, sem,
                                       results, cfg.input_dir,
                                       idle_timeout=cfg.stream_idle_timeout,
                                       ttft_hint=cfg.ttft_thinking_hint))
            tasks.append(recognize_one(client, model, pair.nutrient_path, False,
                                       cfg.raw_dir, cfg.max_retries, force, sem,
                                       results, cfg.input_dir,
                                       idle_timeout=cfg.stream_idle_timeout,
                                       ttft_hint=cfg.ttft_thinking_hint))
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
            "total_pairs": len(pairs),
            "models": [m.tag for m in cfg.models],
            "results": [r.__dict__ for r in results],
        }, f, ensure_ascii=False, indent=2)
    print(f"识别摘要已保存: {summary_path}")
    return results
