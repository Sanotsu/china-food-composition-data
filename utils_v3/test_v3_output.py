#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试脚本：交叉验证"2个模型的API识别原始值 + 手动修正 corrections.json + 最终 merge JSON"

不调用生产主流程（run_merge/run_validation/run_apply），而是用独立重放的
最小实现，从最原始材料逐层推导期望结果，与实际产物对比：

  T0  复用的少数纯工具函数先做 sanity 断言（防止工具本身同错）
  L1  raw/{tag}/{base}-energy|nutrient.json → parsed/{tag}/pairs/{base}.json
      mini 解析 + 配对合并规则复现：逐字段值来源追溯 / foodCode 集合完整性 /
      行数三方核对（重放 vs parse_report vs 人工统计md，md 不存在则跳过）
  L2  parsed/{tag}/pairs/*.json → parsed/{tag}/{category}.json（归类+排序重组对比）
  L3  parsed/{A,B}/{category}.json → consensus/merged_{category}.json
      （共识策略复现：model_b = 整条取B侧，单边code取存在侧）
  L4  consensus + corrections.json → json_data_v3_*/merged_*.json（独立重放修正）
      + apply_log.json 对账
  S1  最终结构：字段序/foodCode格式/排序/无重复/符号合法性/归一化残留
  S2  恒等式与值域独立重算，与 apply_log.json 的 warning 集合对账

三项输入的来源（适用于任何执行者验证自己的流水线产物）：
  --temp          中间目录（raw/{模型tag}/ 识别原始值 + parsed/consensus 中间产物）
  --corrections   手动填写的修正文件
  --output        最终 merge JSON 输出目录
三者均可省略，默认读 config_v3.json（temp_dir/review_dir/模型顺序/共识策略/阈值），
无配置文件时按约定目录约定发现。

覆盖范围说明：本脚本证明"识别之后的处理链路"确定且无错；若两模型对同一
单元格犯了一样的识别错误，属于识别层问题，不在本脚本能力范围内（但恒等式
重算的统计可提供旁证）。

用法:
  .venv/bin/python test_v3_output.py                       # 全默认（读 config_v3.json）
  .venv/bin/python test_v3_output.py \
      --temp temp_v3 --tags "qwen38max,kimi-k3" --policy model_b \
      --corrections review_v3/corrections.json \
      --output json_data_v3_20260825_qwen38max_kimi_k3
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # 项目根（本脚本位于 utils_v3/ 下）
sys.path.insert(0, str(ROOT))

from support_v3.config import (
    DEFAULT_THRESHOLDS, ENERGY_COLS, MERGED_FIELD_ORDER, NUTRIENT_COLS,
    canonicalize_name, extract_base_category, normalize_name,
)
from support_v3.parsing import normalize_cell, sort_by_food_code
from support_v3.recognition import strip_json_fences
from support_v3.validation import parse_manual_counts

# 与生产同源的常量（结构定义，非逻辑）
NUMERIC_FIELDS = [c for c in MERGED_FIELD_ORDER if c not in ("foodCode", "foodName", "remark")]
FOOD_CODE_RE = re.compile(r"^\d{6}[a-zA-Z]?$")
SPECIAL_VALUES = {"Tr", "—", "(0)", "*", "un", "X", ""}
DASH_VARIANTS = {"-", "–", "―", "――", "——", "一"}

# 值域表从 validation 导入（数据常量表须与生产同步——用户会按数据特性放宽 RANGES，
# 硬编码副本会随调整失配导致 S2 误报；S2 的独立性体现在重算逻辑本身）
from support_v3.validation import RANGES  # noqa: E402


def _trunc(x, n=180):
    s = repr(x)
    return s if len(s) <= n else s[:n] + "…"


class Checker:
    """一组断言的收集与汇报"""

    def __init__(self, name):
        self.name = name
        self.checks = 0
        self.fails = []

    def eq(self, actual, expect, msg):
        self.checks += 1
        if actual != expect:
            self.fails.append(f"{msg}\n      期望: {_trunc(expect)}\n      实际: {_trunc(actual)}")

    def ok(self, cond, msg):
        self.checks += 1
        if not cond:
            self.fails.append(msg)

    def report(self):
        status = "PASS" if not self.fails else "FAIL"
        print(f"[{status}] {self.name} — {self.checks} 项断言, {len(self.fails)} 处失败")
        for f in self.fails[:10]:
            print("   ✗ " + f)
        if len(self.fails) > 10:
            print(f"   … 另有 {len(self.fails) - 10} 处失败")
        return not self.fails


