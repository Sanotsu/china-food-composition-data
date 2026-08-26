#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段2：JSON解析与合并模块

对每个模型独立执行：
1. 解析 raw JSON（结构 {"rows": [{字段:值字符串}, ...]}，字段完整性校验）
2. energy + nutrient 按 foodCode 合并为单对JSON
3. 多对按基础类别合并，按 foodCode 排序

输出：
  {temp_dir}/parsed/{model_tag}/{base_name}.json   单对合并结果
  {temp_dir}/parsed/{model_tag}/{category}.json    类别合并结果
  {temp_dir}/parsed/{model_tag}/parse_report.json  解析问题清单
"""
import json
import re
from collections import defaultdict
from typing import Dict, List, Tuple

from .config import (
    ENERGY_COLS, NUTRIENT_COLS, MERGED_FIELD_ORDER, AppConfig,
    canonicalize_name, extract_base_category, names_equivalent, normalize_name,
)
from .recognition import collect_recognized_pairs, strip_json_fences

FOOD_CODE_RE = re.compile(r"^\d{6}[a-zA-Z]?$")


def normalize_cell(v: str) -> str:
    """单元格归一化：去空白、统一特殊符号写法"""
    s = (v or "").strip()
    # 各种横线变体统一为长破折号 —
    if s in {"-", "–", "—", "――", "——", "一"}:
        return "—"
    if s.upper() == "TR":
        return "Tr"
    return s


def _load_json_raw(text: str):
    """解析JSON文本；数值token保留原文（"2.50"不会变成"2.5"），null转空串"""
    return json.loads(
        text,
        parse_float=str,      # 浮点 token 原样字符串，保留小数位数
        parse_int=str,        # 整数 token 原样字符串，保留前导零写法
        parse_constant=lambda c: "" if c == "null" else c,
    )


def parse_json(text: str, expected_cols: List[str],
               image_name: str = "") -> Tuple[List[Dict], List[Dict]]:
    """解析模型返回的 {"rows":[...]} JSON 为行字典列表

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

    expected = set(expected_cols)
    for idx, item in enumerate(data["rows"], start=1):
        if not isinstance(item, dict):
            errors.append({
                "image": image_name, "reason": "row_not_object", "level": "error",
                "detail": f"rows[{idx}] 不是JSON对象: {str(item)[:120]}",
            })
            continue

        unknown = [k for k in item if k not in expected]
        missing = [c for c in expected_cols if c not in item]
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
        for c in expected_cols:
            v = item.get(c, "")
            # 模型违反字符串约定输出数字/布尔时兜底（parse_float/int=str已保留原文）
            row[c] = normalize_cell(v if isinstance(v, str) else ("" if v is None else str(v)))

        if not row.get("foodCode") or not row.get("foodName"):
            errors.append({
                "image": image_name, "reason": "missing_key_field",
                "detail": f"rows[{idx}] foodCode或foodName为空: {json.dumps(item, ensure_ascii=False)[:200]}",
            })
            continue
        rows.append(row)

    return rows, errors


def sort_by_food_code(data: List[Dict]) -> List[Dict]:
    def key(item):
        code = item.get("foodCode", "0")
        m = re.match(r"(\d+)([a-zA-Z]*)", code)
        if m:
            return (int(m.group(1)), m.group(2))
        return (0, code)
    return sorted(data, key=key)


def merge_pair(energy_rows: List[Dict], nutrient_rows: List[Dict],
               base_name: str) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """按 foodCode 合并 energy 与 nutrient 行数据

    Returns:
        (merged, issues, auto_unified)
    """
    issues: List[Dict] = []
    energy_dict = {r["foodCode"]: r for r in energy_rows if r.get("foodCode")}
    nutrient_dict = {r["foodCode"]: r for r in nutrient_rows if r.get("foodCode")}

    e_codes, n_codes = set(energy_dict), set(nutrient_dict)
    for code in sorted(e_codes - n_codes, key=lambda c: (_num_key(c), c)):
        issues.append({"base_name": base_name, "level": "error",
                       "type": "pair_code_only_in_energy", "foodCode": code,
                       "detail": "该foodCode仅在energy识别结果中存在"})
    for code in sorted(n_codes - e_codes, key=lambda c: (_num_key(c), c)):
        issues.append({"base_name": base_name, "level": "error",
                       "type": "pair_code_only_in_nutrient", "foodCode": code,
                       "detail": "该foodCode仅在nutrient识别结果中存在"})

    merged = []
    auto_unified: List[Dict] = []
    for code in sorted(e_codes & n_codes, key=lambda c: (_num_key(c), c)):
        e, n = energy_dict[code], nutrient_dict[code]
        item = {k: e.get(k, "") for k in ENERGY_COLS}
        for k in NUTRIENT_COLS:
            if k in ("foodCode", "foodName"):
                continue
            item[k] = n.get(k, "")

        # foodName：仅全半角标点/空格差异 -> 自动统一为全角，不报问题；真差异才报warning
        name_e, name_n = e.get("foodName", ""), n.get("foodName", "")
        if name_e == name_n:
            item["foodName"] = canonicalize_name(name_e)
        elif names_equivalent(name_e, name_n):
            item["foodName"] = canonicalize_name(name_e)
            auto_unified.append({"foodCode": code, "energy": name_e,
                                 "nutrient": name_n, "unified": item["foodName"]})
        else:
            item["foodName"] = canonicalize_name(name_e)
            issues.append({"base_name": base_name, "level": "warning",
                           "type": "pair_foodname_differs", "foodCode": code,
                           "detail": f"energy侧=\"{name_e}\" nutrient侧=\"{name_n}\"，已采用energy侧"})
        merged.append(item)

    # 单边存在的foodCode也保留进合并结果（半空记录），交由校验/复核裁决
    for code in sorted(e_codes - n_codes, key=lambda c: (_num_key(c), c)):
        e = energy_dict[code]
        item = {k: e.get(k, "") for k in ENERGY_COLS}
        item["foodName"] = canonicalize_name(item["foodName"])
        for k in NUTRIENT_COLS:
            if k not in ("foodCode", "foodName"):
                item[k] = ""
        merged.append(item)
    for code in sorted(n_codes - e_codes, key=lambda c: (_num_key(c), c)):
        n = nutrient_dict[code]
        item = {k: "" for k in ENERGY_COLS}
        item["foodCode"] = n["foodCode"]
        item["foodName"] = canonicalize_name(n.get("foodName", ""))
        for k in NUTRIENT_COLS:
            if k not in ("foodCode", "foodName"):
                item[k] = n.get(k, "")
        merged.append(item)

    return sort_by_food_code(merged), issues, auto_unified


