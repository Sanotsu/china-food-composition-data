#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""中英文对照表阶段3：自动校验模块

纯本地零API成本，包括：
1. 结构校验：foodCode格式（6位数字可带小写字母后缀）、foodCode全书唯一性
   （同图/跨图重复已在阶段2报告，此处汇总）
2. englishName 规则：含换行/控制字符 -> error（折行拼接失败的信号）；
   含中文字符 -> warning；为空 -> warning
3. 双模型共识比对：逐类别/foodCode/字段比对（3列全部参与），
   生成共识版数据（分歧取 consensus_policy 指定模型值并标记review）
4. foodCode 交叉核对：与营养素成果目录（cross_check_dir）的 merged_*.json
   比对集合差异——EN独有 / EN缺失 逐条列出（按需求仅核对foodCode是否匹配），
   不做 foodName/英文值层面的比对。
   注意：原书两表本就不是一一对应（部分营养素条目无英文名），该差异属预期现象，
   仅作信息输出到独立文件 {temp_dir}/validation/cross_check.json，
   不计入校验问题清单；对照表数据质量以双模型共识准确性为准。

输出：
  {temp_dir}/validation/validation_report.json
  {temp_dir}/validation/cross_check.json
  {temp_dir}/consensus/merged_{category}.json
"""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from support_v3.validation import FOOD_CODE_RE, IssueAccumulator, values_equal
from support_v3_en.config import EN_COLS
from support_v3_en.recognition import collect_recognized_images

# 中文字符（englishName 中出现即报 warning）
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# 控制字符（含换行符，englishName 折行拼接失败的直接信号）
CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def validate_record_en(item: Dict, category: str, model: str,
                       image_rel: str, acc: IssueAccumulator):
    """对单条记录执行对照表字段级校验规则"""
    code = item.get("foodCode", "")
    name = item.get("foodName", "")
    en = str(item.get("englishName", ""))

    if not FOOD_CODE_RE.match(code):
        acc.add("error", "foodcode_format", f"foodCode \"{code}\" 不符合 6位数字(可带字母后缀) 格式",
                model=model, category=category, foodCode=code, foodName=name,
                field="foodCode", value=code, image=image_rel)

    if CTRL_RE.search(en):
        pos = next((m.start() for m in CTRL_RE.finditer(en)), None)
        snippet = en[:80].replace("\n", "\\n").replace("\r", "\\r")
        acc.add("error", "en_control_char",
                f"englishName 含换行/控制字符（首个位置 {pos}），疑似折行拼接失败: \"{snippet}\"",
                model=model, category=category, foodCode=code, foodName=name,
                field="englishName", value=en, image=image_rel)
    if CJK_RE.search(en):
        acc.add("warning", "en_cjk_char",
                f"englishName 含中文字符（疑似误录中文）: \"{en[:80]}\"",
                model=model, category=category, foodCode=code, foodName=name,
                field="englishName", value=en, image=image_rel)
    if en == "":
        acc.add("warning", "en_empty",
                "englishName 为空（请确认原书该条目是否本无英文名）",
                model=model, category=category, foodCode=code, foodName=name,
                field="englishName", value="", image=image_rel)


def _num_key(code: str):
    m = re.match(r"(\d+)([a-zA-Z]*)", code)
    return (int(m.group(1)), m.group(2)) if m else (0, code)


def build_consensus(cfg, per_model: Dict[str, Dict[str, List[Dict]]],
                    acc: IssueAccumulator) -> Dict[str, List[Dict]]:
    """双模型共识合并：字段一致采纳；分歧按 consensus_policy 取值并记review"""
    tag_a, tag_b = cfg.models[0].tag, cfg.models[1].tag
    data_a = per_model.get(tag_a, {})
    data_b = per_model.get(tag_b, {})

    consensus: Dict[str, List[Dict]] = {}

    for category in sorted(set(data_a) | set(data_b)):
        items_a = {it["foodCode"]: it for it in data_a.get(category, [])}
        items_b = {it["foodCode"]: it for it in data_b.get(category, [])}

        for code in sorted(set(items_a) | set(items_b), key=_num_key):
            if code in items_a and code in items_b:
                base = dict(items_a[code])
                # consensus_policy 真正生效：分歧行按策略取值
                if cfg.consensus_policy == "model_b":
                    base = dict(items_b[code])
                for f in EN_COLS:
                    va, vb = items_a[code].get(f, ""), items_b[code].get(f, "")
                    if not values_equal(va, vb):
                        acc.add("review", "model_disagreement",
                                f"两模型字段值不一致（已按策略 {cfg.consensus_policy} 取 "
                                f"{tag_a if cfg.consensus_policy == 'model_a' else tag_b} 的值，请人工裁决）",
                                model="consensus", category=category, foodCode=code,
                                foodName=base.get("foodName", ""), field=f,
                                value=va, value_b=vb)
            elif code in items_a:
                base = dict(items_a[code])
                acc.add("review", "code_missing_in_model_b",
                        f"该foodCode仅在 {tag_a} 中存在，{tag_b} 未识别出（请确认是否漏行）",
                        model="consensus", category=category, foodCode=code,
                        foodName=base.get("foodName", ""))
            else:
                base = dict(items_b[code])
                acc.add("review", "code_missing_in_model_a",
                        f"该foodCode仅在 {tag_b} 中存在，{tag_a} 未识别出（请确认是否漏行）",
                        model="consensus", category=category, foodCode=code,
                        foodName=base.get("foodName", ""))
            consensus.setdefault(category, []).append({f: base.get(f, "") for f in EN_COLS})

    return consensus


def load_nutrition_codes(cross_check_dir: Path) -> Dict[str, Dict]:
    """加载营养素成果目录全部 merged_*.json 的 foodCode -> {foodName, category} 映射"""
    nut: Dict[str, Dict] = {}
    if not cross_check_dir or not cross_check_dir.exists():
        return nut
    for f in sorted(cross_check_dir.glob("merged_*.json")):
        category = f.stem[len("merged_"):]
        try:
            for it in json.loads(f.read_text(encoding="utf-8")):
                code = str(it.get("foodCode", ""))
                if code:
                    nut[code] = {"foodName": it.get("foodName", ""), "category": category}
        except (json.JSONDecodeError, OSError):
            continue
    return nut


def build_cross_check_stats(cross_check_dir: Path,
                            en_map: Dict[str, Dict]) -> Dict:
    """foodCode 交叉核对纯计算：仅比对集合差异，不做名称/英文值比对

    Args:
        cross_check_dir: 营养素成果目录
        en_map: 对照表数据 {foodCode: {"foodName": ..., "category": ...}}
    """
    nut = load_nutrition_codes(cross_check_dir)

    en_only = sorted(set(en_map) - set(nut), key=_num_key)
    missing = sorted(set(nut) - set(en_map), key=_num_key)

    return {
        "cross_check_dir": str(cross_check_dir) if cross_check_dir else "",
        "nutrition_total": len(nut),
        "en_total": len(en_map),
        # 对照表有、营养素成果无（如纯对照表条目）
        "en_only": [{"foodCode": c, **en_map[c]} for c in en_only],
        # 营养素成果有、对照表无（原书本就部分条目无英文名，属预期）
        "missing_en": [{"foodCode": c, **nut[c]} for c in missing],
    }


def write_cross_check_file(cfg, stats: Dict) -> Path:
    """交叉核对结果写入独立文件（信息性输出，不属于校验问题）"""
    cfg.validation_dir.mkdir(parents=True, exist_ok=True)
    out = cfg.validation_dir / "cross_check.json"
    payload = {"generated_at": datetime.now().isoformat(), **stats}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return out


def run_validation(cfg, merge_result: Dict[str, Dict]) -> Dict:
    """执行阶段3校验主流程，生成报告与共识版数据"""
    acc = IssueAccumulator()
    # 阶段3不受 pilot 限制：校验 raw 中已累积识别的全部图片
    images = collect_recognized_images(cfg)
    image_rel_map = {img.base_name: f"{img.rel_dir}/{img.image_path.name}" for img in images}

    for model in cfg.models:
        tag = model.tag
        info = merge_result.get(tag, {})
        report = info.get("report", {})

        # 汇总阶段2的解析错误与 foodCode 重复问题
        for e in report.get("parse_errors", []):
            acc.add(e.get("level", "error"), "parse_" + e.get("reason", "error"),
                    e.get("detail", ""), model=tag, image=e.get("image", ""))
        for iss in report.get("dup_issues", []):
            acc.add(iss.get("level", "error"), iss.get("type", "dup_code"),
                    iss.get("detail", ""), model=tag, category=iss.get("base_name", ""),
                    foodCode=iss.get("foodCode", ""), field="foodCode",
                    image=image_rel_map.get(iss.get("base_name", ""), ""))

        # 字段级规则校验
        code_base_map = _build_code_base_map(cfg, tag)
        for category, items in sorted(info.get("categories", {}).items()):
            for item in items:
                base = code_base_map.get((category, item.get("foodCode", "")), "")
                validate_record_en(item, category, tag,
                                   image_rel_map.get(base, ""), acc)

    # 双模型共识比对与共识版生成
    per_model_categories = {tag: merge_result.get(tag, {}).get("categories", {})
                            for tag in [m.tag for m in cfg.models]}
    consensus = build_consensus(cfg, per_model_categories, acc)

    # foodCode 交叉核对（信息性输出：原书两表本就不是一一对应，仅列出并写独立文件）
    en_map = {it["foodCode"]: {"foodName": it.get("foodName", ""), "category": category}
              for category, items in consensus.items() for it in items}
    cross = build_cross_check_stats(cfg.cross_check_dir, en_map)
    cross_file = write_cross_check_file(cfg, cross)

    cfg.consensus_dir.mkdir(parents=True, exist_ok=True)
    # 清理旧共识文件，避免已删除识别数据的类别残留
    for old in cfg.consensus_dir.glob("merged_*.json"):
        old.unlink()
    for category, items in sorted(consensus.items()):
        out = cfg.consensus_dir / f"merged_{category}.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)

    # 汇总
    summary = {"error": 0, "warning": 0, "review": 0}
    for iss in acc.issues:
        summary[iss["level"]] += 1
    summary["en_total"] = cross["en_total"]
    summary["cross_nutrition_total"] = cross["nutrition_total"]
    summary["cross_en_only"] = len(cross["en_only"])
    summary["cross_missing_en"] = len(cross["missing_en"])
    report = {
        "generated_at": datetime.now().isoformat(),
        "models": [m.tag for m in cfg.models],
        "consensus_policy": cfg.consensus_policy,
        "summary": summary,
        "total_issues": len(acc.issues),
        "cross_check": cross,
        "issues": acc.issues,
    }
    cfg.validation_dir.mkdir(parents=True, exist_ok=True)
    out = cfg.validation_dir / "validation_report.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"校验完成: error={summary['error']} warning={summary['warning']} "
          f"review={summary['review']}（报告: {out}）")
    if cfg.cross_check_dir and cfg.cross_check_dir.exists():
        print(f"foodCode交叉核对（信息性，两表本就不是一一对应）: 对照表 {cross['en_total']} 条 "
              f"vs 营养素成果 {cross['nutrition_total']} 条；EN独有 {len(cross['en_only'])} 条，"
              f"EN缺失 {len(cross['missing_en'])} 条")
        if cross["en_only"]:
            print("  [EN独有]（对照表有、营养素成果无）:")
            for it in cross["en_only"]:
                print(f"    {it['foodCode']} {it['foodName']} [{it['category']}]")
        if cross["missing_en"]:
            print("  [EN缺失]（营养素成果有、对照表无）:")
            for it in cross["missing_en"]:
                print(f"    {it['foodCode']} {it['foodName']} [{it['category']}]")
        print(f"交叉核对明细已输出: {cross_file}")
    else:
        print("未配置有效的 cross_check_dir，跳过 foodCode 交叉核对")
    print(f"共识版数据已生成: {cfg.consensus_dir}/")
    return report


def _build_code_base_map(cfg, tag: str) -> Dict[Tuple[str, str], str]:
    """构建 (category, foodCode) -> base_name 映射（阶段2单图文件保留归属）"""
    cache: Dict[Tuple[str, str], str] = {}
    for img in collect_recognized_images(cfg):
        f = cfg.parsed_dir / tag / "images" / f"{img.base_name}.json"
        if not f.exists():
            continue
        try:
            for it in json.loads(f.read_text(encoding="utf-8")):
                cache.setdefault((img.category, it.get("foodCode", "")), img.base_name)
        except (json.JSONDecodeError, OSError):
            continue
    return cache