# ---------- 独立重放的迷你实现（刻意不复用生产逻辑函数） ----------

def num_parse(raw):
    """独立复刻 validation.parse_num：特殊符号→None，'(0)'→0，忽略尾部*/X标记"""
    s = str(raw or "").strip()
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


def num_key(code):
    m = re.match(r"(\d+)([a-zA-Z]*)", str(code))
    return (int(m.group(1)), m.group(2)) if m else (0, str(code))


def load_rows(raw_path: Path, cols):
    """独立复刻 parse_json 的行提取：json.loads(数值token原文) + 字段过滤 + 归一 + 拒绝空键"""
    text = strip_json_fences(raw_path.read_text(encoding="utf-8"))
    data = json.loads(text, parse_float=str, parse_int=str)
    rows = []
    for item in data.get("rows", []):
        if not isinstance(item, dict):
            continue
        row = {}
        for c in cols:
            v = item.get(c, "")
            row[c] = normalize_cell(v if isinstance(v, str) else ("" if v is None else str(v)))
        if not row.get("foodCode") or not row.get("foodName"):
            continue
        rows.append(row)
    return rows


def diff_records(actual, expect):
    """定位两条记录列表的首个差异，返回可读描述（用于失败信息）"""
    for i, (a, e) in enumerate(zip(actual, expect)):
        if a != e:
            fields = [f for f in MERGED_FIELD_ORDER if a.get(f, "") != e.get(f, "")]
            code = a.get("foodCode", e.get("foodCode", "?"))
            detail = "; ".join(f"{f}: 期望{e.get(f,'')!r} 实际{a.get(f,'')!r}" for f in fields[:5])
            return f"第{i}条({code}) 字段差异: {detail}"
    if len(actual) != len(expect):
        return f"记录数: 期望{len(expect)} 实际{len(actual)}"
    return ""


# ---------- 各检查组 ----------

def check_t0(manual_md: Path):
    c = Checker("T0 基础工具函数 sanity")
    c.eq(normalize_cell("-"), "—", "normalize_cell 横线归一 -")
    c.eq(normalize_cell("–"), "—", "normalize_cell 横线归一 –")
    c.eq(normalize_cell("——"), "—", "normalize_cell 横线归一 ——")
    c.eq(normalize_cell("tr"), "Tr", "normalize_cell tr→Tr")
    c.eq(normalize_cell("TR"), "Tr", "normalize_cell TR→Tr")
    c.eq(normalize_cell(" 2.5 "), "2.5", "normalize_cell 去空白")
    c.eq(normalize_cell(None), "", "normalize_cell None→空串")
    c.eq(canonicalize_name("驴肉(瘦)"), "驴肉（瘦）", "canonicalize_name 半角→全角")
    c.eq(canonicalize_name("牛展, 牛腱子"), "牛展，牛腱子", "canonicalize_name 去空白+逗号全角")
    c.eq(extract_base_category("畜肉类及其制品-牛1"), "畜肉类及其制品-牛", "extract_base_category 尾数字")
    c.eq(extract_base_category("谷类及其制品-小麦2"), "谷类及其制品-小麦", "extract_base_category 尾数字2")
    c.eq(extract_base_category("畜肉类及其制品-驴"), "畜肉类及其制品-驴", "extract_base_category 无尾数字")
    unsorted = [{"foodCode": c} for c in ["2", "10", "1a", "1"]]
    c.eq([r["foodCode"] for r in sort_by_food_code(unsorted)],
         ["1", "1a", "2", "10"], "sort_by_food_code 数值序")
    c.eq(strip_json_fences('{"rows":[]}'), '{"rows":[]}', "strip_json_fences 无fence原样")
    if manual_md.exists():
        counts = parse_manual_counts(manual_md)
        c.ok(len(counts) > 0, "parse_manual_counts 解析出条目")
        c.ok(all(isinstance(v, int) and v > 0 for v in counts.values()),
             "parse_manual_counts 值均为正整数")
    return c


