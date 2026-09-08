#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""中英文对照表阶段4：人工复核报告生成模块（web项目版）

生成到 review_dir 下的静态web项目：
  index.html        页面骨架（固定）
  css/style.css     样式（固定）
  js/app.js         渲染逻辑（固定）
  js/data.js        数据源（每次由校验结果生成；换数据只需替换此文件）
  corrections_template.json  修正文件模板

视图设计（图片镜像）：
  每张截图一个单元，图片上方放一个 1:1 镜像表格（3列，行序与图片一致）：
  - 双模型一致的行显示单行（✓）
  - 字段不一致的行拆为两行（模型A行、模型B行），差异值加粗标红
  - 某模型漏识别的行，另一侧显示"— 该模型无此行 —"
复用 support_v3.review_report 的 LCS 行对齐、镜像页构建、图片路径与修正模板逻辑。
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from support_v3.review_report import (
    STYLE_CSS, _align_by_code, _build_page, _gen_corrections_template,
    _rel_img_from_review,
)
from support_v3_en.config import EN_COLS
from support_v3_en.recognition import collect_recognized_images

INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>中英文对照表数据复核</title>
<link rel="stylesheet" href="css/style.css">
</head>
<body>
<header>
  <h1>中英文对照表数据复核（识别 + foodCode交叉核对 + 定点人工复核）</h1>
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
  某模型漏识别的行另一侧显示"该模型无此行"。
  交叉核对卡片为与营养素成果目录比对的 foodCode 集合差异（信息性提示）：
  原书营养素表与中英文对照表本就不是一一对应（部分条目无英文名），差异明细见
  temp 目录 validation/cross_check.json，不属于识别问题。
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
      'js/data.js 加载失败：请先执行 python index_v3_en.py --stage report 生成数据';
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
    h += '<div class="stat" style="background:#eef1f4">' +
         '<div class="num">' + D.summary.total_records + '</div>' +
         '<div class="lbl">总记录数</div></div>';
    h += '<div class="stat" style="background:#e8f5e9">' +
         '<div class="num" style="color:#2e7d32">' + D.summary.match_images + '</div>' +
         '<div class="lbl">双模型一致图片</div></div>';
    if (D.summary.cross_nutrition_total != null) {
      h += '<div class="stat" style="background:' +
           (D.summary.cross_en_only ? '#fff3e0' : '#e8f5e9') + '">' +
           '<div class="num" style="color:' +
           (D.summary.cross_en_only ? '#b7791f' : '#2e7d32') + '">' +
           D.summary.cross_en_only + '</div>' +
           '<div class="lbl">交叉核对<br>EN独有foodCode</div></div>';
      h += '<div class="stat" style="background:' +
           (D.summary.cross_missing_en ? '#fff3e0' : '#e8f5e9') + '">' +
           '<div class="num" style="color:' +
           (D.summary.cross_missing_en ? '#b7791f' : '#2e7d32') + '">' +
           D.summary.cross_missing_en + '</div>' +
           '<div class="lbl">交叉核对<br>EN缺失foodCode</div></div>';
    }
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
      D.summary.image_total + ' 张图片，其中 ' + D.summary.image_mismatch + ' 张存在模型间差异）';

    const body = document.getElementById('data-body');
    body.innerHTML = D.categories.map(cat => {
      const imgHtml = cat.images.map(p => {
        const hasDiff = p.page.rows.some(r => r.kind !== 'match');
        const open = hasDiff ? ' open' : '';
        const badge = hasDiff
          ? '<span class="badge" style="background:#d97706">存在差异</span>'
          : '<span class="badge" style="background:#2e7d32">双模型一致</span>';
        return '<details class="pair"' + open + '>' +
          '<summary>' + badge + ' <b>' + esc(p.base_name) + '</b></summary>' +
          '<div class="pairbody">' +
          renderPageTable(p.page, D.models) +
          '<div class="imgwrap"><div class="imgtitle">' + esc(p.image) + '</div>' +
          '<img loading="lazy" src="' + esc(p.image) + '" alt=""></div>' +
          '</div></details>';
      }).join('');
      return '<details class="catgroup" id="cat-' + encodeURIComponent(cat.name) + '">' +
        '<summary><b>' + esc(cat.name) + '</b> <span class="catcnt">(' +
        cat.images.length + ' 张图片)</span></summary>' + imgHtml + '</details>';
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


def _load_image_records(cfg, tag: str, base_name: str) -> List[Dict]:
    """读取某模型某图片的识别记录（原行序=图片行序）"""
    path = cfg.parsed_dir / tag / "images" / f"{base_name}.json"
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _build_data(cfg, report: Dict) -> Dict:
    """组装 data.js 的数据结构"""
    tags = report.get("models", [])
    tag_a = tags[0] if tags else "model_a"
    tag_b = tags[1] if len(tags) > 1 else ""
    # 数据区覆盖 raw 中已累积识别的全部图片（与阶段2/3范围一致，不受 pilot 限制）
    images = collect_recognized_images(cfg)

    cat_map: Dict[str, List] = {}
    for img in images:
        cat_map.setdefault(img.category, []).append(img)

    categories = []
    image_total = image_match = 0
    total_records = 0
    for category, imglist in sorted(cat_map.items()):
        img_entries = []
        for img in imglist:
            recs_a = _load_image_records(cfg, tag_a, img.base_name)
            recs_b = _load_image_records(cfg, tag_b, img.base_name) if tag_b else []
            total_records += max(len(recs_a), len(recs_b))

            aligned = _align_by_code(recs_a, recs_b)
            page = _build_page(aligned, EN_COLS)
            clean = all(r["kind"] == "match" for r in page["rows"])
            image_total += 1
            image_match += 1 if clean else 0

            img_entries.append({
                "base_name": img.base_name,
                "image": _rel_img_from_review(cfg, f"{img.rel_dir}/{img.image_path.name}"),
                "page": page,
            })
        categories.append({"name": category, "images": img_entries})

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "models": tags,
        "summary": {
            **{k: v for k, v in report.get("summary", {}).items()},
            "total_records": total_records,
            "image_total": image_total,
            "image_mismatch": image_total - image_match,
            "match_images": image_match,
        },
        "total_issues": report.get("total_issues", 0),
        "issues": report.get("issues", []),
        "categories": categories,
    }


def run_report(cfg, report: Dict) -> Path:
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
    print(f"  图片 {s['image_total']}（一致 {s['match_images']} / 差异 {s['image_mismatch']}），"
          f"共 {s['total_records']} 条记录")
    if s.get("cross_nutrition_total") is not None:
        print(f"  交叉核对: EN独有 {s.get('cross_en_only')} / EN缺失 {s.get('cross_missing_en')}")
    print(f"修正文件模板已生成: {tpl_path}")
    print("人工流程: 打开index.html核对 -> 复制模板为 corrections.json 并填写 -> 运行阶段5 apply")
    return cfg.review_dir / "index.html"
