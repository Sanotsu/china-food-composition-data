#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段5：人工修正回写模块

读取人工填写好的 corrections.json，应用到共识版数据，重新校验后
输出最终 JSON 到 output_dir。

corrections.json 条目格式：
{
  "issue_id": 12,                       // 可选，对应报告问题编号
  "category": "畜肉类及其制品-牛",       // 必填，目标类别（merged_ 后的名称）
  "foodCode": "084101",                 // update/delete 必填；insert 时为空则追加
  "field": "protein",                   // update 必填
  "action": "update",                   // update / delete / insert
  "value": "21.5"                       // update/insert 需要（insert 为完整记录对象）
}
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from .config import MERGED_FIELD_ORDER, AppConfig
from .parsing import normalize_cell, sort_by_food_code
from .validation import IssueAccumulator, validate_record


def load_corrections(path: Path) -> List[Dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"未找到修正文件 {path}。请先复制 corrections_template.json 为 corrections.json 并填写。")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("corrections.json 顶层必须是数组")
    return data


def apply_one(data: List[Dict], c: Dict) -> str:
    """应用单条修正，返回操作日志文本"""
    action = c.get("action", "update")
    category = c.get("category", "")
    code = str(c.get("foodCode", ""))
    field = c.get("field", "")

    if action == "insert":
        record = c.get("value")
        if not isinstance(record, dict):
            return f"[跳过] insert 需要value为完整记录对象: {c}"
        item = {k: normalize_cell(str(record.get(k, ""))) for k in MERGED_FIELD_ORDER}
        data.append(item)
        sort_by_food_code(data)
        return f"[插入] {category} {item['foodCode']} {item['foodName']}"

    idx = next((i for i, it in enumerate(data) if str(it.get("foodCode", "")) == code), None)
    if idx is None:
        return f"[跳过] 未找到 foodCode={code} (category={category})"

    if action == "delete":
        removed = data.pop(idx)
        return f"[删除] {category} {code} {removed.get('foodName', '')}"

    if action == "update":
        if field not in MERGED_FIELD_ORDER:
            return f"[跳过] 未知字段 {field}: {c}"
        old = data[idx].get(field, "")
        # 值归一化（与解析层同规则）：去空白、"-"/"–"→"—"、"tr"→"Tr"
        data[idx][field] = normalize_cell(str(c.get("value", "")))
        return f"[更新] {category} {code}.{field}: \"{old}\" -> \"{data[idx][field]}\""

    return f"[跳过] 未知 action: {action}"


def run_apply(cfg: AppConfig, corrections_path: Path) -> Dict:
    """执行阶段5：应用修正 -> 重校验 -> 输出最终JSON"""
    corrections = load_corrections(corrections_path)

    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    log: List[str] = []
    consensus_files = sorted(cfg.consensus_dir.glob("merged_*.json"))
    if not consensus_files:
        raise FileNotFoundError(f"共识版数据目录为空: {cfg.consensus_dir}")

    # 清理输出目录中不属于本次共识版的过时类别文件
    # （output_dir 固定复用时，旧轮次已消失类别的 merged_*.json 不会被动覆盖，需主动剔除）
    consensus_names = {f.name for f in consensus_files}
    for stale in cfg.output_dir.glob("merged_*.json"):
        if stale.name not in consensus_names:
            stale.unlink()
            log.append(f"[清理] 过时文件 {stale.name}")

    # 按类别把修正分组后逐文件应用
    by_cat: Dict[str, List[Dict]] = {}
    for c in corrections:
        by_cat.setdefault(c.get("category", ""), []).append(c)

    acc = IssueAccumulator()
    for f in consensus_files:
        category = f.stem[len("merged_"):]
        data = json.loads(f.read_text(encoding="utf-8"))
        for c in by_cat.get(category, []):
            log.append(apply_one(data, c))
        # 修正后重新执行字段级校验（不含行数/共识类，那些是一次性的；
        # 值域超标降warning——共识版已是双模型裁决产物或人工修正值）
        for item in data:
            validate_record(item, category, "final", {"energy": "", "nutrient": ""}, acc, cfg.thresholds,
                            range_as_warning=True)
        out = cfg.output_dir / f.name
        with open(out, "w", encoding="utf-8") as fp:
            json.dump(sort_by_food_code(data), fp, ensure_ascii=False, indent=2)

    remaining = {"error": 0, "warning": 0, "review": 0}
    for iss in acc.issues:
        remaining[iss["level"]] += 1

    result = {
        "applied_at": datetime.now().isoformat(),
        "corrections_file": str(corrections_path),
        "total_corrections": len(corrections),
        "log": log,
        "remaining_after_revalidate": remaining,
        "remaining_issues": acc.issues,
        "output_dir": str(cfg.output_dir),
        "output_files": [f.name for f in sorted(cfg.output_dir.glob("merged_*.json"))],
    }
    apply_log = cfg.output_dir / "apply_log.json"
    with open(apply_log, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    for line in log:
        print(line)
    print(f"\n应用修正 {len(corrections)} 条，最终输出目录: {cfg.output_dir}")
    print(f"重校验剩余: error={remaining['error']} warning={remaining['warning']}"
          f"（详见 {apply_log}）")
    return result