def collect_orphans(temp_dir: Path, tags):
    """raw 两页 JSON 均不存在的单对文件 = 过时残留（生产逻辑不读取，测试同样剔除）"""
    orphans = {}
    for tag in tags:
        pdir = temp_dir / "parsed" / tag / "pairs"
        if not pdir.exists():
            continue
        bad = set()
        for pf in pdir.glob("*.json"):
            e = temp_dir / "raw" / tag / f"{pf.stem}-energy.json"
            n = temp_dir / "raw" / tag / f"{pf.stem}-nutrient.json"
            if not e.exists() and not n.exists():
                bad.add(pf.stem)
        orphans[tag] = bad
        if bad:
            print(f"  （提示）[{tag}] {len(bad)} 个孤儿单对文件（raw 两页均缺失，已剔除）: "
                  f"{sorted(bad)[:3]}{'…' if len(bad) > 3 else ''}")
    return orphans


def check_l1(temp_dir: Path, tags, manual_md: Path, orphans):
    """raw → pairs：逐字段来源追溯 + code 完整性 + 行数三方核对"""
    c = Checker("L1 raw → pairs 单对合并复现（值来源追溯）")
    manual = parse_manual_counts(manual_md) if manual_md.exists() else {}
    total_field_checks = 0
    for tag in tags:
        pdir = temp_dir / "parsed" / tag / "pairs"
        report = json.loads((temp_dir / "parsed" / tag / "parse_report.json").read_text(encoding="utf-8"))
        rows_by_image = report.get("rows_by_image", {})

        for pf in sorted(pdir.glob("*.json")):
            base = pf.stem
            if base in orphans.get(tag, set()):
                continue
            e_json = temp_dir / "raw" / tag / f"{base}-energy.json"
            n_json = temp_dir / "raw" / tag / f"{base}-nutrient.json"
            if not e_json.exists() or not n_json.exists():
                c.ok(False, f"[{tag}] {base} 单页raw缺失 (energy={e_json.exists()}, nutrient={n_json.exists()})")
                continue

            e_rows = load_rows(e_json, ENERGY_COLS)
            n_rows = load_rows(n_json, NUTRIENT_COLS)

            # 行数三方核对：重放 vs parse_report vs 人工统计md
            for jp, rws in ((e_json, e_rows), (n_json, n_rows)):
                img = jp.name[:-5] + ".png"
                c.eq(rows_by_image.get(img), len(rws), f"[{tag}] {img} 行数: 重放 vs parse_report")
                expect_manual = manual.get(normalize_name(img))
                if expect_manual is not None:
                    c.eq(len(rws), expect_manual, f"[{tag}] {img} 行数: 重放 vs 人工统计")

            # 配对合并期望复现
            e = {r["foodCode"]: r for r in e_rows}
            n = {r["foodCode"]: r for r in n_rows}
            recs = json.loads(pf.read_text(encoding="utf-8"))
            rmap = {r["foodCode"]: r for r in recs}
            c.eq(set(rmap), set(e) | set(n), f"[{tag}] {base} foodCode 集合完整性")

            for code in set(e) | set(n):
                rec = rmap.get(code)
                if rec is None:
                    c.ok(False, f"[{tag}] {base} 缺 foodCode={code}")
                    continue
                if code in e and code in n:  # 双页记录
                    for f in ENERGY_COLS:
                        expect = (canonicalize_name(e[code].get("foodName", ""))
                                  if f == "foodName" else e[code].get(f))
                        c.eq(rec.get(f), expect, f"[{tag}] {base} {code}.{f} 应来自energy页")
                        total_field_checks += 1
                    for f in NUTRIENT_COLS:
                        if f in ("foodCode", "foodName"):
                            continue
                        c.eq(rec.get(f), n[code].get(f), f"[{tag}] {base} {code}.{f} 应来自nutrient页")
                        total_field_checks += 1
                    c.eq(rec.get("foodName"), canonicalize_name(e[code].get("foodName", "")),
                         f"[{tag}] {base} {code}.foodName 应为energy侧全角统一")
                elif code in e:  # 仅energy：nutrient字段全空
                    for f in ENERGY_COLS:
                        expect = (canonicalize_name(e[code].get("foodName", ""))
                                  if f == "foodName" else e[code].get(f))
                        c.eq(rec.get(f), expect, f"[{tag}] {base} {code}.{f}（仅energy记录）")
                    for f in NUTRIENT_COLS:
                        if f not in ("foodCode", "foodName"):
                            c.eq(rec.get(f), "", f"[{tag}] {base} {code}.{f} 应为空（仅energy记录）")
                else:  # 仅nutrient：energy字段全空
                    for f in ENERGY_COLS:
                        expect = (n[code].get("foodCode") if f == "foodCode"
                                  else canonicalize_name(n[code].get("foodName", "")) if f == "foodName"
                                  else "")
                        c.eq(rec.get(f), expect, f"[{tag}] {base} {code}.{f}（仅nutrient记录）")
                    for f in NUTRIENT_COLS:
                        if f not in ("foodCode", "foodName"):
                            c.eq(rec.get(f), n[code].get(f), f"[{tag}] {base} {code}.{f}（仅nutrient记录）")
    c.report_extra = f"（追溯约 {total_field_checks} 个字段来源）"
    return c


