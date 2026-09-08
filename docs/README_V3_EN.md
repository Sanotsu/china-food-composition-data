# V3 对照表方案说明（index_v3_en.py）

食物名称中英文对照表（书末附表）的识别流水线：双模型交叉识别 + 自动校验 + 定点人工复核。
与 `index_v3.py`（营养素流水线）并行独立，复用其经过验证的识别循环 / LCS 行对齐 / 复核页框架，
营养素流水线行为不受影响。

## 与营养素流水线的差异

| 维度 | 营养素流水线（index_v3.py） | 对照表流水线（index_v3_en.py） |
| --- | --- | --- |
| 图片组织 | 每子类成对截图 `-energy` / `-nutrient` | 每子类**单张**截图 |
| 表格列 | energy 17 列 / nutrient 19 列 | 3 列：`foodCode` / `foodName` / `englishName` |
| 类别命名 | 文件名自带前缀 `乳类及其制品-奶粉1` | 由父文件夹拼出：`猪1.png` → `畜肉类及其制品-猪1`（猪1/猪2/猪3 合并到 `畜肉类及其制品-猪`） |
| 文件键 | 图片 stem | `{父文件夹}-{stem}`（多个子目录存在同名 `其他.png`，stem 会冲突） |
| 校验规则 | 恒等式 / 值域 / 行数 / 符号白名单 | foodCode 格式 / englishName 规则 / 双模型共识 |
| 符号归一 | `-`→`—`、`tr`→`Tr` | **不做**（英文文本连字符等标点必须原样保留） |
| 交叉核对 | — | 与营养素成果比 foodCode 集合差异（**信息性输出**，见下文） |

## englishName 折行处理（核心风险点）

原书英文过长时单元格内折为两行（如 031207 豆奶粉），两道防线：

1. **提示词显式规则**（`support_v3_en/recognition.py`）：折行内容必须按顺序拼接为
   一行完整字符串（断点处在英文单词间用一个空格连接），禁止丢弃第二行、禁止输出换行符
2. **校验兜底**（`support_v3_en/validation.py`）：
   - englishName 含换行/控制字符 → **error**（拼接失败的直接信号）
   - 含中文字符 → warning（疑似误录）
   - 为空 → warning（请确认原书是否本无英文名）
   - 双模型共识对 englishName 逐字段比对——拼接方式不同的两侧文本通常不同，能被交叉发现

## 阶段说明

```
单图 → [阶段1] 双模型各识别一遍（JSON结构化，复用流式+看门狗+断点续跑）
     → [阶段2] 解析 + foodCode去重（同图/跨图重复报error保留首条）+ 类别聚合（code升序）
     → [阶段3] 字段校验 + 双模型共识（分歧取 consensus_policy 值并标review）
              + foodCode 交叉核对（独立文件，不计入问题清单）
     → [阶段4] web复核报告（单图3列镜像表格，分歧行拆A/B标红）
     → 人工填写 corrections.json → [阶段5] 应用修正+重校验 → 最终JSON
```

- 阶段2 单图文件保留模型输出行序（=图片行序），供复核镜像对照；类别文件按 foodCode 排序
- **corrections 防护**：`update` 条目 value 留空 = 未填模板项，一律跳过（保留共识值），
  避免把 foodName 中文名误清空——人工只填需要修正的值即可
- 其余断点续跑 / 试点模式 / 4xx不重试 / 全角半角归一等行为与营养素流水线一致

## foodCode 交叉核对（信息性，不是校验）

原书营养素表与书末中英文对照表**本就不是一一对应**（部分营养素条目原书就没有英文名），
因此集合差异属预期现象，不作为识别缺漏：

- `en_only`：对照表有、营养素成果无（本轮 18 条）
- `missing_en`：营养素有、对照表无（本轮 435 条）

明细输出到 `{temp_dir}/validation/cross_check.json`（阶段3与阶段5各更新一次）；
增强版的 `enrich_log.json` 中同内容字段名为 `missing_en` / `en_only_unused`。
对照表数据质量以**双模型共识准确性**为准。

## config_v3_en.json 要点

- `input_dir`：对照表截图根目录（递归扫描 `*.png`）
- `temp_dir` / `review_dir` / `output_dir`：独立于营养素流水线（`temp_v3_en` / `review_v3_en`）
- `cross_check_dir`：foodCode 交叉核对基准（营养素成果目录，如 `_fixed` 版）
- `models`：与营养素配置同构；`consensus_policy` 分歧取值策略
- `pilot.dirs`：试点目录（分批识别省 API；阶段2起自动覆盖 raw 中已累积的全部批次）
- `.env` 密钥与营养素流水线共用（`V3_DASHSCOPE_API_BASE` / `V3_DASHSCOPE_API_KEY`）

## 全量实测成绩（2026-09，83 张图 / 166 次 API 调用）

| 指标 | 结果 |
| --- | --- |
| 识别 | 1260 条（60 个类别文件），foodCode 无重复、格式全合法 |
| 需人工处理 | 88 条模板项（分歧/解析类），人工仅矫正 englishName **18 条** |
| apply 后重校验 | error=0 / warning=0 |
| 增强版回填 | 1677 条中 1242 条（74.1%）获得英文名，其余字段与 fixed 版逐字节等价 |

## 增强版数据生成

`utils_v3/enrich_nutrition_en.py`：在 fixed 矫正版基础上按 `foodCode` 回填 `englishName`
（紧跟 `foodName` 字段，无英文名填空串）；中文名一律以 fixed 版为准，
EN map 的 `foodName` 不参与回填；记录与字段顺序 1:1 保留。

```sh
python utils_v3/enrich_nutrition_en.py            # dry-run：仅报告
python utils_v3/enrich_nutrition_en.py --apply    # 生成/重建 {fixed}_en/
```