def _num_key(code: str):
    m = re.match(r"(\d+)([a-zA-Z]*)", code)
    return (int(m.group(1)), m.group(2)) if m else (0, code)


def ordered_fields(item: Dict) -> Dict:
    return {k: item.get(k, "") for k in MERGED_FIELD_ORDER}


def run_merge(cfg: AppConfig) -> Dict[str, Dict]:
    """执行阶段2：对每个模型独立解析与合并

    Returns:
        {model_tag: {"categories": {category: [items]}, "report": {...}}}
    """
    # 阶段2起不受 pilot 限制：处理 raw 中已累积识别的全部图片对（分批试点后合并自动包含所有批次）
    pairs = collect_recognized_pairs(cfg)
    if not pairs:
        print("没有找到需要处理的图片对（识别结果均不存在，请先运行 --stage recognize）")
        return {}

    result: Dict[str, Dict] = {}
    for model in cfg.models:
        tag = model.tag
        model_dir = cfg.parsed_dir / tag
        pairs_dir = model_dir / "pairs"
        pairs_dir.mkdir(parents=True, exist_ok=True)

        all_parse_errors: List[Dict] = []
        all_pair_issues: List[Dict] = []
        all_auto_unified: List[Dict] = []
        rows_by_image: Dict[str, int] = {}
        category_data: Dict[str, List[Dict]] = defaultdict(list)

        for pair in pairs:
            e_rows, e_errs = [], []
            n_rows, n_errs = [], []
            e_json = cfg.raw_dir / tag / f"{pair.energy_path.stem}.json"
            n_json = cfg.raw_dir / tag / f"{pair.nutrient_path.stem}.json"
            if e_json.exists():
                e_rows, e_errs = parse_json(e_json.read_text(encoding="utf-8"),
                                            ENERGY_COLS, pair.energy_path.name)
            else:
                e_errs = [{"image": pair.energy_path.name, "reason": "raw_missing",
                           "detail": "阶段1未产出原始JSON"}]
            if n_json.exists():
                n_rows, n_errs = parse_json(n_json.read_text(encoding="utf-8"),
                                            NUTRIENT_COLS, pair.nutrient_path.name)
            else:
                n_errs = [{"image": pair.nutrient_path.name, "reason": "raw_missing",
                           "detail": "阶段1未产出原始JSON"}]

            all_parse_errors.extend(e_errs + n_errs)
            rows_by_image[pair.energy_path.name] = len(e_rows)
            rows_by_image[pair.nutrient_path.name] = len(n_rows)

            merged, issues, unified = merge_pair(e_rows, n_rows, pair.base_name)
            all_pair_issues.extend(issues)
            all_auto_unified.extend(unified)

            # 单对文件放 pairs/ 子目录，避免与类别文件同名冲突
            # （如 "谷类及其制品-小麦.json" 单对 被 "谷类及其制品-小麦"类别[小麦+小麦2合并] 覆盖）
            out = pairs_dir / f"{pair.base_name}.json"
            with open(out, "w", encoding="utf-8") as f:
                json.dump([ordered_fields(m) for m in merged], f, ensure_ascii=False, indent=2)

            category_data[pair.category].extend(merged)

        # 类别级合并保存（单对与类别文件均覆盖写；不预清理——孤儿文件不会被
        # 后续阶段读取，它们以 raw json 存在性为准）
        for category, data in sorted(category_data.items()):
            cat_path = model_dir / f"{category}.json"
            with open(cat_path, "w", encoding="utf-8") as f:
                json.dump([ordered_fields(m) for m in sort_by_food_code(data)],
                          f, ensure_ascii=False, indent=2)

        report = {
            "model_tag": tag,
            "pairs": len(pairs),
            "parse_errors": all_parse_errors,
            "pair_issues": all_pair_issues,
            "foodname_auto_unified": all_auto_unified,
            "rows_by_image": rows_by_image,
        }
        with open(model_dir / "parse_report.json", "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        n_err = len(all_parse_errors)
        n_pair = len(all_pair_issues)
        total_rows = sum(len(v) for v in category_data.values())
        print(f"[{tag}] 解析完成: {total_rows} 条记录，解析问题 {n_err} 项，"
              f"配对问题 {n_pair} 项，名称自动统一(全角) {len(all_auto_unified)} 条"
              f"（详见 parse_report.json）")

        result[tag] = {"categories": dict(category_data), "report": report}

    return result