def check_l2(temp_dir: Path, tags, orphans):
    """pairs → category：按类别重组+排序后与类别文件对比"""
    c = Checker("L2 pairs → 类别文件重组对比")
    for tag in tags:
        expect_by_cat = defaultdict(list)
        for pf in sorted((temp_dir / "parsed" / tag / "pairs").glob("*.json")):
            if pf.stem in orphans.get(tag, set()):
                continue
            expect_by_cat[extract_base_category(pf.stem)].extend(
                json.loads(pf.read_text(encoding="utf-8")))
        for cat, items in sorted(expect_by_cat.items()):
            expect = sort_by_food_code(
                [{f: it.get(f, "") for f in MERGED_FIELD_ORDER} for it in items])
            catf = temp_dir / "parsed" / tag / f"{cat}.json"
            c.ok(catf.exists(), f"[{tag}] 类别文件缺失: {catf.name}")
            if not catf.exists():
                continue
            actual = json.loads(catf.read_text(encoding="utf-8"))
            if actual != expect:
                c.ok(False, f"[{tag}] {cat} 类别文件与单对重组不一致 — {diff_records(actual, expect)}")
            else:
                c.checks += 1
        # 类别文件比重组结果多 = 过时残留（生产不读取，仅提示）
        extra = set(p.stem for p in (temp_dir / "parsed" / tag).glob("*.json")) \
            - set(expect_by_cat) - {"parse_report"}
        if extra:
            print(f"  （提示）[{tag}] {len(extra)} 个过时类别文件（生产不读取）: {sorted(extra)[:3]}")
    return c


def check_l3(temp_dir: Path, tags, policy):
    """category(双模型) → consensus：共识策略复现对比"""
    c = Checker("L3 双模型 → 共识版（策略复现）")
    tag_a, tag_b = tags[0], tags[1]
    pick_first = tag_b if policy == "model_b" else tag_a
    pick_second = tag_a if pick_first == tag_b else tag_b

    files = sorted((temp_dir / "consensus").glob("merged_*.json"))
    c.ok(len(files) > 0, "共识版目录为空")
    for f in files:
        cat = f.stem[len("merged_"):]
        by_tag = {}
        for t in (tag_a, tag_b):
            p = temp_dir / "parsed" / t / f"{cat}.json"
            c.ok(p.exists(), f"[{t}] 缺类别文件: {cat}.json（共识版引用）")
            by_tag[t] = ({it["foodCode"]: it for it in json.loads(p.read_text(encoding="utf-8"))}
                         if p.exists() else {})
        a, b = by_tag[tag_a], by_tag[tag_b]
        expect = []
        for code in sorted(set(a) | set(b), key=num_key):
            rec = by_tag[pick_first].get(code) or by_tag[pick_second].get(code)
            if rec is None:
                c.ok(False, f"{cat} foodCode={code} 在双模型中均不存在（不可能分支）")
                continue
            expect.append({f2: rec.get(f2, "") for f2 in MERGED_FIELD_ORDER})
        actual = json.loads(f.read_text(encoding="utf-8"))
        if actual != expect:
            c.ok(False, f"{cat} 共识版与策略({policy})期望不一致 — {diff_records(actual, expect)}")
        else:
            c.checks += 1
    return c


