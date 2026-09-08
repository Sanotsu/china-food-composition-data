# -*- coding: utf-8 -*-
"""中英文对照表识别流水线（单图 3 列：foodCode / foodName / englishName）

与 support_v3（营养素流水线）并行独立，复用其模型配置、流式识别循环、
LCS行对齐与复核模板等经过验证的工具函数；营养素流水线不受影响。
"""
