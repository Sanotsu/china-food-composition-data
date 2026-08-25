#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段4：人工复核报告生成模块（web项目版）

生成到 {temp_dir}/review/ 下的静态web项目：
  index.html        页面骨架（固定）
  css/style.css     样式（固定）
  js/app.js         渲染逻辑（固定）
  js/data.js        数据源（每次由校验结果生成；换数据只需替换此文件）
  corrections_template.json  修正文件模板

视图设计（图片镜像）：
  每个图片对（energy+nutrient）一个单元，每张图片上方放一个 1:1 镜像表格：
  - 行序与图片一致；双模型一致的行显示单行（✓）
  - 字段不一致的行拆为两行（模型A行、模型B行，行序为配置文件第一、二个模型），差异值加粗标红
  - 某模型漏识别的行，另一侧显示"— 该模型无此行 —"
"""
import html as _html
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .config import ENERGY_COLS, NUTRIENT_COLS, MERGED_FIELD_ORDER, AppConfig
from .recognition import collect_recognized_pairs
from .validation import values_equal

LEVEL_STYLE = {
    "error": ("#c0392b", "#fdecea"),
    "warning": ("#b7791f", "#fdf6e3"),
    "review": ("#7048e8", "#f3f0ff"),
}

INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>数据复核</title>
<link rel="stylesheet" href="css/style.css">
</head>
<body>
<header>
  <h1>数据复核（识别 + 自动校验 + 定点人工复核）</h1>
  <div class="sub" id="sub"></div>
</header>
<nav>
  <a href="#issues">问题清单</a>
  <a href="#data">完整数据（图片镜像）</a>
  <button id="btn-expand" class="navbtn" type="button">全部展开</button>
  <button id="btn-collapse" class="navbtn" type="button">全部折叠</button>
</nav>
<div class="stats" id="stats"></div>
<div class="toolbar">
  提示：完整数据区中每张图片上方是 1:1 镜像表格——行序与图片一致；
  双模型一致的行显示单行(✓)；不一致的行拆为两行（行序为配置文件第一、二个模型），红色加粗值即分歧点；
  某模型漏识别的行另一侧显示"该模型无此行"。绿色统计卡表示 foodName 仅全半角标点/空格差异、已自动统一。
</div>
<main id="issues"></main>
<section id="data">
  <h2 class="sech2" id="data-title"></h2>
  <div id="data-body"></div>
</section>
<script src="js/app.js"></script>
</body>
</html>
"""