def check_l4(temp_dir: Path, out_dir: Path, corrections_path: Path):
    """consensus + corrections → 最终输出：独立重放修正对比 + apply_log 对账"""
    c = Checker("L4 共识版 + corrections → 最终输出（独立重放）")
    c.ok(corrections_path.exists(), f"修正文件不存在: {corrections_path}")
    corrections = json.loads(corrections_path.read_text(encoding="utf-8")) \
        if corrections_path.exists() else []
    c.ok(isinstance(corrections, list), "corrections 顶层必须是数组")

    by_cat = defaultdict(list)
    for corr in corrections:
        by_cat[corr.get("category", "")].append(corr)

    cons_files = sorted((temp_dir / "consensus").glob("merged_*.json"))
    final_files = sorted(out_dir.glob("merged_*.json"))
    c.eq([f.name for f in final_files], [f.name for f in cons_files],
         "最终输出文件清单应与共识版一致")

    for cf in cons_files:
        cat = cf.stem[len("merged_"):]
        data = json.loads(cf.read_text(encoding="utf-8"))
        for corr in by_cat.get(cat, []):
            action = corr.get("action", "update")
            code = str(corr.get("foodCode", ""))
            field = corr.get("field", "")
            if action == "insert":
                val = corr.get("value")
                if not isinstance(val, dict):
                    c.ok(False, f"{cat} insert 的 value 必须是完整记录对象")
                    continue
                data.append({f: normalize_cell(str(val.get(f, ""))) for f in MERGED_FIELD_ORDER})
            else:
                idx = next((i for i, it in enumerate(data)
                            if str(it.get("foodCode", "")) == code), None)
                if idx is None:
                    c.ok(False, f"{cat} 修正引用了不存在的 foodCode={code}")
                    continue
                if action == "delete":
                    data.pop(idx)
                elif action == "update":
                    if field not in MERGED_FIELD_ORDER:
                        c.ok(False, f"{cat} {code} 修正引用未知字段 {field}")
                        continue
                    data[idx][field] = normalize_cell(str(corr.get("value", "")))
        expect = sort_by_food_code(data)
        out = out_dir / cf.name
        c.ok(out.exists(), f"最终输出缺文件: {cf.name}")
        if not out.exists():
            continue
        actual = json.loads(out.read_text(encoding="utf-8"))
        if actual != expect:
            c.ok(False, f"{cat} 最终输出与重放期望不一致 — {diff_records(actual, expect)}")
        else:
            c.checks += 1

    # apply_log 对账
    logf = out_dir / "apply_log.json"
    c.ok(logf.exists(), "缺 apply_log.json")
    if logf.exists():
        log = json.loads(logf.read_text(encoding="utf-8"))
        c.eq(log.get("total_corrections"), len(corrections), "apply_log 修正条数对账")
        skipped = [line for line in log.get("log", []) if "[跳过]" in line]
        c.eq(skipped, [], "apply 日志不应有被跳过的修正")

    # corrections 覆盖度：每条 update 目标在共识版中应真实存在（上面已查，此处汇总计数）
    c.report_extra = f"（重放 {len(corrections)} 条修正）"
    return c


def check_s1(out_dir: Path):
    """最终结构：字段/格式/排序/重复/符号/归一化残留"""
    c = Checker("S1 最终输出结构检查")
    total = 0
    for f in sorted(out_dir.glob("merged_*.json")):
        cat = f.stem[len("merged_"):]
        data = json.loads(f.read_text(encoding="utf-8"))
        seen_codes = set()
        prev_key = None
        for rec in data:
            code = rec.get("foodCode", "")
            c.eq(list(rec.keys()), MERGED_FIELD_ORDER, f"{cat} {code} 字段完整性与顺序")
            c.ok(bool(FOOD_CODE_RE.match(code)), f"{cat} foodCode 格式非法: {code!r}")
            c.ok(code not in seen_codes, f"{cat} foodCode 重复: {code}")
            seen_codes.add(code)
            key = num_key(code)
            c.ok(prev_key is None or prev_key <= key, f"{cat} foodCode 排序异常: {code}")
            prev_key = key
            name = rec.get("foodName", "")
            c.ok(canonicalize_name(name) == name,
                 f"{cat} {code} foodName 未全角统一或含空白: {name!r}")
            for fld in NUMERIC_FIELDS:
                v = str(rec.get(fld, ""))
                core = v.strip().rstrip("*Xx")
                ok_val = (core == "" or core in SPECIAL_VALUES
                          or (lambda: (float(core), True)[1] if _is_float(core) else False)())
                c.ok(bool(ok_val), f"{cat} {code}.{fld} 非白名单值: {v!r}")
                residue = (v != v.strip() or v in DASH_VARIANTS
                           or (v.upper() == "TR" and v != "Tr")
                           or any(ch in "０１２３４５６７８９" for ch in v))
                c.ok(not residue, f"{cat} {code}.{fld} 存在未归一残留: {v!r}")
            total += 1
    print(f"  （{len(list(out_dir.glob('merged_*.json')))} 个类别文件 / {total} 条记录）")
    return c


