#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""增强版数据生成：在 fixed 营养素数据基础上按 foodCode 回填英文名

背景：
  - json_data_v3_*_fixed/ 已是中文名人工矫正版（营养素权威数据）
  - json_name_en_map_v3_*/（index_v3_en.py 流水线 apply 产物）是中英文对照表
    识别结果（仅 foodCode/foodName/englishName 三列，英文经人工矫正）
  - 原书营养素表与中英文对照表本就不是一一对应（cross_check.json 中
    missing_en/en_only 为预期差异），仅按 foodCode 能配上就回填

原则：
  - 营养素文件的记录 1:1 保留（字段、顺序、数值字符串逐字节等价），
    仅在每个记录的 foodName 之后插入 englishName 字段（无英文名填 ""）
  - EN map 中营养素没有的 foodCode（en_only）不写入，仅记录在日志
  - 中文名一律以 fixed 版为准，EN map 的 foodName 不参与回填

用法：
  python utils_v3/enrich_nutrition_en.py            # dry-run：仅报告
  python utils_v3/enrich_nutrition_en.py --apply    # 生成/重建增强版目录
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config_v3_en.json"


def load_dirs_from_config(config_path: Path):
    """从 config_v3_en.json 读取 EN map 目录与营养素基准目录"""
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return Path(cfg["cross_check_dir"]), Path(cfg["output_dir"])


def load_en_map(en_dir: Path):
    """加载 EN map：foodCode -> englishName"""
    en_map = {}
    for f in sorted(en_dir.glob("merged_*.json")):
        for it in json.loads(f.read_text(encoding="utf-8")):
            code = str(it.get("foodCode", ""))
            if code:
                en_map[code] = str(it.get("englishName", ""))
    return en_map


def enrich_record(rec: dict, en_map: dict) -> dict:
    """在 foodName 之后插入 englishName，其余字段与顺序原样保留"""
    out = {}
    inserted = False
    for k, v in rec.items():
        out[k] = v
        if k == "foodName" and not inserted:
            out["englishName"] = en_map.get(str(rec.get("foodCode", "")), "")
            inserted = True
    if not inserted:  # 异常记录无 foodName 时兜底追加
        out["englishName"] = en_map.get(str(rec.get("foodCode", "")), "")
    return out


def main():
    parser = argparse.ArgumentParser(description="按 foodCode 将英文名回填到 fixed 营养素数据，生成增强版")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="对照表流水线配置（读 en map 与基准目录）")
    parser.add_argument("--base", default=None, help="营养素基准目录（默认取配置 cross_check_dir）")
    parser.add_argument("--en", default=None, help="EN map 目录（默认取配置 output_dir）")
    parser.add_argument("--out", default=None, help="增强版输出目录（默认 {base}_en）")
    parser.add_argument("--apply", action="store_true", help="实际写出（缺省 dry-run 仅报告）")
    args = parser.parse_args()

    base_dir = Path(args.base) if args.base else None
    en_dir = Path(args.en) if args.en else None
    if base_dir is None or en_dir is None:
        cfg_base, cfg_en = load_dirs_from_config(Path(args.config))
        base_dir = base_dir or cfg_base
        en_dir = en_dir or cfg_en
    out_dir = Path(args.out) if args.out else base_dir.parent / (base_dir.name + "_en")

    base_files = sorted(base_dir.glob("merged_*.json"))
    if not base_files:
        raise SystemExit(f"基准目录无 merged_*.json: {base_dir}")
    en_map = load_en_map(en_dir)
    if not en_map:
        raise SystemExit(f"EN map 目录无 merged_*.json: {en_dir}")

    matched = missing = 0
    missing_en = []          # 营养素有、EN map 无（原书无英文名的预期差异）
    enriched_by_file = {}
    for f in base_files:
        records = json.loads(f.read_text(encoding="utf-8"))
        new_records = []
        for rec in records:
            new_records.append(enrich_record(rec, en_map))
            if str(rec.get("foodCode", "")) in en_map:
                matched += 1
            else:
                missing += 1
                missing_en.append({"foodCode": rec.get("foodCode", ""),
                                   "foodName": rec.get("foodName", ""),
                                   "category": f.stem[len("merged_"):]})
        enriched_by_file[f.name] = new_records
    total = matched + missing
    en_only = sorted(set(en_map) - {str(r.get("foodCode", ""))
                                    for rs in enriched_by_file.values() for r in rs})

    print(f"基准: {base_dir}（{len(base_files)} 个文件，{total} 条记录）")
    print(f"EN map: {en_dir}（{len(en_map)} 条）")
    print(f"回填: 匹配 {matched} 条（{matched / total:.1%}），无英文名 {missing} 条")
    print(f"EN map 未使用（营养素无此foodCode）: {len(en_only)} 条")
    print(f"输出目录: {out_dir}{'（dry-run 未写出）' if not args.apply else ''}")

    if not args.apply:
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    # 清理过时文件（基准已删除的类别不残留）
    base_names = {f.name for f in base_files}
    for stale in out_dir.glob("merged_*.json"):
        if stale.name not in base_names:
            stale.unlink()
    for name, records in enriched_by_file.items():
        (out_dir / name).write_text(
            json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    log = {
        "applied_at": datetime.now().isoformat(),
        "base_dir": str(base_dir),
        "en_dir": str(en_dir),
        "output_dir": str(out_dir),
        "base_files": len(base_files),
        "total_records": total,
        "matched": matched,
        "without_english": missing,
        "missing_en": missing_en,
        "en_only_unused": en_only,
    }
    log_path = out_dir / "enrich_log.json"
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写出 {len(base_files)} 个文件，日志: {log_path}")


if __name__ == "__main__":
    main()
