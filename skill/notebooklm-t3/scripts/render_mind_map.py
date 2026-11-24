#!/usr/bin/env python3
"""Compile NotebookLM Mind Map JSON into a T3 Code themed interactive collapsible tree HTML."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

MAX_DEPTH = 16


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _safe_inline_json(payload: Any) -> str:
    return (
        json.dumps(payload, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _count_raw_nodes(raw: Any) -> int:
    if not isinstance(raw, dict):
        return 0
    children = raw.get("children")
    sub = sum(_count_raw_nodes(c) for c in children) if isinstance(children, list) else 0
    return 1 + sub


def _normalize_node(raw: Any, stats: dict[str, int], depth: int = 0) -> dict[str, Any] | None:
    if depth > MAX_DEPTH:
        stats["truncated"] += _count_raw_nodes(raw) or 1
        return None
    if not isinstance(raw, dict):
        stats["skipped"] += 1
        return None
    name = _safe_str(raw.get("name") or raw.get("title") or raw.get("label") or raw.get("text"))
    raw_children = raw.get("children")
    children: list[dict[str, Any]] = []
    if isinstance(raw_children, list):
        for child in raw_children:
            norm = _normalize_node(child, stats, depth + 1)
            if norm is not None:
                children.append(norm)
    if not name and not children:
        stats["skipped"] += 1
        return None
    return {"name": name or "(未命名节点)", "children": children}


def _count_nodes(node: dict[str, Any]) -> int:
    return 1 + sum(_count_nodes(c) for c in node.get("children", []))


def load_tree(src: Path) -> tuple[dict[str, Any], dict[str, int]]:
    parsed = json.loads(src.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("Mind Map JSON root must be an object")
    root_raw = parsed.get("mind_map") if isinstance(parsed.get("mind_map"), dict) else parsed
    stats = {"skipped": 0, "truncated": 0}
    tree = _normalize_node(root_raw, stats)
    if tree is None or not tree.get("children"):
        raise ValueError(
            "Unrecognized or empty Mind Map JSON schema; fall back to nested Markdown outline"
        )
    return tree, stats


def build_html(tree: dict[str, Any]) -> str:
    safe_json = _safe_inline_json(tree)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>NotebookLM Mind Map</title>
<style>
  :root {{
    --c-fg: var(--foreground, #e6edf3);
    --c-muted: var(--muted-foreground, #8b949e);
    --c-card: var(--card, rgba(127, 127, 127, 0.08));
    --c-sec: var(--secondary, rgba(127, 127, 127, 0.14));
    --c-bd: var(--border, rgba(127, 127, 127, 0.28));
    --c-acc: var(--primary, var(--accent, #3b82f6));
    --c-acc-bg: var(--accent-surface, color-mix(in srgb, var(--c-acc) 14%, transparent));
    --c-1: var(--chart-1, #38bdf8);
    --c-2: var(--chart-2, #a855f7);
    --c-3: var(--chart-3, #10b981);
    --c-4: var(--chart-4, #f59e0b);
    --c-5: var(--chart-5, #ec4899);
    --c-6: var(--chart-6, #06b6d4);
    --c-warn-bg: var(--warning-surface, color-mix(in srgb, var(--warning, #eab308) 18%, transparent));
    --r: var(--radius, 10px);
    --font-ui: var(--font-sans, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif);
    --font-code: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  }}
  * {{ box-sizing: border-box; }}
  html, body {{
    margin: 0;
    padding: 0;
    background: transparent;
    color: var(--c-fg);
    font-family: var(--font-ui);
    font-size: 13.5px;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }}
  .root {{
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }}
  .head {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    flex-wrap: wrap;
  }}
  .head-left {{
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }}
  .tag {{
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.03em;
    text-transform: uppercase;
    padding: 2px 8px;
    border-radius: 999px;
    background: var(--c-acc-bg);
    color: var(--c-acc);
    flex-shrink: 0;
  }}
  .title {{
    font-size: 15px;
    font-weight: 600;
  }}
  .title.hit {{
    padding: 0 4px;
    border-radius: 4px;
    background: var(--c-warn-bg);
  }}
  .meta {{
    font-size: 12px;
    color: var(--c-muted);
    font-family: var(--font-code);
  }}
  .toolbar {{
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }}
  .search {{
    flex: 1;
    min-width: 180px;
    background: var(--c-card);
    color: var(--c-fg);
    border: 1px solid var(--c-bd);
    border-radius: 7px;
    padding: 6px 10px;
    font: inherit;
    font-size: 12.5px;
    outline: none;
  }}
  .search:focus {{ border-color: var(--c-acc); }}
  .btn {{
    appearance: none;
    border: 1px solid var(--c-bd);
    background: transparent;
    color: var(--c-fg);
    font: inherit;
    font-size: 12px;
    padding: 5px 10px;
    border-radius: 7px;
    cursor: pointer;
    white-space: nowrap;
  }}
  .btn:hover {{ border-color: var(--c-acc); }}
  .tree-wrap {{
    background: var(--c-card);
    border: 1px solid var(--c-bd);
    border-radius: var(--r);
    padding: 14px 16px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }}
  .branch {{
    display: flex;
    flex-direction: column;
    gap: 4px;
  }}
  .children {{
    margin-left: 16px;
    padding-left: 12px;
    border-left: 1px solid var(--c-bd);
    display: flex;
    flex-direction: column;
    gap: 4px;
  }}
  .row {{
    display: flex;
    align-items: flex-start;
    gap: 7px;
    padding: 4px 7px;
    border-radius: 6px;
    transition: background-color 120ms ease;
  }}
  .row.clickable {{ cursor: pointer; }}
  .row.clickable:hover {{ background: var(--c-sec); }}
  .row.hit {{
    background: var(--c-warn-bg);
  }}
  .chev {{
    width: 16px;
    height: 20px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: var(--c-muted);
    font-family: var(--font-code);
    font-size: 11px;
    flex-shrink: 0;
    user-select: none;
  }}
  .dot {{
    width: 8px;
    height: 8px;
    border-radius: 999px;
    margin-top: 6px;
    flex-shrink: 0;
  }}
  .label {{
    flex: 1;
    min-width: 0;
    word-break: break-word;
  }}
  .label.l1 {{ font-weight: 600; font-size: 14px; }}
  .label.l2 {{ font-weight: 500; }}
  .label.leaf {{ color: var(--c-fg); }}
  .cnt {{
    font-size: 11px;
    font-family: var(--font-code);
    color: var(--c-muted);
    padding: 0 6px;
    border-radius: 999px;
    background: var(--c-sec);
    flex-shrink: 0;
    margin-top: 2px;
  }}
  .empty {{
    font-size: 13px;
    color: var(--c-muted);
    padding: 8px 2px;
  }}
  .status {{
    font-size: 11.5px;
    color: var(--c-muted);
  }}
</style>
</head>
<body>
<div class="root">
  <div class="head">
    <div class="head-left">
      <span class="tag">Mind Map</span>
      <span id="mm-title" class="title"></span>
    </div>
    <div id="mm-meta" class="meta"></div>
  </div>
  <div class="toolbar">
    <input type="text" class="search" id="mm-search" placeholder="搜索节点关键词..." />
    <button type="button" class="btn" id="btn-expand">全部展开</button>
    <button type="button" class="btn" id="btn-default">默认视图</button>
    <button type="button" class="btn" id="btn-top">仅显示一级</button>
    <button type="button" class="btn" id="btn-copy">复制大纲</button>
  </div>
  <div id="mm-tree" class="tree-wrap"></div>
  <div id="mm-status" class="status" hidden></div>
</div>
<script>
const TREE = {safe_json};
const PALETTE = ['var(--c-1)', 'var(--c-2)', 'var(--c-3)', 'var(--c-4)', 'var(--c-5)', 'var(--c-6)'];

const titleEl = document.getElementById('mm-title');
const metaEl = document.getElementById('mm-meta');
const searchEl = document.getElementById('mm-search');
const treeEl = document.getElementById('mm-tree');
const statusEl = document.getElementById('mm-status');

function esc(s) {{
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
}}

let seq = 0;
function annotate(node, depth, branchIdx) {{
  const id = `n${{seq++}}`;
  const children = (node.children || []).map((c, i) =>
    annotate(c, depth + 1, depth === 0 ? i : branchIdx)
  );
  const count = 1 + children.reduce((acc, c) => acc + c.count, 0);
  return {{ id, name: node.name, depth, branchIdx, count, children }};
}}

const ROOT = annotate(TREE, 0, 0);
titleEl.textContent = ROOT.name;

const collapsed = new Set();
const searchCollapsed = new Set();

function applyDefaultCollapse() {{
  collapsed.clear();
  const walk = (node) => {{
    if (ROOT.count > 24 && node.depth >= 2 && node.children.length > 0) {{
      collapsed.add(node.id);
    }}
    node.children.forEach(walk);
  }};
  walk(ROOT);
}}
applyDefaultCollapse();

let query = '';
let isComposing = false;

function filterMatch(node, q) {{
  const selfHit = q ? node.name.toLowerCase().includes(q) : false;
  const childMatches = node.children.map((c) => filterMatch(c, q)).filter(Boolean);
  if (!q || selfHit || childMatches.length > 0) {{
    const visibleDesc = childMatches.reduce((acc, c) => acc + 1 + c.visibleDesc, 0);
    return {{ node, selfHit, children: childMatches, visibleDesc }};
  }}
  return null;
}}

function renderNode(entry, searching) {{
  const {{ node, selfHit, children, visibleDesc }} = entry;
  const hasKids = children.length > 0;
  const collapseSet = searching ? searchCollapsed : collapsed;
  const isCollapsed = collapseSet.has(node.id);
  const color = PALETTE[node.branchIdx % PALETTE.length];
  const lblCls = node.depth === 1 ? 'label l1' : (hasKids ? 'label l2' : 'label leaf');
  const chev = hasKids ? (isCollapsed ? '+' : '-') : '·';

  const kidsHtml = hasKids && !isCollapsed
    ? `<div class="children">${{children.map((c) => renderNode(c, searching)).join('')}}</div>`
    : '';

  return `
    <div class="branch">
      <div class="row ${{hasKids ? 'clickable' : ''}} ${{selfHit ? 'hit' : ''}}" ${{hasKids ? `data-toggle="${{node.id}}"` : ''}}>
        <span class="chev">${{chev}}</span>
        <span class="dot" style="background:${{color}}"></span>
        <span class="${{lblCls}}">${{esc(node.name)}}</span>
        ${{hasKids ? `<span class="cnt">${{visibleDesc}}</span>` : ''}}
      </div>
      ${{kidsHtml}}
    </div>`;
}}

function toMarkdown(node, indent = 0) {{
  const prefix = indent === 0 ? '# ' : `${{'  '.repeat(indent - 1)}}- `;
  const lines = [`${{prefix}}${{node.name}}`];
  for (const c of node.children) lines.push(toMarkdown(c, indent + 1));
  return lines.join('\\n');
}}

function setStatus(msg) {{
  if (!msg) {{
    statusEl.hidden = true;
    statusEl.textContent = '';
  }} else {{
    statusEl.hidden = false;
    statusEl.textContent = msg;
  }}
}}

function renderTree() {{
  const q = query.trim().toLowerCase();
  const searching = Boolean(q);
  const matchedRoot = filterMatch(ROOT, q);
  titleEl.className = matchedRoot && matchedRoot.selfHit ? 'title hit' : 'title';

  if (searching && matchedRoot && matchedRoot.selfHit && matchedRoot.children.length === 0) {{
    // Root matched directly: show all branches so user can inspect under root.
    const fullRoot = filterMatch(ROOT, '');
    metaEl.textContent = `命中根节点 · ${{ROOT.count - 1}} 节点`;
    treeEl.innerHTML = fullRoot.children.map((c) => renderNode(c, true)).join('');
    return;
  }}

  const branches = matchedRoot ? matchedRoot.children : [];
  if (searching) {{
    const shown = matchedRoot ? matchedRoot.visibleDesc : 0;
    metaEl.textContent = `匹配 ${{shown}}/${{ROOT.count - 1}} 节点`;
  }} else {{
    metaEl.textContent = `${{ROOT.children.length}} 分支 · ${{ROOT.count - 1}} 节点`;
  }}

  treeEl.innerHTML = branches.length
    ? branches.map((c) => renderNode(c, searching)).join('')
    : '<div class="empty">无匹配节点</div>';
}}

treeEl.addEventListener('click', (e) => {{
  const row = e.target.closest('[data-toggle]');
  if (!row) return;
  const id = row.dataset.toggle;
  const targetSet = query.trim() ? searchCollapsed : collapsed;
  if (targetSet.has(id)) targetSet.delete(id);
  else targetSet.add(id);
  renderTree();
}});

searchEl.addEventListener('compositionstart', () => {{
  isComposing = true;
}});
searchEl.addEventListener('compositionend', (e) => {{
  isComposing = false;
  query = e.target.value;
  searchCollapsed.clear();
  setStatus('');
  renderTree();
}});
searchEl.addEventListener('input', (e) => {{
  if (isComposing || e.isComposing) return;
  query = e.target.value;
  searchCollapsed.clear();
  setStatus('');
  renderTree();
}});

document.getElementById('btn-expand').addEventListener('click', () => {{
  (query.trim() ? searchCollapsed : collapsed).clear();
  renderTree();
}});
document.getElementById('btn-default').addEventListener('click', () => {{
  searchCollapsed.clear();
  applyDefaultCollapse();
  renderTree();
}});
document.getElementById('btn-top').addEventListener('click', () => {{
  const targetSet = query.trim() ? searchCollapsed : collapsed;
  targetSet.clear();
  const walk = (n) => {{
    if (n.depth >= 1 && n.children.length > 0) targetSet.add(n.id);
    n.children.forEach(walk);
  }};
  walk(ROOT);
  renderTree();
}});
document.getElementById('btn-copy').addEventListener('click', async () => {{
  try {{
    await navigator.clipboard.writeText(toMarkdown(ROOT));
    setStatus('已复制 Markdown 大纲到剪贴板');
  }} catch {{
    setStatus('当前环境未授权剪贴板写入');
  }}
}});

renderTree();
</script>
</body>
</html>
"""


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: render_mind_map.py <input.json> <output.html>", file=sys.stderr)
        return 2
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    try:
        tree, stats = load_tree(src)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if stats["skipped"] > 0 or stats["truncated"] > 0:
        print(
            f"WARN: skipped {stats['skipped']} invalid node(s), truncated {stats['truncated']} node(s) deeper than {MAX_DEPTH}",
            file=sys.stderr,
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(build_html(tree), encoding="utf-8")
    print(f"OK: rendered mind-map ({_count_nodes(tree) - 1} nodes) -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