STYLE_CSS = """
body { font-family: "Noto Sans CJK SC", "Microsoft YaHei", sans-serif;
       margin: 0; background: #f0f2f5; color: #333; }
header { background: #1f2937; color: #fff; padding: 18px 28px; }
header h1 { margin: 0 0 6px; font-size: 20px; }
header .sub { color: #9ca3af; font-size: 13px; }
nav { background: #111827; padding: 8px 28px; display: flex; align-items: center; gap: 22px; }
nav a { color: #93c5fd; text-decoration: none; font-size: 14px; }
nav a:hover { text-decoration: underline; }
.navbtn { background: #1f2937; color: #cbd5e1; border: 1px solid #374151;
          border-radius: 4px; padding: 3px 10px; font-size: 12px; cursor: pointer; }
.navbtn:hover { background: #374151; }
.stats { display: flex; gap: 14px; padding: 18px 28px; flex-wrap: wrap; }
.stat { flex: 0 0 140px; border-radius: 10px; padding: 14px; text-align: center; }
.stat .num { font-size: 30px; font-weight: 700; }
.stat .lbl { font-size: 13px; color: #666; }
.toolbar { padding: 0 28px 12px; font-size: 13px; color: #666; }
main, section#data { padding: 0 28px 40px; }
main { display: flex; flex-direction: column; gap: 14px; }
.sech2 { font-size: 17px; margin: 26px 0 8px; }
.card { background: #fff; border-radius: 10px; padding: 14px 18px;
        box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.cardhead { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.badge { color: #fff; border-radius: 4px; font-size: 12px; padding: 2px 8px; }
.badge.gray { background: #9ca3af; }
.type { background: #eef1f4; border-radius: 4px; padding: 2px 8px; font-size: 12px; }
.meta { color: #666; font-size: 13px; }
.detail { margin: 10px 0; font-size: 14px; }
.kv { border-collapse: collapse; font-size: 13px; margin-bottom: 8px; }
.kv th { text-align: left; background: #f7f8fa; padding: 4px 10px;
         border: 1px solid #e5e7eb; white-space: nowrap; }
.kv td { padding: 4px 10px; border: 1px solid #e5e7eb; }
.mono { font-family: "JetBrains Mono", Consolas, monospace; }
.diff { color: #c0392b; font-weight: 700; }
.mm { color: #c0392b; font-weight: 700; background: #fdecea; }
.lvl-error { color: #c0392b; }
.lvl-warning { color: #b7791f; }
.lvl-review { color: #7048e8; }
details.catgroup { background: #fff; border-radius: 10px; margin: 10px 0;
                   box-shadow: 0 1px 3px rgba(0,0,0,.08); padding: 10px 16px; }
details.catgroup > summary { cursor: pointer; font-size: 15px; user-select: none; }
.catcnt { color: #888; font-size: 13px; font-weight: 400; }
details.pair { border: 1px solid #e5e7eb; border-radius: 8px; margin: 10px 0; padding: 8px 14px; }
details.pair > summary { cursor: pointer; font-size: 14px; user-select: none; }
.pairbody { padding: 6px 0; }
.pagetitle { font-size: 13px; color: #555; margin: 14px 0 4px; font-weight: 600; }
.tblwrap { overflow-x: auto; margin-top: 4px; }
.mirtbl { border-collapse: collapse; font-size: 12px; white-space: nowrap; }
.mirtbl th, .mirtbl td { border: 1px solid #e5e7eb; padding: 3px 8px; }
.mirtbl th { background: #f7f8fa; position: sticky; left: 0; z-index: 1; }
.mirtbl tr th:first-child { background: #eef1f4; font-weight: 600; }
.rowtag { font-size: 11px; padding: 1px 6px; border-radius: 3px; color: #fff; white-space: nowrap; }
.rowtag.ok { background: #2e7d32; }
.rowtag.a { background: #1d4ed8; }
.rowtag.b { background: #b45309; }
.rowtag.miss { background: #9ca3af; }
.missrow td { color: #999; font-style: italic; background: #fafafa; }
.imgwrap { margin: 8px 0 4px; }
.imgtitle { font-size: 12px; color: #888; margin-bottom: 4px; }
.imgwrap img { max-width: 100%; max-height: 560px; border: 1px solid #ddd;
               border-radius: 6px; display: block; }
"""