def _is_float(s):
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def check_s2(out_dir: Path, thresholds):
    """恒等式+值域独立重算，与 apply_log 的 warning 集合对账"""
    c = Checker("S2 恒等式/值域独立重算 vs apply_log 对账")
    recount = Counter()
    for f in sorted(out_dir.glob("merged_*.json")):
        cat = f.stem[len("merged_"):]
        for rec in json.loads(f.read_text(encoding="utf-8")):
            code = rec.get("foodCode", "")
            n = {fld: num_parse(rec.get(fld, "")) for fld in NUMERIC_FIELDS}

            def add(type_, field):
                recount[(cat, code, type_, field)] += 1

            for fld, (lo, hi) in RANGES.items():
                v = n.get(fld)
                if v is not None and not (lo <= v <= hi):
                    add("range", fld)

            kcal, kj = n["energyKCal"], n["energyKJ"]
            if kcal is not None and kj is not None and kcal > 0:
                if abs(kj - kcal * 4.184) / (kcal * 4.184) > thresholds["kj_rel_tolerance"]:
                    add("kj_mismatch", "energyKJ")
            p, fat, cho = n["protein"], n["fat"], n["CHO"]
            if None not in (p, fat, cho) and kcal is not None and kcal > 0:
                if abs(4 * p + 9 * fat + 4 * cho - kcal) / kcal > thresholds["atwater_rel_tolerance"]:
                    add("atwater_mismatch", "energyKCal")
            w, ash = n["water"], n["ash"]
            if None not in (w, p, fat, cho, ash):
                if w + p + fat + cho + ash > 100 + thresholds["dry_matter_tolerance"]:
                    add("dry_matter", "water")
            et, e1 = n["vitaminETotal"], n["vitaminE1"]
            if et is not None and e1 is not None:
                s = sum(x for x in (n["vitaminE1"], n["vitaminE2"], n["vitaminE3"]) if x is not None)
                tol = max(thresholds["ve_abs_tolerance"], thresholds["ve_rel_tolerance"] * et)
                if abs(s - et) > tol:
                    add("vitamin_e_mismatch", "vitaminETotal")
            va, car, ret = n["vitaminA"], n["carotene"], n["retinol"]
            if None not in (va, car, ret):
                tol = max(thresholds["vitamin_a_abs_tolerance"],
                          thresholds["vitamin_a_rel_tolerance"] * va)
                if (abs(ret + car / 6 - va) > tol) and (abs(ret + car / 12 - va) > tol):
                    add("vitamin_a_mismatch", "vitaminA")

    logf = out_dir / "apply_log.json"
    log_issues = Counter()
    if logf.exists():
        for iss in json.loads(logf.read_text(encoding="utf-8")).get("remaining_issues", []):
            log_issues[(iss.get("category", ""), iss.get("foodCode", ""),
                        iss.get("type", ""), iss.get("field", ""))] += 1

    only_recount = recount - log_issues
    only_log = log_issues - recount
    if only_recount:
        for k in list(only_recount)[:10]:
            c.ok(False, f"独立重算发现而 apply_log 未记: {k}")
    if only_log:
        for k in list(only_log)[:10]:
            c.ok(False, f"apply_log 记录而独立重算未发现: {k}")
    c.eq(sum(recount.values()), sum(log_issues.values()), "问题总数对账")
    if not only_recount and not only_log:
        c.checks += 1
    print(f"  （重算 warning {sum(recount.values())} 条, apply_log {sum(log_issues.values())} 条,"
          f" 构成 {dict((t, sum(v for k2, v in recount.items() if k2[2] == t)) for t in sorted(set(k[2] for k in recount)))}）")
    return c


