#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""中英文对照表流水线配置加载模块

模型配置复用 support_v3.config 的 ModelConfig/_load_model_cfg（.env 密钥引用方式一致）；
表结构固定 3 列，无需值域/恒等式等营养素专用配置。
"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List

from support_v3.config import ConfigError, ModelConfig, _load_model_cfg

# 对照表列定义（顺序必须与表格截图一致）
EN_COLS = ["foodCode", "foodName", "englishName"]


@dataclass
class EnAppConfig:
    input_dir: Path
    temp_dir: Path
    output_dir: Path
    review_dir: Path
    review_image_base: str          # 复核页图片URL前缀（空=本地相对路径）
    cross_check_dir: Path           # 营养素成果目录（foodCode 交叉核对基准；空串=跳过核对）
    models: List[ModelConfig]
    pilot_enabled: bool
    pilot_base_names: List[str]
    pilot_dirs: List[str]
    max_workers: int
    max_retries: int
    stream_idle_timeout: float
    ttft_thinking_hint: float
    consensus_policy: str

    @property
    def raw_dir(self) -> Path:
        return self.temp_dir / "raw"

    @property
    def parsed_dir(self) -> Path:
        return self.temp_dir / "parsed"

    @property
    def consensus_dir(self) -> Path:
        return self.temp_dir / "consensus"

    @property
    def validation_dir(self) -> Path:
        return self.temp_dir / "validation"


def load_en_config(config_path: str = "config_v3_en.json") -> EnAppConfig:
    """加载 config_v3_en.json 并解析为 EnAppConfig

    Raises:
        ConfigError: 配置文件缺失/格式错误/环境变量缺失
    """
    path = Path(config_path)
    if not path.exists():
        raise ConfigError(f"配置文件不存在: {path.resolve()}")

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    models = [_load_model_cfg(m) for m in raw.get("models", [])]
    if len(models) < 2:
        raise ConfigError("需要至少配置 2 个交叉识别模型")

    pilot = raw.get("pilot") or {}
    pilot_enabled = bool(pilot.get("enabled", False))
    pilot_base_names = list(pilot.get("base_names", []))
    pilot_dirs = list(pilot.get("dirs", []))
    if pilot_enabled and not pilot_base_names and not pilot_dirs:
        raise ConfigError("pilot.enabled 为 true 但未配置 pilot.base_names 或 pilot.dirs")

    temp_dir = Path(raw.get("temp_dir", "temp_v3_en"))
    output_dir_raw = raw.get("output_dir")
    if output_dir_raw:
        output_dir = Path(output_dir_raw)
    else:
        import time
        stamp = time.strftime("%Y%m%d")
        output_dir = Path(f"json_name_en_map_v3_{stamp}_{'+'.join(m.tag for m in models)}")

    cross_check_raw = str(raw.get("cross_check_dir", "") or "")
    cross_check_dir = Path(cross_check_raw) if cross_check_raw else Path()

    concurrency = raw.get("concurrency") or {}
    concurrency_defaults = {"max_workers": 4, "max_retries": 3,
                            "stream_idle_timeout": 120, "ttft_thinking_hint": 15}

    return EnAppConfig(
        input_dir=Path(raw.get("input_dir", "食物名称中英文对照表截图")),
        temp_dir=temp_dir,
        output_dir=output_dir,
        review_dir=Path(raw.get("review_dir", "review_v3_en")),
        review_image_base=str(raw.get("review_image_base", "") or ""),
        cross_check_dir=cross_check_dir,
        models=models,
        pilot_enabled=pilot_enabled,
        pilot_base_names=pilot_base_names,
        pilot_dirs=pilot_dirs,
        max_workers=int(concurrency.get("max_workers", concurrency_defaults["max_workers"])),
        max_retries=int(concurrency.get("max_retries", concurrency_defaults["max_retries"])),
        stream_idle_timeout=float(concurrency.get("stream_idle_timeout", concurrency_defaults["stream_idle_timeout"])),
        ttft_thinking_hint=float(concurrency.get("ttft_thinking_hint", concurrency_defaults["ttft_thinking_hint"])),
        consensus_policy=raw.get("consensus_policy", "model_a"),
    )