APP_JS = """
'use strict';
// 动态加载 data.js（带时间戳防浏览器缓存），加载完成后渲染。
// 好处：index.html/app.js 固定不变，数据每次执行流水线自动更新到 js/data.js。
(function () {
  var s = document.createElement('script');
  s.src = 'js/data.js?t=' + Date.now();
  s.onload = function () { render(); };
  s.onerror = function () {
    document.getElementById('data-body').textContent =
      'js/data.js 加载失败：请先执行 python index_v3_pipeline.py --stage report 生成数据';
  };
  document.head.appendChild(s);
})();

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function render() {
  const D = window.REVIEW_DATA;

  function renderSub() {
    document.getElementById('sub').textContent =
      '生成时间 ' + D.generated_at + ' · 交叉模型 ' + D.models.join(' vs ') +
      ' · 问题总数 ' + D.total_issues;
  }

  function renderStats() {
    const box = document.getElementById('stats');
    const items = [
      ['error', '#c0392b', '#fdecea'], ['warning', '#b7791f', '#fdf6e3'],
      ['review', '#7048e8', '#f3f0ff']];
    let h = '';
    for (const [k, c, bg] of items) {
      if (D.summary[k] == null) continue;
      h += '<div class="stat" style="background:' + bg + '">' +
           '<div class="num" style="color:' + c + '">' + D.summary[k] + '</div>' +
           '<div class="lbl">' + k + '</div></div>';
    }
    if (D.summary.foodname_auto_unified) {
      h += '<div class="stat" style="background:#e8f5e9">' +
           '<div class="num" style="color:#2e7d32">' + D.summary.foodname_auto_unified + '</div>' +
           '<div class="lbl">名称已自动统一<br>(仅全半角标点/空格差异)</div></div>';
    }
    h += '<div class="stat" style="background:#eef1f4">' +
         '<div class="num">' + D.summary.total_records + '</div>' +
         '<div class="lbl">总记录数</div></div>';
    h += '<div class="stat" style="background:#e8f5e9">' +
         '<div class="num" style="color:#2e7d32">' + D.summary.match_pairs + '</div>' +
         '<div class="lbl">完全一致图片对</div></div>';
    box.innerHTML = h;
  }

  function renderIssues() {
    const box = document.getElementById('issues');
    if (!D.issues.length) {
      box.innerHTML = '<div class="card">没有任何问题，全部通过校验。</div>';
      return;
    }
    const lvlColor = { error: '#c0392b', warning: '#b7791f', review: '#7048e8' };
    box.innerHTML = D.issues.map(iss => {
      const color = lvlColor[iss.level] || '#555';
      let kv = '';
      if (iss.type === 'model_disagreement') {
        kv = '<table class="kv">' +
          '<tr><th>字段</th><td class="mono">' + esc(iss.field) + '</td></tr>' +
          '<tr><th>' + esc(D.models[0]) + ' 值</th><td class="mono">' + esc(iss.value) + '</td></tr>' +
          '<tr><th>' + esc(D.models[1] || 'model_b') + ' 值</th><td class="mono">' + esc(iss.value_b) + '</td></tr>' +
          '</table>';
      }
      const link = iss.category ?
        '<a class="mono srclink" href="#cat-' + encodeURIComponent(iss.category) + '">定位到数据区 →</a>' : '';
      return '<div class="card" style="border-left:4px solid ' + color + '">' +
        '<div class="cardhead">' +
        '<span class="badge" style="background:' + color + '">' + iss.level.toUpperCase() + '</span>' +
        '<span class="badge gray">#' + iss.id + '</span>' +
        '<span class="type mono">' + esc(iss.type) + '</span>' +
        '<span class="meta">' + esc(iss.model) + ' · ' + esc(iss.category) +
        ' · <span class="mono">' + esc(iss.foodCode) + '</span> ' + esc(iss.foodName) + '</span></div>' +
        '<div class="detail">' + esc(iss.detail) + '</div>' + kv + link + '</div>';
    }).join('');
  }

  function rowCells(rec, cols, diffs) {
    let h = '';
    for (const c of cols) {
      const v = rec ? rec[c] : '';
      const cls = (diffs && diffs.indexOf(c) >= 0) ? ' class="mm"' : '';
      h += '<td' + cls + '><span class="mono">' + esc(v) + '</span></td>';
    }
    return h;
  }

  function missRow(cols, tagLabel) {
    return '<tr class="missrow"><td><span class="rowtag miss">' + esc(tagLabel) + '</span></td>' +
           '<td colspan="' + cols.length + '">— 该模型无此行 —</td></tr>';
  }

  function renderPageTable(page, tags) {
    const cols = page.columns;
    let h = '<div class="tblwrap"><table class="mirtbl">' +
            '<tr><th>行</th>' + cols.map(c => '<th>' + esc(c) + '</th>').join('') + '</tr>';
    let idx = 0;
    for (const r of page.rows) {
      idx += 1;
      if (r.kind === 'match') {
        h += '<tr><td><span class="rowtag ok">✓' + idx + '</span></td>' + rowCells(r.a, cols, null) + '</tr>';
      } else if (r.kind === 'diff') {
        h += '<tr><td><span class="rowtag a">' + esc(tags[0]) + ' #' + idx + '</span></td>' + rowCells(r.a, cols, r.diffs) + '</tr>';
        h += '<tr><td><span class="rowtag b">' + esc(tags[1]) + ' #' + idx + '</span></td>' + rowCells(r.b, cols, r.diffs) + '</tr>';
      } else if (r.kind === 'only_a') {
        h += '<tr><td><span class="rowtag a">' + esc(tags[0]) + ' #' + idx + '</span></td>' + rowCells(r.a, cols, null) + '</tr>';
        h += missRow(cols, tags[1]);
      } else {
        h += missRow(cols, tags[0]);
        h += '<tr><td><span class="rowtag b">' + esc(tags[1]) + ' #' + idx + '</span></td>' + rowCells(r.b, cols, null) + '</tr>';
      }
    }
    h += '</table></div>';
    return h;
  }

  function renderData() {
    document.getElementById('data-title').textContent =
      '完整数据复核（图片镜像：全部 ' + D.summary.total_records + ' 条，' +
      D.summary.pair_total + ' 个图片对，其中 ' + D.summary.pair_mismatch + ' 对存在模型间差异）';

    const body = document.getElementById('data-body');
    body.innerHTML = D.categories.map(cat => {
      const pairHtml = cat.pairs.map(p => {
        const hasDiff = p.energy.rows.some(r => r.kind !== 'match') ||
                        p.nutrient.rows.some(r => r.kind !== 'match');
        const open = hasDiff ? ' open' : '';
        const badge = hasDiff
          ? '<span class="badge" style="background:#d97706">存在差异</span>'
          : '<span class="badge" style="background:#2e7d32">双模型一致</span>';
        const mkPage = (page, label) => page
          ? '<div class="pagetitle">' + label + '</div>' +
            renderPageTable(page, D.models) +
            '<div class="imgwrap"><div class="imgtitle">' + esc(p.images[label === 'energy 部分' ? 'energy' : 'nutrient']) + '</div>' +
            '<img loading="lazy" src="' + esc(p.images[label === 'energy 部分' ? 'energy' : 'nutrient']) + '" alt=""></div>'
          : '';
        return '<details class="pair"' + open + '>' +
          '<summary>' + badge + ' <b>' + esc(p.base_name) + '</b></summary>' +
          '<div class="pairbody">' + mkPage(p.energy, 'energy 部分') + mkPage(p.nutrient, 'nutrient 部分') + '</div>' +
          '</details>';
      }).join('');
      return '<details class="catgroup" id="cat-' + encodeURIComponent(cat.name) + '">' +
        '<summary><b>' + esc(cat.name) + '</b> <span class="catcnt">(' +
        cat.pairs.length + ' 个图片对)</span></summary>' + pairHtml + '</details>';
    }).join('');
  }

  function bindNav() {
    document.getElementById('btn-expand').onclick = () =>
      document.querySelectorAll('details').forEach(d => d.open = true);
    document.getElementById('btn-collapse').onclick = () =>
      document.querySelectorAll('details').forEach(d => d.open = false);
  }

  renderSub();
  renderStats();
  renderIssues();
  renderData();
  bindNav();
}
"""


