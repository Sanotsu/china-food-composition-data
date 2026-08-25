#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""配置加载模块

v3_config.json 中的 API 密钥通过 *_env 字段引用 .env 中的环境变量名，
避免密钥明文写入仓库配置文件。
"""
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

# 能量页列定义（17列，顺序必须与表格截图一致）
ENERGY_COLS = [
    "foodCode", "foodName", "edible", "water",
    "energyKCal", "energyKJ", "protein", "fat", "CHO",
    "dietaryFiber", "cholesterol", "ash",
    "vitaminA", "carotene", "retinol", "thiamin", "riboflavin",
]

# 营养素页列定义（19列，顺序必须与表格截图一致）
NUTRIENT_COLS = [
    "foodCode", "foodName",
    "niacin", "vitaminC",
    "vitaminETotal", "vitaminE1", "vitaminE2", "vitaminE3",
    "Ca", "P", "K", "Na", "Mg", "Fe", "Zn", "Se", "Cu", "Mn", "remark",
]

# 合并后的完整字段顺序（输出JSON的字段结构）
MERGED_FIELD_ORDER = ENERGY_COLS + [c for c in NUTRIENT_COLS if c not in ("foodCode", "foodName")]

DEFAULT_THRESHOLDS = {
    "kj_rel_tolerance": 0.02,
    "atwater_rel_tolerance": 0.10,
    "dry_matter_tolerance": 0.5,
    "ve_abs_tolerance": 0.02,
    "ve_rel_tolerance": 0.05,
    "vitamin_a_rel_tolerance": 0.10,
    "vitamin_a_abs_tolerance": 2.0,
}


class ConfigError(Exception):
    """配置错误"""


@dataclass
class ModelConfig:
    tag: str
    model_name: str
    api_base: str
    api_key: str
    max_tokens: int
    temperature: Optional[float]  # None 表示不发送该参数（部分模型如 kimi-k3 不支持）
    timeout: float
    extra_payload: Dict[str, Any] = field(default_factory=dict)
    response_format: Optional[Dict[str, Any]] = None  # 如 {"type":"json_object"}；null=不发送


@dataclass
class AppConfig:
    input_dir: Path
    manual_counts_md: Path
    ignore_prefixes: List[str]
    temp_dir: Path
    output_dir: Path
    review_dir: Path
    models: List[ModelConfig]
    pilot_enabled: bool
    pilot_base_names: List[str]
    pilot_dirs: List[str]
    max_workers: int
    max_retries: int
    stream_idle_timeout: float
    ttft_thinking_hint: float
    thresholds: Dict[str, float]
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

    @property
    def model_tags_joined(self) -> str:
        return "+".join(m.tag for m in self.models)

    def model_by_tag(self, tag: str) -> ModelConfig:
        for m in self.models:
            if m.tag == tag:
                return m
        raise ConfigError(f"未找到模型 tag: {tag}")


def _load_model_cfg(raw: Dict[str, Any]) -> ModelConfig:
    tag = raw.get("tag")
    model_name = raw.get("model_name")
    if not tag or not model_name:
        raise ConfigError(f"模型配置缺少 tag 或 model_name: {raw}")

    api_base_env = raw.get("api_base_env", "")
    api_key_env = raw.get("api_key_env", "")
    api_base = raw.get("api_base") or os.getenv(api_base_env, "")
    api_key = raw.get("api_key") or os.getenv(api_key_env, "")

    if not api_base:
        raise ConfigError(f"模型 {tag} 缺少 api_base，请检查 .env 中的 {api_base_env}")
    if not api_key:
        raise ConfigError(f"模型 {tag} 缺少 api_key，请检查 .env 中的 {api_key_env}")

    return ModelConfig(
        tag=tag,
        model_name=model_name,
        api_base=api_base.rstrip("/"),
        api_key=api_key,
        max_tokens=int(raw.get("max_tokens", 8192)),
        temperature=None if raw.get("temperature") is None else float(raw.get("temperature", 0.1)),
        timeout=float(raw.get("timeout", 300)),
        extra_payload=raw.get("extra_payload") or {},
        response_format=raw.get("response_format"),
    )


def load_app_config(config_path: str = "v3_config.json") -> AppConfig:
    """加载 v3_config.json 并解析为 AppConfig

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

    temp_dir = Path(raw.get("temp_dir", "temp_v3"))
    output_dir_raw = raw.get("output_dir")
    if output_dir_raw:
        output_dir = Path(output_dir_raw)
    else:
        import time
        stamp = time.strftime("%Y%m%d")
        output_dir = Path(f"json_data_v3_{stamp}_{'+'.join(m.tag for m in models)}")

    thresholds = dict(DEFAULT_THRESHOLDS)
    thresholds.update(raw.get("thresholds") or {})

    # review web项目输出目录：固定独立目录（不随 temp_dir 变化），每次执行自动覆盖更新
    review_dir = Path(raw.get("review_dir", "review_v3"))

    concurrency = raw.get("concurrency") or {}
    concurrency_defaults = {"max_workers": 4, "max_retries": 3,
                            "stream_idle_timeout": 120, "ttft_thinking_hint": 15}

    return AppConfig(
        input_dir=Path(raw.get("input_dir", "营养素截图")),
        manual_counts_md=Path(raw.get("manual_counts_md", "手动统计图片数据数量251204.md")),
        ignore_prefixes=list(raw.get("ignore_prefixes", [])),
        temp_dir=temp_dir,
        output_dir=output_dir,
        review_dir=review_dir,
        models=models,
        pilot_enabled=pilot_enabled,
        pilot_base_names=pilot_base_names,
        pilot_dirs=pilot_dirs,
        max_workers=int(concurrency.get("max_workers", concurrency_defaults["max_workers"])),
        max_retries=int(concurrency.get("max_retries", concurrency_defaults["max_retries"])),
        stream_idle_timeout=float(concurrency.get("stream_idle_timeout", concurrency_defaults["stream_idle_timeout"])),
        ttft_thinking_hint=float(concurrency.get("ttft_thinking_hint", concurrency_defaults["ttft_thinking_hint"])),
        thresholds=thresholds,
        consensus_policy=raw.get("consensus_policy", "model_a"),
    )


