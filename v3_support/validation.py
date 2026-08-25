#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段3：自动校验模块

纯本地零API成本，包括：
1. 结构校验：foodCode格式、energy/nutrient配对一致性、行数vs人工统计
2. 恒等式校验：能量换算、Atwater、干物质、维E加和、维A当量
   注意：实测书第6版中维A当量换算系数 /6 与 /12 并存（第一册/第二册
   及不同数据来源不同），故维A规则为任一系数匹配即通过
3. 值域与符号白名单校验
4. 双模型共识比对：逐类别/foodCode/字段比对，数值等价判定（"2.50"≡"2.5"）
5. 生成共识版数据（分歧取 consensus_policy 指定模型值并标记review）

输出：
  {temp_dir}/validation/validation_report.json
  {temp_dir}/consensus/merged_{category}.json
"""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import (
    ENERGY_COLS, NUTRIENT_COLS, MERGED_FIELD_ORDER, AppConfig,
    normalize_for_compare, normalize_name,
)
from .recognition import collect_recognized_pairs

FOOD_CODE_RE = re.compile(r"^\d{6}[a-zA-Z]?$")
SPECIAL_VALUES = {"Tr", "—", "(0)", "*", "un", "X", ""}

# 数值字段（不含foodCode/foodName/remark）
NUMERIC_FIELDS = [c for c in MERGED_FIELD_ORDER if c not in ("foodCode", "foodName", "remark")]

# 字段所属页（用于关联原图）
FIELD_PAGE = {c: "energy" for c in ENERGY_COLS}
FIELD_PAGE.update({c: "nutrient" for c in NUTRIENT_COLS if c not in FIELD_PAGE})

# 宽松值域表（超出即报error，阈值刻意放很宽防误报）
RANGES = {
    "edible": (0, 100), "water": (0, 100),
    "energyKCal": (0, 900), "energyKJ": (0, 3800),
    "protein": (0, 100), "fat": (0, 100), "CHO": (0, 100),
    "dietaryFiber": (0, 90), "cholesterol": (0, 3100), "ash": (0, 40),
    "vitaminA": (0, 30000), "carotene": (0, 60000), "retinol": (0, 30000),
    "thiamin": (0, 20), "riboflavin": (0, 20), "niacin": (0, 150),
    "vitaminC": (0, 700),
    "vitaminETotal": (0, 300), "vitaminE1": (0, 300),
    "vitaminE2": (0, 300), "vitaminE3": (0, 300),
    "Ca": (0, 3000), "P": (0, 5000), "K": (0, 10000), "Na": (0, 12000),
    "Mg": (0, 2000), "Fe": (0, 300), "Zn": (0, 300),
    "Se": (0, 2000), "Cu": (0, 100), "Mn": (0, 100),
}

# 不参与双模型共识比对的字段（备注类文本易有无害差异）
CONSENSUS_SKIP_FIELDS = {"remark"}


def parse_num(raw) -> Optional[float]:
    """解析数值；特殊符号(Tr/—/un等)返回None；'(0)'按0；忽略尾部的*/X标记"""
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "":
        return None
    if s == "(0)":
        return 0.0
    core = s.rstrip("*Xx")
    if core == "":
        return None
    try:
        return float(core)
    except ValueError:
        return None


def is_allowed_value(raw) -> bool:
    """单元格是否为合法值：数值、白名单符号、或其带*/X标记的组合"""
    s = str(raw or "").strip()
    if s == "":
        return True
    core = s.rstrip("*Xx")
    if core == "":
        return True
    if core in SPECIAL_VALUES:
        return True
    return parse_num(core) is not None


def parse_manual_counts(md_path: Path) -> Dict[str, int]:
    """解析手动统计md中的 {文件名(归一化去空格): 数据行数} 映射"""
    counts: Dict[str, int] = {}
    if not md_path.exists():
        return counts
    text = md_path.read_text(encoding="utf-8")
    for m in re.finditer(r"^\|\s*([^|]+?\.png)\s*\|[^|]*\|\s*(\d+)\s*\|\s*$", text, re.M):
        fname, n = m.group(1).strip(), int(m.group(2))
        counts[normalize_name(fname)] = n
    return counts


class IssueAccumulator:
    """收集校验问题并统一编号"""

    def __init__(self):
        self.issues: List[Dict] = []

    def add(self, level: str, type_: str, detail: str, *, model: str = "",
            category: str = "", foodCode: str = "", foodName: str = "",
            field: str = "", value="", value_b="", image: str = "", images=None,
            fields: Optional[Dict] = None):
        self.issues.append({
            "id": len(self.issues) + 1,
            "level": level,      # error / warning / review
            "type": type_,
            "model": model,
            "category": category,
            "foodCode": foodCode,
            "foodName": foodName,
            "field": field,
            "value": value,
            "value_b": value_b,
            "image": image,
            "images": images or ([image] if image else []),
            "fields": fields or {},
            "detail": detail,
        })


def validate_record(item: Dict, category: str, model: str, images_by_page: Dict[str, str],
                    acc: IssueAccumulator, th: Dict[str, float],
                    other_item: Optional[Dict] = None,
                    range_as_warning: bool = False):
    """对单条合并记录执行全部字段级校验规则

    Args:
        other_item: 另一模型同foodCode的记录（存在时，恒等式问题附带双模型字段对照）
        range_as_warning: 值域超标统一按 warning（用于阶段5 apply——共识版已是双模型
            裁决产物或人工修正值，无另一模型可对照，且阶段3已做过降级判断）
    """
    code = item.get("foodCode", "")
    name = item.get("foodName", "")

    def img(field):
        page = FIELD_PAGE.get(field, "energy")
        return images_by_page.get(page, "")

    def flds(*names):
        """收集参与计算的各字段双模型值，用于HTML报告人工核对"""
        return {f: {"a": item.get(f, ""),
                    "b": (other_item or {}).get(f, "")} for f in names}

    if not FOOD_CODE_RE.match(code):
        acc.add("error", "foodcode_format", f"foodCode \"{code}\" 不符合 6位数字(可带字母后缀) 格式",
                model=model, category=category, foodCode=code, foodName=name,
                field="foodCode", value=code, image=img("foodCode"),
                images=list(images_by_page.values()))

    n = {f: parse_num(item.get(f, "")) for f in NUMERIC_FIELDS}

    # 符号白名单
    for f in NUMERIC_FIELDS:
        raw = str(item.get(f, ""))
        if not is_allowed_value(raw):
            acc.add("warning", "unknown_symbol", f"字段 {f} 出现非白名单值 \"{raw}\"",
                    model=model, category=category, foodCode=code, foodName=name,
                    field=f, value=raw, image=img(f))

    # 值域
    for f, (lo, hi) in RANGES.items():
        v = n.get(f)
        if v is not None and not (lo <= v <= hi):
            # apply阶段（range_as_warning）无另一模型可对照，统一warning；
            # 阶段3：双模型同值（如药食同源/脱水食品的书中真实极端值）→ 识别可信，降为warning；
            #        双模型不一致 → 疑似识别错误，保持error
            other_raw = (other_item or {}).get(f, "")
            level = "warning" if (range_as_warning
                                  or (other_item is not None and values_equal(item.get(f, ""), other_raw))) else "error"
            acc.add(level, "range", f"字段 {f} 数值 {v} 超出合理范围 [{lo}, {hi}]"
                    + ("（疑为原书极端值）" if level == "warning" else ""),
                    model=model, category=category, foodCode=code, foodName=name,
                    field=f, value=item.get(f, ""), image=img(f))

    # 规则1: energyKJ ≈ energyKCal * 4.184
    kcal, kj = n["energyKCal"], n["energyKJ"]
    if kcal is not None and kj is not None and kcal > 0:
        expect = kcal * 4.184
        if abs(kj - expect) / expect > th["kj_rel_tolerance"]:
            acc.add("warning", "kj_mismatch",
                    f"energyKJ={kj} 与 energyKCal={kcal}x4.184={expect:.1f} 偏差超过 {th['kj_rel_tolerance']:.0%}",
                    model=model, category=category, foodCode=code, foodName=name,
                    field="energyKJ", value=item.get("energyKJ", ""), image=img("energyKJ"),
                    fields=flds("energyKCal", "energyKJ"))

    # 规则2: Atwater 4p+9f+4c ≈ kcal（代表值/参考值天然有偏差，定warning）
    p, fat, cho = n["protein"], n["fat"], n["CHO"]
    if None not in (p, fat, cho) and kcal is not None and kcal > 0:
        calc = 4 * p + 9 * fat + 4 * cho
        if abs(calc - kcal) / kcal > th["atwater_rel_tolerance"]:
            acc.add("warning", "atwater_mismatch",
                    f"4xprotein+9xfat+4xCHO = {calc:.1f} 与 energyKCal={kcal} 偏差超过 {th['atwater_rel_tolerance']:.0%}",
                    model=model, category=category, foodCode=code, foodName=name,
                    field="energyKCal", value=item.get("energyKCal", ""), image=img("energyKCal"),
                    fields=flds("protein", "fat", "CHO", "energyKCal"))

    # 规则3: 干物质 water+protein+fat+CHO+ash <= 100
    w, ash = n["water"], n["ash"]
    if None not in (w, p, fat, cho, ash):
        total = w + p + fat + cho + ash
        if total > 100 + th["dry_matter_tolerance"]:
            acc.add("warning", "dry_matter",
                    f"water+protein+fat+CHO+ash = {total:.1f} 超过 100+{th['dry_matter_tolerance']}",
                    model=model, category=category, foodCode=code, foodName=name,
                    field="water", value=item.get("water", ""), image=img("water"),
                    fields=flds("water", "protein", "fat", "CHO", "ash"))

    # 规则4: 维E加和 E1+E2+E3 ≈ ETotal（Tr按0；E1为—时跳过）
    et, e1 = n["vitaminETotal"], n["vitaminE1"]
    if et is not None and e1 is not None:
        s = sum(x for x in (n["vitaminE1"], n["vitaminE2"], n["vitaminE3"]) if x is not None)
        tol = max(th["ve_abs_tolerance"], th["ve_rel_tolerance"] * et)
        if abs(s - et) > tol:
            acc.add("warning", "vitamin_e_mismatch",
                    f"E1+E2+E3 = {s:.2f} 与 vitaminETotal={et} 偏差超过 {tol:.2f}",
                    model=model, category=category, foodCode=code, foodName=name,
                    field="vitaminETotal", value=item.get("vitaminETotal", ""), image=img("vitaminETotal"),
                    fields=flds("vitaminETotal", "vitaminE1", "vitaminE2", "vitaminE3"))

    # 规则5: 维A当量 retinol + carotene/6 或 /12 ≈ vitaminA（系数并存，任一匹配即过）
    va, car, ret = n["vitaminA"], n["carotene"], n["retinol"]
    if None not in (va, car, ret):
        ok6 = abs(ret + car / 6 - va) <= max(th["vitamin_a_abs_tolerance"], th["vitamin_a_rel_tolerance"] * va)
        ok12 = abs(ret + car / 12 - va) <= max(th["vitamin_a_abs_tolerance"], th["vitamin_a_rel_tolerance"] * va)
        if not ok6 and not ok12:
            acc.add("warning", "vitamin_a_mismatch",
                    f"retinol+carotene/6={ret + car / 6:.1f}、/12={ret + car / 12:.1f} 均与 vitaminA={va} 偏差超过容差",
                    model=model, category=category, foodCode=code, foodName=name,
                    field="vitaminA", value=item.get("vitaminA", ""), image=img("vitaminA"),
                    fields=flds("vitaminA", "carotene", "retinol"))


def values_equal(a: str, b: str) -> bool:
    """双模型共识判定：归一化相等（含全角/半角标点、内部空格归一）或数值相等（"2.50"≡"2.5"）"""
    sa, sb = str(a or "").strip(), str(b or "").strip()
    if sa == sb:
        return True
    if normalize_for_compare(sa) == normalize_for_compare(sb):
        return True
    fa, fb = parse_num(sa), parse_num(sb)
    if fa is not None and fb is not None:
        return abs(fa - fb) < 1e-9
    return False


def build_consensus(cfg: AppConfig, per_model: Dict[str, Dict[str, List[Dict]]],
                    acc: IssueAccumulator,
                    code_images: Dict[Tuple[str, str], Dict[str, str]]) -> Dict[str, List[Dict]]:
    """双模型共识合并：字段一致采纳；分歧按 consensus_policy 取值并记review"""
    tag_a, tag_b = cfg.models[0].tag, cfg.models[1].tag
    data_a = per_model.get(tag_a, {})
    data_b = per_model.get(tag_b, {})

    consensus: Dict[str, List[Dict]] = {}

    def page_fields_empty(rec: Dict, page: str) -> bool:
        """判断记录在指定页（energy/nutrient）的数值字段是否全部为空（半空记录）"""
        fields = [f for f in MERGED_FIELD_ORDER
                  if FIELD_PAGE.get(f) == page and f not in ("foodCode", "foodName")]
        return all(str(rec.get(f, "")) == "" for f in fields)

    for category in sorted(set(data_a) | set(data_b)):
        items_a = {it["foodCode"]: it for it in data_a.get(category, [])}
        items_b = {it["foodCode"]: it for it in data_b.get(category, [])}

        for code in sorted(set(items_a) | set(items_b), key=lambda c: _num_key(c)):
            imgs_map = code_images.get((category, code), {"energy": "", "nutrient": ""})

            def img(field):
                page = FIELD_PAGE.get(field, "energy")
                return imgs_map.get(page, "")

            if code in items_a and code in items_b:
                base = dict(items_a[code])
                # consensus_policy 真正生效：分歧行按策略取值（model_a=models[0]，model_b=models[1]）
                if cfg.consensus_policy == "model_b":
                    for f in MERGED_FIELD_ORDER:
                        base[f] = items_b[code].get(f, "")
                # 半空记录：某页整体缺失时跳过该页字段级比对（已被配对错误/行数校验覆盖，
                # 避免同一条目级联出十几个"A有值 vs B空"的噪音）
                skip_energy = page_fields_empty(items_a[code], "energy") or page_fields_empty(items_b[code], "energy")
                skip_nutrient = page_fields_empty(items_a[code], "nutrient") or page_fields_empty(items_b[code], "nutrient")
                for f in MERGED_FIELD_ORDER:
                    if f in CONSENSUS_SKIP_FIELDS:
                        continue
                    if FIELD_PAGE.get(f) == "energy" and skip_energy:
                        continue
                    if FIELD_PAGE.get(f) == "nutrient" and skip_nutrient:
                        continue
                    va, vb = items_a[code].get(f, ""), items_b[code].get(f, "")
                    if not values_equal(va, vb):
                        acc.add("review", "model_disagreement",
                                f"两模型字段值不一致（已按策略 {cfg.consensus_policy} 取 "
                                f"{tag_a if cfg.consensus_policy == 'model_a' else tag_b} 的值，请人工裁决）",
                                model="consensus", category=category, foodCode=code,
                                foodName=base.get("foodName", ""), field=f,
                                value=va, value_b=vb, image=img(f))
            elif code in items_a:
                base = dict(items_a[code])
                acc.add("review", "code_missing_in_model_b",
                        f"该foodCode仅在 {tag_a} 中存在，{tag_b} 未识别出（请确认是否漏行）",
                        model="consensus", category=category, foodCode=code,
                        foodName=base.get("foodName", ""), field="", value="", image="",
                        images=list(imgs_map.values()))
            else:
                base = dict(items_b[code])
                acc.add("review", "code_missing_in_model_a",
                        f"该foodCode仅在 {tag_b} 中存在，{tag_a} 未识别出（请确认是否漏行）",
                        model="consensus", category=category, foodCode=code,
                        foodName=base.get("foodName", ""), field="", value="", image="",
                        images=list(imgs_map.values()))
            consensus.setdefault(category, []).append({f: base.get(f, "") for f in MERGED_FIELD_ORDER})

    return consensus


def _num_key(code: str):
    m = re.match(r"(\d+)([a-zA-Z]*)", code)
    return (int(m.group(1)), m.group(2)) if m else (0, code)


def run_validation(cfg: AppConfig, merge_result: Dict[str, Dict]) -> Dict:
    """执行阶段3校验主流程，生成报告与共识版数据"""
    acc = IssueAccumulator()
    # 阶段3不受 pilot 限制：校验 raw 中已累积识别的全部图片对
    pairs = collect_recognized_pairs(cfg)
    manual_counts = parse_manual_counts(cfg.manual_counts_md)

    pair_images: Dict[str, Dict[str, str]] = {}
    for pair in pairs:
        pair_images[pair.base_name] = {
            "energy": f"{pair.rel_dir}/{pair.energy_path.name}",
            "nutrient": f"{pair.rel_dir}/{pair.nutrient_path.name}",
        }

    # 文件名 -> 相对input_dir的完整路径（parse类问题只存了文件名，需拼接子目录前缀用于HTML嵌图）
    image_rel_map: Dict[str, str] = {}
    for pi in pair_images.values():
        image_rel_map[Path(pi["energy"]).name] = pi["energy"]
        image_rel_map[Path(pi["nutrient"]).name] = pi["nutrient"]

    # 文件名 -> base_name（parse类问题定位类别）
    image_base_map: Dict[str, str] = {}
    for pair in pairs:
        image_base_map[pair.energy_path.name] = pair.base_name
        image_base_map[pair.nutrient_path.name] = pair.base_name

    for model in cfg.models:
        tag = model.tag
        info = merge_result.get(tag, {})
        report = info.get("report", {})

        # 汇总阶段2的解析错误与配对问题
        for e in report.get("parse_errors", []):
            img_name = e.get("image", "")
            acc.add(e.get("level", "error"), "parse_" + e.get("reason", "error"),
                    f"{e.get('detail', '')}（第{e.get('line', '?')}行）",
                    model=tag, category=image_base_map.get(img_name, ""),
                    image=image_rel_map.get(img_name, img_name))
        for iss in report.get("pair_issues", []):
            level = "warning" if iss.get("level") == "warning" else "error"
            imgs = []
            bn = iss.get("base_name", "")
            if bn in pair_images:
                imgs = list(pair_images[bn].values())
            acc.add(level, iss.get("type", "pair_issue"), iss.get("detail", ""),
                    model=tag, category=bn, foodCode=iss.get("foodCode", ""),
                    field="foodCode", value="", images=imgs,
                    image=imgs[0] if imgs else "")

        # 行数核对（含图片关联）
        for img_name, rows in report.get("rows_by_image", {}).items():
            expect = manual_counts.get(normalize_name(img_name))
            if expect is None:
                continue
            if rows != expect:
                base = img_name.rsplit("-", 1)[0]
                imgs = pair_images.get(base, {})
                rel = imgs.get("energy" if "energy" in img_name else "nutrient", "")
                acc.add("error", "row_count",
                        f"{img_name} 识别出 {rows} 行，人工统计为 {expect} 行",
                        model=tag, category=base, image=rel)

        # 字段级规则校验（按单对JSON重建 foodCode -> 图片对 映射，精确关联原图）
        other_tags = [m.tag for m in cfg.models if m.tag != tag]
        other_categories = (merge_result.get(other_tags[0], {}).get("categories", {})
                            if other_tags else {})
        for category, items in sorted(info.get("categories", {}).items()):
            code_to_base: Dict[str, str] = {}
            for pair in pairs:
                if pair.category != category:
                    continue
                single = cfg.parsed_dir / tag / "pairs" / f"{pair.base_name}.json"
                if single.exists():
                    try:
                        for it in json.loads(single.read_text(encoding="utf-8")):
                            code_to_base[it.get("foodCode", "")] = pair.base_name
                    except (json.JSONDecodeError, OSError):
                        pass
            other_by_code = {it["foodCode"]: it
                             for it in other_categories.get(category, [])}
            for item in items:
                base = code_to_base.get(item.get("foodCode", ""))
                imgs_map = pair_images.get(base, {"energy": "", "nutrient": ""})
                validate_record(item, category, tag, imgs_map, acc, cfg.thresholds,
                                other_item=other_by_code.get(item.get("foodCode", "")))

    # 双模型共识比对与共识版生成
    per_model_categories = {tag: merge_result.get(tag, {}).get("categories", {})
                            for tag in [m.tag for m in cfg.models]}

    # 构建 (category, foodCode) -> 图片相对路径 映射（用于共识分歧项嵌图）
    code_images: Dict[Tuple[str, str], Dict[str, str]] = {}
    for pair in pairs:
        imgs = pair_images.get(pair.base_name, {"energy": "", "nutrient": ""})
        for tag in per_model_categories:
            single = cfg.parsed_dir / tag / "pairs" / f"{pair.base_name}.json"
            if not single.exists():
                continue
            try:
                for it in json.loads(single.read_text(encoding="utf-8")):
                    code_images.setdefault((pair.category, it.get("foodCode", "")), imgs)
            except (json.JSONDecodeError, OSError):
                pass

    consensus = build_consensus(cfg, per_model_categories, acc, code_images)

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
    # 名称自动统一（仅全半角标点/空格差异，已静默统一为全角，不进问题清单）
    total_unified = sum(len(merge_result.get(m.tag, {}).get("report", {})
                             .get("foodname_auto_unified", []))
                        for m in cfg.models)
    summary["foodname_auto_unified"] = total_unified
    report = {
        "generated_at": datetime.now().isoformat(),
        "models": [m.tag for m in cfg.models],
        "consensus_policy": cfg.consensus_policy,
        "summary": summary,
        "total_issues": len(acc.issues),
        "issues": acc.issues,
    }
    cfg.validation_dir.mkdir(parents=True, exist_ok=True)
    out = cfg.validation_dir / "validation_report.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"校验完成: error={summary['error']} warning={summary['warning']} "
          f"review={summary['review']}（报告: {out}）")
    if total_unified:
        print(f"另有 {total_unified} 条 foodName 仅全半角标点/空格差异，已自动统一为全角（不计入问题）")
    print(f"共识版数据已生成: {cfg.consensus_dir}/")
    return report
