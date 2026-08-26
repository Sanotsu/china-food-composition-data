#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""能量列互换矫正：修复原书 energyKCal/energyKJ 整列印刷互换的数据

背景：《中国食物成分表》第6版 蔬菜类及其制品-野生蔬菜类（048004~048084 连续行块）
原书两列数值互换（如 048004 白沙蒿 kcal=232/kJ=56，互换后 56kcal=234kJ 才自洽）。

原则：识别版 json_data_v3_* 与原书 1:1 忠实一致（错也错一样）；
     本脚本生成平行矫正版 {output_dir}_fixed/，只交换两字段字符串，其余逐字节等价。

判定规则：
  1) 比值法：kJ/kcal∈[3.5,4.9] 正常；kcal/kJ∈[3.5,4.9] 互换（4.184 换算的宽容差）
  2) kcal<10 的小值改用绝对误差 |kJ-4.184*kcal|<=2.5（防取整误报，如 044501 薤 1/3）
  3) 仅当互换行按 foodCode 形成连续块(>=MIN_BLOCK)才自动修——零星孤立互换只报告
     （那是识别错位的特征，不是原书印刷错误的特征）

用法：
  python utils_v3/fix_energy_swap.py            # dry-run：仅报告
  python utils_v3/fix_energy_swap.py --apply    # 生成/重建 {output_dir}_fixed/
"""
import argparse
import json
import shutil
from pathlib import Path

RATIO_LO, RATIO_HI = 3.5, 4.9          # kJ/kcal 正常比值区间
SMALL_KCAL = 10.0                       # 小值改绝对误差判定
SMALL_ABS_TOL = 2.5
MIN_BLOCK = 5                           # 连续互换块的最小行数（自动修的门槛）
KCAL, KJ = "energyKCal", "energyKJ"


def fnum(v):
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def classify(rec):
    """返回 'normal' | 'swap' | 'other' | 'nonnum' | None(字段缺失视为 nonnum)"""
    kcal, kJ = fnum(rec.get(KCAL)), fnum(rec.get(KJ))
    if kcal is None or kJ is None:
        return "nonnum"
    in_tol = lambda x: RATIO_LO <= x <= RATIO_HI
    rn, rs = kJ / kcal, kcal / kJ
    if kcal < SMALL_KCAL or kJ < SMALL_KCAL:
        # 小值双方向绝对误差判定（正向正常 / 反向互换）
        if abs(kJ - 4.184 * kcal) <= SMALL_ABS_TOL:
            return "normal"
        if abs(kcal - 4.184 * kJ) <= SMALL_ABS_TOL:
            return "swap"
        return "other"
    if in_tol(rn) and not in_tol(rs):
        return "normal"
    if in_tol(rs) and not in_tol(rn):
        return "swap"
    return "other"


def find_swap_blocks(records):
    """在单品类记录中找可自动修的连续互换块

    连续性按记录在文件中的位置（=图片行序，merged 已按 foodCode 排序）判定：
    原书 foodCode 跳号属常态；非数值行（"—"等）无方向信息，透传不断块。
    （零星孤立互换只报告不修——那是识别错位的特征，不是整片印刷错误的特征）

    Returns:
        (auto_food_codes: set, isolated: [(foodCode, kcal, kJ), ...])
    """
    kinds = [classify(r) for r in records]
    blocks, cur = [], []   # cur: [(idx, kind)]，swap 与 nonnum 均可延伸块
    for i, k in enumerate(kinds):
        if k in ("swap", "nonnum"):
            cur.append((i, k))
        else:
            if cur:
                blocks.append(cur)
                cur = []
    if cur:
        blocks.append(cur)

    auto, isolated = set(), []   # auto: 可自动修的 foodCode 集合
    for b in blocks:
        swaps = [i for i, k in b if k == "swap"]
        if len(swaps) >= MIN_BLOCK:
            auto.update(str(records[i].get("foodCode", "")) for i in swaps)
        else:
            isolated.extend((str(records[i].get("foodCode", "")),
                             records[i].get(KCAL), records[i].get(KJ)) for i in swaps)
    return auto, isolated


def run(apply: bool):
    ROOT = Path(__file__).resolve().parent.parent   # 项目根（本脚本位于 utils_v3/ 下）
    cfg = json.loads((ROOT / "config_v3.json").read_text(encoding="utf-8"))
    src = Path(cfg["output_dir"])
    dst = src.parent / f"{src.name}_fixed"
    if not src.is_dir():
        raise SystemExit(f"源目录不存在: {src}（config_v3.json output_dir）")

    fix_log, gray, isolated_all = [], [], []
    n_files = n_auto = 0
    print(f"[{'APPLY' if apply else 'DRY-RUN'}] 源: {src} -> 目标: {dst}\n")
    print(f"{'品类文件':<44} {'互换':>4} {'自动修':>6}")
    for f in sorted(src.glob("merged_*.json")):
        recs = json.loads(f.read_text(encoding="utf-8"))
        auto, isolated = find_swap_blocks(recs)
        isolated_all += [(f.name, *t) for t in isolated]
        for r in recs:
            c = classify(r)
            if c == "swap" and str(r.get("foodCode", "")) in auto:
                fix_log.append({
                    "file": f.name, "foodCode": r.get("foodCode", ""),
                    "foodName": r.get("foodName", ""),
                    "before": {KCAL: r[KCAL], KJ: r[KJ]},
                    "after": {KCAL: r[KJ], KJ: r[KCAL]},
                })
            elif c == "other":
                gray.append({"file": f.name, "foodCode": r.get("foodCode", ""),
                             "foodName": r.get("foodName", ""),
                             KCAL: r.get(KCAL), KJ: r.get(KJ)})
        n_files += 1
        n_auto += len(auto)
        if auto or isolated:
            print(f"{f.name:<44} {len(auto) + len(isolated):>4} {len(auto):>6}"
                  + (f"  (孤立不修: {len(isolated)})" if isolated else ""))

    print(f"\n合计: {n_files} 个品类文件, 自动修 {len(fix_log)} 条, "
          f"孤立互换不修 {len(isolated_all)} 条, 灰色(比值双向异常,人工核对) {len(gray)} 条")

    if not fix_log:
        print("无需修改，不生成矫正目录。")
        return
    if not apply:
        print("\n这是 dry-run。确认无误后执行: python utils_v3/fix_energy_swap.py --apply")
        return

    dst.mkdir(parents=True, exist_ok=True)
    # 全量复制源目录（merged_*.json 之外的 apply_log.json 等一并，保持目录自包含）
    for f in sorted(src.iterdir()):
        if f.is_file():
            shutil.copy2(f, dst / f.name)
    # 重写含修正的 merged 文件：从源逐条重放（幂等——矫正目录永远由识别版全量重建）
    by_file = {}
    for e in fix_log:
        by_file.setdefault(e["file"], set()).add(e["foodCode"])
    for fname, codes in by_file.items():
        recs = json.loads((src / fname).read_text(encoding="utf-8"))
        for r in recs:
            if str(r.get("foodCode", "")) in codes:
                r[KCAL], r[KJ] = r[KJ], r[KCAL]
        (dst / fname).write_text(
            json.dumps(recs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (dst / "energy_swap_fix_log.json").write_text(
        json.dumps({"total_fixes": len(fix_log), "fixes": fix_log,
                    "isolated_not_fixed": isolated_all, "gray_manual_review": gray},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"矫正版已生成: {dst}/（{len(fix_log)} 条互换已交换, 含 energy_swap_fix_log.json）")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="实际生成矫正目录（默认 dry-run）")
    run(ap.parse_args().apply)