def extract_base_category(name: str) -> str:
    """提取基础类别名（去除末尾数字后缀）

    例: "畜肉类及其制品-牛1" -> "畜肉类及其制品-牛"; "畜肉类及其制品-驴" -> "畜肉类及其制品-驴"
    """
    m = re.match(r"(.+?)\d+$", name)
    return m.group(1) if m else name


def normalize_name(name: str) -> str:
    """文件名/类别名归一化：去除所有空白字符，用于匹配手动统计md中的带空格文件名"""
    return re.sub(r"\s+", "", name)


# ---------- foodName 全角/半角归一化工具（parsing 与 validation 共用） ----------

# 比较用：全角/半角标点、顿号逗号都映射到同一字符（方向无所谓，只为等价判定）
_COMPARE_MAP = str.maketrans({
    "（": "(", "）": ")", "［": "[", "］": "]", "【": "[", "】": "]",
    "，": ",", "、": ",", "；": ";", "：": ":", "．": ".",
})

# 输出统一用：半角转全角（用户要求统一为全角）
_CANONICAL_MAP = str.maketrans({
    "(": "（", ")": "）", "[": "［", "]": "］", ",": "，", ";": "；",
})


def normalize_for_compare(s: str) -> str:
    """名称比较归一化：去所有空白 + 全半角标点归一（驴肉(瘦) ≡ 驴肉（瘦）、牛展,牛腱子 ≡ 牛展、牛腱子）"""
    return re.sub(r"\s+", "", str(s or "").translate(_COMPARE_MAP))


def names_equivalent(a: str, b: str) -> bool:
    """两侧名称是否仅存在全角/半角标点或空格差异（视为内容一致）"""
    return normalize_for_compare(a) == normalize_for_compare(b)


def canonicalize_name(s: str) -> str:
    """输出统一的名称形式：去内部空白 + 半角括号/逗号转全角"""
    return re.sub(r"\s+", "", str(s or "").strip()).translate(_CANONICAL_MAP)