def _rel_img_from_review(cfg: AppConfig, rel_img_path: str) -> str:
    """计算 review/index.html 到原图的相对路径"""
    if not rel_img_path:
        return ""
    img_abs = (cfg.input_dir / rel_img_path).resolve()
    try:
        return os.path.relpath(img_abs, cfg.review_dir.resolve())
    except ValueError:
        return img_abs.as_uri()


def _load_pair_records(cfg: AppConfig, tag: str, base_name: str) -> List[Dict]:
    """读取某模型某图片对的合并记录（行序=foodCode升序=图片行序）"""
    path = cfg.parsed_dir / tag / "pairs" / f"{base_name}.json"
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _align_by_code(rows_a: List[Dict], rows_b: List[Dict]) -> List[Dict]:
    """按 foodCode 做 LCS 行对齐

    Returns:
        [{kind: match|diff|only_a|only_b, a, b, diffs}]
    """
    codes_a = [str(r.get("foodCode", "")) for r in rows_a]
    codes_b = [str(r.get("foodCode", "")) for r in rows_b]
    n, m = len(codes_a), len(codes_b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if codes_a[i] == codes_b[j]:
                dp[i][j] = dp[i + 1][j + 1] + 1
            else:
                dp[i][j] = max(dp[i + 1][j], dp[i][j + 1])

    out: List[Dict] = []
    i = j = 0
    while i < n and j < m:
        if codes_a[i] == codes_b[j]:
            out.append({"kind": "both", "a": rows_a[i], "b": rows_b[j]})
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            out.append({"kind": "only_a", "a": rows_a[i], "b": None})
            i += 1
        else:
            out.append({"kind": "only_b", "a": None, "b": rows_b[j]})
            j += 1
    while i < n:
        out.append({"kind": "only_a", "a": rows_a[i], "b": None})
        i += 1
    while j < m:
        out.append({"kind": "only_b", "a": None, "b": rows_b[j]})
        j += 1
    return out


def _build_page(rows_aligned: List[Dict], page_cols: List[str]) -> Dict:
    """将对齐行转为镜像表格页结构（diff判定排除remark）"""
    result_rows = []
    for r in rows_aligned:
        if r["kind"] == "both":
            a, b = r["a"], r["b"]
            diffs = [c for c in page_cols
                     if c != "remark" and not values_equal(str(a.get(c, "")), str(b.get(c, "")))]
            if diffs:
                result_rows.append({"kind": "diff", "a": a, "b": b, "diffs": diffs})
            else:
                result_rows.append({"kind": "match", "a": a, "b": b, "diffs": []})
        elif r["kind"] == "only_a":
            result_rows.append({"kind": "only_a", "a": r["a"], "b": None, "diffs": []})
        else:
            result_rows.append({"kind": "only_b", "a": None, "b": r["b"], "diffs": []})
    return {"columns": page_cols, "rows": result_rows}


def _build_data(cfg: AppConfig, report: Dict) -> Dict:
    """组装 data.js 的数据结构"""
    tags = report.get("models", [])
    tag_a = tags[0] if tags else "model_a"
    tag_b = tags[1] if len(tags) > 1 else ""
    # 数据区覆盖 raw 中已累积识别的全部图片对（与阶段2/3范围一致，不受 pilot 限制）
    pairs = collect_recognized_pairs(cfg)

    # 类别 -> 图片对 分组（保持文件顺序）
    cat_map: Dict[str, List] = {}
    for pair in pairs:
        cat_map.setdefault(pair.category, []).append(pair)

    categories = []
    pair_total = pair_match = 0
    total_records = 0
    for category, plist in sorted(cat_map.items()):
        pair_entries = []
        for pair in plist:
            recs_a = _load_pair_records(cfg, tag_a, pair.base_name)
            recs_b = _load_pair_records(cfg, tag_b, pair.base_name) if tag_b else []
            total_records += max(len(recs_a), len(recs_b))

            aligned = _align_by_code(recs_a, recs_b)
            energy_page = _build_page(aligned, ENERGY_COLS)
            nutrient_page = _build_page(aligned, NUTRIENT_COLS)
            clean = all(r["kind"] == "match" for r in energy_page["rows"]) and \
                    all(r["kind"] == "match" for r in nutrient_page["rows"])
            pair_total += 1
            pair_match += 1 if clean else 0

            pair_entries.append({
                "base_name": pair.base_name,
                "images": {
                    "energy": _rel_img_from_review(cfg, f"{pair.rel_dir}/{pair.energy_path.name}"),
                    "nutrient": _rel_img_from_review(cfg, f"{pair.rel_dir}/{pair.nutrient_path.name}"),
                },
                "energy": energy_page,
                "nutrient": nutrient_page,
            })
        categories.append({"name": category, "pairs": pair_entries})

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "models": tags,
        "summary": {
            **{k: v for k, v in report.get("summary", {}).items()},
            "total_records": total_records,
            "pair_total": pair_total,
            "pair_mismatch": pair_total - pair_match,
            "match_pairs": pair_match,
        },
        "total_issues": report.get("total_issues", 0),
        "issues": report.get("issues", []),
        "categories": categories,
    }


