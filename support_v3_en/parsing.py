#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""中英文对照表阶段2：JSON解析与合并模块

对每个模型独立执行：
1. 解析 raw JSON（结构 {"rows": [{foodCode,foodName,englishName}, ...]}，字段完整性校验）
   注意：与营养素流水线不同，单元格不做 "-"/"Tr" 等符号归一——
   englishName 是英文文本，连字符等标点必须原样保留。
2. foodCode 字母后缀统一为小写（书内惯例如 "031301x"），大小写差异不构成分歧
3. foodCode 去重（同图内重复/跨图重复均报问题，保留首条）
4. 图片行按原顺序存单图文件（复核镜像需要与图片行序一致）；
   类别级合并按 foodCode 排序（猪1/猪2/猪3 -> 同一类别文件）

输出：
  {temp_dir}/parsed/{model_tag}/images/{base_name}.json   单图结果（原行序）
  {temp_dir}/parsed/{model_tag}/{category}.json           类别合并结果（code升序）
  {temp_dir}/parsed/{model_tag}/parse_report.json         解析问题清单
"""
import json
import re
from collections import defaultdict
from typing import Dict, List, Tuple

from support_v3.recognition import strip_json_fences
from support_v3_en.config import EN_COLS
from support_v3_en.recognition import collect_recognized_images

FOOD_CODE_RE = re.compile(r"^\d{6}[a-zA-Z]?$")


def _load_json_raw(text: str):
    """解析JSON文本；数值token保留原文，null转空串"""
    return json.loads(
        text,
        parse_float=str,
        parse_int=str,
        parse_constant=lambda c: "" if c == "null" else c,
    )


def parse_json_en(text: str, image_name: str = "") -> Tuple[List[Dict], List[Dict]]:
    """解析模型返回的 {"rows":[...]} JSON 为行字典列表（3列，原样转录）

    与营养素 parse_json 的差异：
    - 单元格仅去首尾空白，不做符号归一（保护英文连字符/撇号等标点）
    - foodCode 字母后缀自动转小写（书内惯例，静默统一不计问题）

    Returns:
        (rows, errors)：rows为解析成功的数据行；errors为问题清单
    """
    errors: List[Dict] = []
    rows: List[Dict] = []

    raw = strip_json_fences(text)
    try:
        data = _load_json_raw(raw)
    except json.JSONDecodeError as e:
        errors.append({
            "image": image_name, "reason": "invalid_json",
            "detail": f"JSON解析失败: {e}（响应可能被截断或夹带非JSON内容）",
        })
        return rows, errors

    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        errors.append({
            "image": image_name, "reason": "rows_missing",
            "detail": '顶层结构必须是 {"rows": [...]}（JSON Object 含 rows 数组）',
        })
        return rows, errors

    expected = set(EN_COLS)
    for idx, item in enumerate(data["rows"], start=1):
        if not isinstance(item, dict):
            errors.append({
                "image": image_name, "reason": "row_not_object", "level": "error",
                "detail": f"rows[{idx}] 不是JSON对象: {str(item)[:120]}",
            })
            continue

        unknown = [k for k in item if k not in expected]
        missing = [c for c in EN_COLS if c not in item]
        if unknown:
            errors.append({
                "image": image_name, "reason": "unknown_field", "level": "warning",
                "detail": f"rows[{idx}] 含未知字段 {unknown}（已忽略）",
            })
        if missing:
            errors.append({
                "image": image_name, "reason": "missing_field_padded", "level": "warning",
                "detail": f"rows[{idx}] 缺字段 {missing}，已自动补空，请复核该行",
            })

        row = {}
        for c in EN_COLS:
            v = item.get(c, "")
            # 模型违反字符串约定输出数字/布尔时兜底（parse_float/int=str已保留原文）
            row[c] = (v if isinstance(v, str) else ("" if v is None else str(v))).strip()

        # foodCode：去内部空白；字母后缀统一小写（如 "031301X" -> "031301x"）
        code = row.get("foodCode", "").replace(" ", "")
        if re.match(r"^\d{6}[A-Z]$", code):
            code = code.lower()
        row["foodCode"] = code

        if not row.get("foodCode") or not row.get("foodName"):
            errors.append({
                "image": image_name, "reason": "missing_key_field",
                "detail": f"rows[{idx}] foodCode或foodName为空: {json.dumps(item, ensure_ascii=False)[:200]}",
            })
            continue
        rows.append(row)

    return rows, errors


def sort_by_food_code(data: List[Dict]) -> List[Dict]:
    from support_v3.parsing import sort_by_food_code as _srt
    return _srt(data)


def run_merge(cfg) -> Dict[str, Dict]:
    """执行阶段2：对每个模型独立解析与合并

    Returns:
        {model_tag: {"categories": {category: [items]}, "report": {...}}}
    """
    # 阶段2起不受 pilot 限制：处理 raw 中已累积识别的全部图片
    images = collect_recognized_images(cfg)
    if not images:
        print("没有找到需要处理的图片（识别结果均不存在，请先运行 --stage recognize）")
        return {}

    result: Dict[str, Dict] = {}
    for model in cfg.models:
        tag = model.tag
        model_dir = cfg.parsed_dir / tag
        images_dir = model_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        all_parse_errors: List[Dict] = []
        all_dup_issues: List[Dict] = []
        rows_by_image: Dict[str, int] = {}
        category_data: Dict[str, List[Dict]] = defaultdict(list)
        seen_codes: Dict[str, str] = {}   # foodCode -> 首次出现的 base_name（全书唯一）

        for img in images:
            raw_path = cfg.raw_dir / tag / f"{img.base_name}.json"
            if raw_path.exists():
                rows, errs = parse_json_en(raw_path.read_text(encoding="utf-8"),
                                           f"{img.rel_dir}/{img.image_path.name}")
            else:
                rows, errs = [], [{"image": f"{img.rel_dir}/{img.image_path.name}",
                                   "reason": "raw_missing",
                                   "detail": "阶段1未产出原始JSON"}]
            all_parse_errors.extend(errs)
            rows_by_image[img.base_name] = len(rows)

            kept: List[Dict] = []
            local_seen = set()
            for r in rows:
                code = r["foodCode"]
                if code in local_seen:
                    all_dup_issues.append({
                        "base_name": img.base_name, "level": "error",
                        "type": "dup_code_in_image", "foodCode": code,
                        "detail": f"同一图片内 foodCode {code} 重复出现，已保留首条",
                    })
                    continue
                if code in seen_codes:
                    all_dup_issues.append({
                        "base_name": img.base_name, "level": "error",
                        "type": "dup_code_across_images", "foodCode": code,
                        "detail": f"foodCode {code} 与图片 {seen_codes[code]} 重复（疑似边界重识别或误读），已保留首条",
                    })
                    continue
                local_seen.add(code)
                seen_codes[code] = img.base_name
                kept.append(r)

            # 单图文件保持模型输出行序（=图片行序），供复核镜像对照
            with open(images_dir / f"{img.base_name}.json", "w", encoding="utf-8") as f:
                json.dump(kept, f, ensure_ascii=False, indent=2)

            category_data[img.category].extend(kept)

        for category, data in sorted(category_data.items()):
            cat_path = model_dir / f"{category}.json"
            with open(cat_path, "w", encoding="utf-8") as f:
                json.dump(sort_by_food_code(data), f, ensure_ascii=False, indent=2)

        report = {
            "model_tag": tag,
            "images": len(images),
            "parse_errors": all_parse_errors,
            "dup_issues": all_dup_issues,
            "rows_by_image": rows_by_image,
        }
        with open(model_dir / "parse_report.json", "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        n_err = len(all_parse_errors)
        n_dup = len(all_dup_issues)
        total_rows = sum(len(v) for v in category_data.values())
        print(f"[{tag}] 解析完成: {total_rows} 条记录（{len(category_data)} 个类别），"
              f"解析问题 {n_err} 项，foodCode重复 {n_dup} 项（详见 parse_report.json）")

        result[tag] = {"categories": dict(category_data), "report": report}

    return result