def main():
    ap = argparse.ArgumentParser(description="交叉验证 raw识别原始值 + corrections + 最终merge输出")
    ap.add_argument("--temp", default=None,
                    help="中间目录（含 raw/{tag}/ 与 parsed/{tag}/；默认 config_v3.json 的 temp_dir，无配置则为 temp_v3）")
    ap.add_argument("--tags", default=None,
                    help='两个模型tag逗号分隔，顺序=模型A,模型B（默认 config_v3.json models 顺序；'
                         "再缺失则按 raw/ 子目录名自动发现，此时A/B顺序为字典序，"
                         "若与实际流水线不一致 L3 会失败，请显式指定）")
    ap.add_argument("--policy", default=None, choices=("model_a", "model_b"),
                    help="共识策略（默认 config_v3.json 的 consensus_policy，缺省 model_a）")
    ap.add_argument("--output", default=None,
                    help="最终输出目录（默认取最新的含 apply_log.json 的 json_data_v3_*）")
    ap.add_argument("--corrections", default=None,
                    help="corrections.json 路径（默认 config_v3.json 的 review_dir 下 corrections.json）")
    args = ap.parse_args()

    cfg_path = ROOT / "config_v3.json"
    cfg_raw = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}

    temp_dir = Path(args.temp) if args.temp else ROOT / cfg_raw.get("temp_dir", "temp_v3")
    thresholds = dict(DEFAULT_THRESHOLDS)
    thresholds.update(cfg_raw.get("thresholds") or {})
    manual_md = ROOT / cfg_raw.get("manual_counts_md", "docs/手动统计图片数据数量251204.md")
    review_dir = ROOT / cfg_raw.get("review_dir", "review_v3")
    policy = args.policy or cfg_raw.get("consensus_policy", "model_a")

    tags = ([t.strip() for t in args.tags.split(",")] if args.tags
            else [m["tag"] for m in cfg_raw.get("models", [])][:2])
    if len(tags) != 2:
        raw_root = temp_dir / "raw"
        if raw_root.exists():
            tags = sorted(d.name for d in raw_root.iterdir()
                          if d.is_dir() and any(d.glob("*.json")))[:2]
        if len(tags) == 2:
            print(f"（自动发现 raw/ 模型目录: A={tags[0]} B={tags[1]} —— 字典序假设，"
                  f"若与实际流水线不一致 L3 会失败，请用 --tags 显式指定）")
        else:
            print("需要恰好 2 个模型 tag：用 --tags 指定，或在 config_v3.json models 中配置")
            sys.exit(2)

    if args.output:
        out_dir = Path(args.output)
    else:
        # 默认发现顺序：配置的固定 output_dir（已有 apply_log）> 最新的 json_data_v3_*
        out_dir = None
        cfg_out = cfg_raw.get("output_dir")
        if cfg_out:
            p = Path(cfg_out)
            if not p.is_absolute():
                p = ROOT / p
            if (p / "apply_log.json").exists():
                out_dir = p
        if out_dir is None:
            cands = sorted((p for p in ROOT.glob("json_data_v3_*") if (p / "apply_log.json").exists()),
                           key=lambda p: p.stat().st_mtime)
            if not cands:
                print("未找到含 apply_log.json 的 json_data_v3_* 目录，请用 --output 指定")
                sys.exit(2)
            out_dir = cands[-1]
    corrections_path = (Path(args.corrections) if args.corrections
                        else review_dir / "corrections.json")

    def _rel(p: Path) -> str:
        """绝对路径显示为相对项目根的短路径，其余原样"""
        p = Path(p)
        try:
            return str(p.resolve().relative_to(ROOT))
        except ValueError:
            return str(p)

    print(f"模型A={tags[0]} 模型B={tags[1]} 共识策略={policy}")
    print(f"中间目录={_rel(temp_dir)}")
    print(f"最终目录={_rel(out_dir)}")
    print(f"修正文件={_rel(corrections_path)}")
    if not manual_md.exists():
        print(f"（人工统计md不存在，行数核对将跳过: {manual_md}）")
    print()

    orphans = collect_orphans(temp_dir, tags)

    checkers = [
        check_t0(manual_md),
        check_l1(temp_dir, tags, manual_md, orphans),
        check_l2(temp_dir, tags, orphans),
        check_l3(temp_dir, tags, policy),
        check_l4(temp_dir, out_dir, corrections_path),
        check_s1(out_dir),
        check_s2(out_dir, thresholds),
    ]

    print()
    print("=" * 60)
    passed = sum(1 for c in checkers if c.report())
    print(f"结果: {passed}/{len(checkers)} 组通过")
    if passed != len(checkers):
        print("存在失败项——处理链路或数据与期望不一致，请检查上方明细")
        sys.exit(1)
    print("处理链路（识别→解析→合并→共识→修正→输出）复现验证全部通过")


if __name__ == "__main__":
    main()