def _gen_corrections_template(report: Dict) -> List[Dict]:
    """生成修正文件模板：每条 error/review 生成一个预填定位信息的条目"""
    template = []
    for iss in report.get("issues", []):
        if iss["level"] not in ("error", "review"):
            continue
        template.append({
            "issue_id": iss["id"],
            "category": iss.get("category", ""),
            "foodCode": str(iss.get("foodCode", "")),
            "field": iss.get("field", ""),
            "action": "update",
            "value": "",
            "_comment": f"[{iss['level']}#{iss['id']}] {iss.get('type','')}: {iss.get('detail','')[:120]}",
        })
    return template


def run_report(cfg: AppConfig, report: Dict) -> Path:
    """执行阶段4：生成web项目（index.html/css/js + data.js）与修正模板"""
    cfg.review_dir.mkdir(parents=True, exist_ok=True)
    (cfg.review_dir / "css").mkdir(exist_ok=True)
    (cfg.review_dir / "js").mkdir(exist_ok=True)

    # 固定的web骨架（幂等覆盖；自定义样式后不想被覆盖可直接改生成后的文件）
    (cfg.review_dir / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (cfg.review_dir / "css" / "style.css").write_text(STYLE_CSS, encoding="utf-8")
    (cfg.review_dir / "js" / "app.js").write_text(APP_JS, encoding="utf-8")

    # 数据源（每次生成）
    data = _build_data(cfg, report)
    data_js = "window.REVIEW_DATA = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n"
    data_path = cfg.review_dir / "js" / "data.js"
    data_path.write_text(data_js, encoding="utf-8")

    tpl_path = cfg.review_dir / "corrections_template.json"
    with open(tpl_path, "w", encoding="utf-8") as f:
        json.dump(_gen_corrections_template(report), f, ensure_ascii=False, indent=2)

    s = data["summary"]
    print(f"复核web项目已生成: {cfg.review_dir}/index.html")
    print(f"  数据源: {data_path}（换数据只需替换此文件）")
    print(f"  图片对 {s['pair_total']}（一致 {s['match_pairs']} / 差异 {s['pair_mismatch']}），"
          f"共 {s['total_records']} 条记录")
    print(f"修正文件模板已生成: {tpl_path}")
    print("人工流程: 打开index.html核对 -> 复制模板为 corrections.json 并填写 -> 运行阶段5 apply")
    return cfg.review_dir / "index.html"
