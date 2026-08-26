# 说明

此处的测试数据，主要为了[Sanotsu/free-fitness](https://github.com/Sanotsu/free-fitness) 项目的食物导入功能的测试。

将 pdf 版本的[《中国食物成分表标准版（第 6 版）》](https://www.pumpedu.com/home-shop/5514.html) 中"能量和食物一般营养成分"部分进行截图，通过 python 脚本构建为指定格式的 json 文件。

---

2026-08-25 更新

**最新版数据**在以下文件夹：

- 原书识别版：[json_data_v3_20260825_qwen38max_kimi_k3](./json_data_v3_20260825_qwen38max_kimi_k3)
- 手动矫正版：[json_data_v3_20260825_qwen38max_kimi_k3_fixed](./json_data_v3_20260825_qwen38max_kimi_k3_fixed)

识别说明：

- 食品数据共计 **1677 条**，61 个类别文件，可直接使用
- 先由两个视觉大模型（`qwen3.8-max` 与 `kimi-k3`）交叉识别
- 再本地自动校验（恒等式/值域/行数/双模型共识）
- 然后经人工定点复核修正生成(我只处理了两个模型识别不一致的地方)
  - **目前可能数据出错就是 qwen3.8-max 和 kimi-k3 的视觉识别都错了，还错成一样了**
- `apply_log.json` 记录人工修正与重校验结果
- 约 11.4 万个单元格的处理链路已经 `utils_v3/test_v3_output.py` 独立重放验证零偏差

校对说明：

- 原书中 `蔬菜类及其制品-野生蔬菜类`（048004~048084），能量 kcal 和 kJ 数值明显是标反了的
- 这部分可以确认的，手动调整为正确的值（而识别版优先数据和原书一致，错也错一样）

"婴幼儿食品"分类的表格栏位与其他分类不同，且数据多为品牌奶粉商品而非通用数据，流水线通过配置 `ignore_prefixes` 跳过，数据中不包含该分类。

我的两个基准大模型API构建的原始json数据，可查看 [https://cfcd.pages.dev/](https://cfcd.pages.dev/)

---

2025-07-24 添加了 [食物升糖指数（GI）截图](./食物升糖指数（GI）截图) 和 [食物 GI 指数 json 文件](json_gi_of_foods/glycemic_index_of_foods.json) (这个数据量少，有手动检查过)。

---

## 脚本说明

将同类食物"能量"部分和"一般营养成分"部分的成对截图，  
调用两个视觉大模型 API 识别为JSON 结构化数据（字段显式命名、数值原文保真），  
按 foodCode 配对合并、双模型交叉校验，  
再经人工复核修正，构建成指定格式的 json 文件。

即多张同类食物"能量"部分和"一般营养成分"部分的截图，如：

`禽肉类及其制品-鸡1-energy.png`:
![禽肉类及其制品-鸡1-energy.png](./docs/_readme_pics/禽肉类及其制品-鸡1-energy.png)

`禽肉类及其制品-鸡1-nutrient.png`:
![禽肉类及其制品-鸡1-nutrient.png](./docs/_readme_pics/禽肉类及其制品-鸡1-nutrient.png)

`禽肉类及其制品-鸡2-energy.png`:
![禽肉类及其制品-鸡2-energy.png](./docs/_readme_pics/禽肉类及其制品-鸡2-energy.png)

`禽肉类及其制品-鸡2-nutrient.png`:
![禽肉类及其制品-鸡2-nutrient.png](./docs/_readme_pics/禽肉类及其制品-鸡2-nutrient.png)

转换为单个`merged_禽肉类及其制品-鸡.json`文件

```json
[
  {
    "foodCode": "091101x",
    "foodName": "鸡（代表值）",
    "edible": "63",
    "water": "70.5",
    "energyKCal": "145",
    "energyKJ": "608",
    "protein": "20.3",
    "fat": "6.7",
    "CHO": "0.9",
    "dietaryFiber": "0.0",
    "cholesterol": "106",
    "ash": "1.1",
    "vitaminA": "92",
    "carotene": "0",
    "retinol": "92",
    "thiamin": "0.06",
    "riboflavin": "0.07",
    "niacin": "7.54",
    "vitaminC": "Tr",
    "vitaminETotal": "1.34",
    "vitaminE1": "1.34",
    "vitaminE2": "0.37",
    "vitaminE3": "0.10",
    "Ca": "13",
    "P": "166",
    "K": "249",
    "Na": "62.8",
    "Mg": "22",
    "Fe": "1.8",
    "Zn": "1.46",
    "Se": "11.92",
    "Cu": "0.09",
    "Mn": "0.05",
    "remark": ""
  },
    ……
]
```

## 各个栏位中文名称

energy 部分栏位中文名称:

![energy部分栏位中文名称](./docs/_readme_pics/energy部分栏位中文名称.png)

nutrient 部分栏位中文名称:

![nutrient部分栏位中文名称](./docs/_readme_pics/nutrient部分栏位中文名称.png)

## 运行

```sh
pip install -r requirements.txt   # openai / aiofiles / python-dotenv

# 1. 在 .env 配置两个视觉大模型的 API 密钥（参考 .env.example）
# 2. 按需调整 config_v3.json（模型清单、试点范围等）
# 3. 跑流水线（识别 + 解析合并 + 校验 + 复核报告）
python index_v3.py --stage all
# 4. 浏览器打开 review_v3/index.html 人工核对
# 5. 需要修正时：
cp review_v3/corrections_template.json review_v3/corrections.json
#    编辑 corrections.json（action: update/delete/insert）
python index_v3.py --stage apply

# 验证处理链路（可选，零 API 调用）
python utils_v3/test_v3_output.py

# 生成矫正版（可选）：修复原书能量 kcal/kJ 整列互换（野生蔬菜类 68 条）
python utils_v3/fix_energy_swap.py            # dry-run：仅报告
python utils_v3/fix_energy_swap.py --apply    # 生成/重建 {output_dir}_fixed/
```

也可以直接使用 `json_data_v3_20260825_qwen38max_kimi_k3`（1:1 识别版，与原书一致）
或 `json_data_v3_20260825_qwen38max_kimi_k3_fixed`（能量矫正版）文件夹中的 json 文件。

具体使用方法参看[V3版本方案说明](docs/README_V3.md)。

## 注意事项

### 数据可能存在逻辑问题，但是这可能就是原书籍就是这样

- **比如原书中 `蔬菜类及其制品-野生蔬菜类`（048004~048084），能量 kcal 和 kJ 数值明显是标反了的**
  - 为了保持和书籍一致，识别版 `json_data_v3_20260825_qwen38max_kimi_k3` 未修正，请严肃注意
  - 矫正版 `json_data_v3_20260825_qwen38max_kimi_k3_fixed` 已将两列交换
    - 68 条，`utils_v3/fix_energy_swap.py` 生成，明细见其中的 `energy_swap_fix_log.json`
- 比如 062101x 桃（代表值）kcal=42/kJ=212、066201x 西瓜（代表值）kcal=31/kJ=108
  - 经核对原书确实如此（比值不满足 4.184 换算），未做修改，使用时注意
- 比如 `畜肉类及其制品-牛 · 082115 牛肉（胸部肉）［牛胸］`
  - 水分 + 蛋白质 + 脂肪 + 碳水 + 灰分 =58.8+16.6+28.8+0.8= 105.0 ，远超100
  - 这个显得不合理，而且这样的数据非常多
- 目前我将 `config_v3.json` 中的 `thresholds` 配置得极大，忽略了这些内容
- 同样的还有微量元素的值，也在`support_v3/validation.py` 的 `RANGES` 中配置得很宽
  - 实际数据需要专业人员判断，这里优先保证识别的结果和书上原文一致

### 不保证数据识别的绝对一致

- 虽然经过双模型交叉 + 自动校验 + 人工复核后，可疑单元格均已定点核对过；
- 目前可能数据出错就是 qwen3.8-max 和 kimi-k3 的视觉识别都错了，还错得一样
- 若有余力可自行逐行校对，或者使用其他模型测试
  - 个人花了不少费用进行测试，**仅针对当前这个项目的需求**，对应的视觉大模型的识别效果:
  - kimi-k3 > qwen3.8-max > kimi-k2.7-code > [minimax-m3, qwen3.8-2.4t-a95b, qwen3.8-27b, qwen3.7-max-2026-06-08]
    - 后面那几个效果都比较差

### 迭代记录

- 2023-12-30 飞桨 OCR 路线（第一版）
- 2025-07-23 单视觉大模型 markdown 表格转录（第二版）
- 2025-12-06 更新了视觉大模型处理结果，移除了结构不一致的 婴幼儿食品部分，手动添加了GI数据
- 2026-08-25 双模型交叉 + 自动校验 + 人工复核验证（第三版）

### 版权声明

所有版权都归原作者所有，脚本仅用于个人学习研究，有任何必要的话请通知我删除此仓库。
