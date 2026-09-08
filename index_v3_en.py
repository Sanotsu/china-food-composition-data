#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""中英文对照表主入口：双模型交叉识别 + foodCode交叉核对 + 定点人工复核 流水线

与 index_v3.py（营养素流水线）并行独立，表结构为 3 列（foodCode/foodName/englishName）。

用法：
  python index_v3_en.py --stage all                    # 识别->解析合并->校验->生成复核报告
  python index_v3_en.py --stage recognize              # 仅阶段1（支持断点续跑）
  python index_v3_en.py --stage recognize --force      # 强制重新识别
  python index_v3_en.py --stage merge                  # 仅阶段2
  python index_v3_en.py --stage validate               # 仅阶段3（依赖阶段2产物）
  python index_v3_en.py --stage report                 # 仅阶段4（依赖阶段3产物）
  python index_v3_en.py --stage apply                  # 阶段5：应用人工修正并输出最终JSON
  python index_v3_en.py --stage apply --corrections review_v3_en/corrections.json
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from support_v3.config import ConfigError
from support_v3_en.config import load_en_config
from support_v3_en.recognition import run_recognition
from support_v3_en.parsing import run_merge
from support_v3_en.validation import run_validation
from support_v3_en.review_report import run_report
from support_v3_en.apply_corrections import run_apply

STAGES = ("recognize", "merge", "validate", "report", "apply", "all")


def main():
    parser = argparse.ArgumentParser(description="中英文对照表：双模型交叉识别 + foodCode交叉核对 + 定点人工复核")
    parser.add_argument("--config", default="config_v3_en.json", help="配置文件路径")
    parser.add_argument("--stage", required=True, choices=STAGES, help="执行阶段")
    parser.add_argument("--force", action="store_true", help="识别阶段强制重跑（忽略已有JSON）")
    parser.add_argument("--corrections", default=None,
                        help="apply阶段使用的修正文件路径（默认 {review_dir}/corrections.json）")
    args = parser.parse_args()

    try:
        cfg = load_en_config(args.config)
    except Exception as e:  # noqa: BLE001
        print(f"配置加载失败: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"输入目录: {cfg.input_dir} | 临时目录: {cfg.temp_dir} | 输出目录: {cfg.output_dir}")
    print(f"模型: {' vs '.join(m.model_name + '(' + m.tag + ')' for m in cfg.models)}")
    if cfg.cross_check_dir and cfg.cross_check_dir != Path():
        print(f"foodCode交叉核对基准: {cfg.cross_check_dir}")
    if cfg.pilot_enabled:
        parts = []
        if cfg.pilot_base_names:
            print(f"[试点模式] base_names: {', '.join(cfg.pilot_base_names)}")
        if cfg.pilot_dirs:
            print(f"[试点模式] dirs: {', '.join(cfg.pilot_dirs)}")

    merge_result = None
    if args.stage in ("recognize", "all"):
        print("\n========== 阶段1 识别 ==========")
        asyncio.run(run_recognition(cfg, force=args.force))

    if args.stage in ("merge", "all"):
        print("\n========== 阶段2 解析合并 ==========")
        merge_result = run_merge(cfg)
        if not merge_result and args.stage == "all":
            print("阶段2无产物，终止流水线", file=sys.stderr)
            sys.exit(1)

    if args.stage in ("validate", "all"):
        print("\n========== 阶段3 自动校验 ==========")
        if merge_result is None:
            merge_result = run_merge(cfg)
        report = run_validation(cfg, merge_result)

    if args.stage in ("report", "all"):
        print("\n========== 阶段4 生成复核报告 ==========")
        report_path = cfg.validation_dir / "validation_report.json"
        if not report_path.exists():
            print(f"未找到校验报告 {report_path}，请先执行 validate 阶段", file=sys.stderr)
            sys.exit(1)
        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)
        run_report(cfg, report)

    if args.stage == "apply":
        print("\n========== 阶段5 应用人工修正 ==========")
        corr = Path(args.corrections) if args.corrections else cfg.review_dir / "corrections.json"
        run_apply(cfg, corr)

    print("\n完成。")


if __name__ == "__main__":
    main()
