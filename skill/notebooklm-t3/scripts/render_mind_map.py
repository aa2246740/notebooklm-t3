#!/usr/bin/env python3
"""将 NotebookLM Mind Map JSON 编译为对标原生 NotebookLM 体验的交互式横向思维导图（单文件离线 HTML，适配 T3 亮/暗主题）。"""

from __future__ import annotations

import html as html_lib
import json
import sys
from pathlib import Path
from typing import Any

MAX_DEPTH = 16
STAGE_HEIGHT_DESKTOP = 520


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


def _render_static_outline_ul(node: dict[str, Any]) -> str:
    """渐进增强静态降级：无 JS 时依然可完整阅读已转义树形大纲。"""
    label = html_lib.escape(node.get("name", ""), quote=True)
    children = node.get("children") or []
    if not children:
        return f"<li>{label}</li>"
    inner = "".join(_render_static_outline_ul(c) for c in children)
    return f"<li><span>{label}</span><ul>{inner}</ul></li>"


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
    static_title = html_lib.escape(tree.get("name", "Mind Map"), quote=True)
    static_outline = f"<ul class=\"static-outline\">{_render_static_outline_ul(tree)}</ul>"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>NotebookLM Mind Map</title>
<style>
  :root {{
    color-scheme: light dark;
    --fb-bg: #f8f9fb;
    --fb-card: #ffffff;
    --fb-fg: #18181b;
    --fb-muted: #52525b;
    --fb-sec: #f1f5f9;
    --fb-bd: #d4d4d8;
    --fb-acc: #4f46e5;
    --fb-acc-fg: #ffffff;
    --fb-warn: #eab308;
    --c-bg: var(--background, var(--fb-bg));
    --c-opaque-base: var(--card, var(--fb-card));
    --c-fg: var(--foreground, var(--fb-fg));
    --c-muted: var(--muted-foreground, var(--fb-muted));
    --c-sec: var(--secondary, var(--fb-sec));
    --c-bd: var(--border, var(--fb-bd));
    --c-acc: var(--primary, var(--accent, var(--fb-acc)));
    --c-acc-fg: var(--primary-foreground, var(--fb-acc-fg));
    --c-ring: var(--ring, var(--c-acc));
    --c-acc-bg: var(--accent-surface, color-mix(in oklab, var(--c-acc) 14%, transparent));
    --c-warn-bg: var(--warning-surface, color-mix(in oklab, var(--warning, var(--fb-warn)) 24%, transparent));
    --c-1: var(--chart-1, #2563eb);
    --c-2: var(--chart-2, #7c3aed);
    --c-3: var(--chart-3, #059669);
    --c-4: var(--chart-4, #d97706);
    --c-5: var(--chart-5, #db2777);
    --c-6: var(--chart-6, #0891b2);
    --r: max(var(--radius, 10px), 10px);
    --font-ui: var(--font-sans, system-ui, -apple-system, BlinkMacSystemFont, "PingFang SC", "Segoe UI", sans-serif);
    --font-code: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
    --stage-h: {STAGE_HEIGHT_DESKTOP}px;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --fb-bg: #0f141e;
      --fb-card: #1a2130;
      --fb-fg: #e6edf3;
      --fb-muted: #94a3b8;
      --fb-sec: #222b3d;
      --fb-bd: #334155;
      --fb-acc: #6366f1;
      --fb-acc-fg: #ffffff;
      --c-1: var(--chart-1, #38bdf8);
      --c-2: var(--chart-2, #a855f7);
      --c-3: var(--chart-3, #10b981);
      --c-4: var(--chart-4, #f59e0b);
      --c-5: var(--chart-5, #ec4899);
      --c-6: var(--chart-6, #06b6d4);
    }}
  }}
  * {{ box-sizing: border-box; }}
  html, body {{
    margin: 0;
    padding: 0;
    background: transparent;
    color: var(--c-fg);
    font-family: var(--font-ui);
    font-size: 13.5px;
    line-height: 1.45;
    -webkit-font-smoothing: antialiased;
  }}
  .root {{
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: 8px;
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
    flex: 1;
  }}
  .tag {{
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    padding: 2px 8px;
    border-radius: 999px;
    background: var(--c-acc-bg);
    color: var(--c-acc);
    flex-shrink: 0;
  }}
  .title {{
    font-size: 15px;
    font-weight: 650;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  .meta {{
    font-size: 12px;
    color: var(--c-muted);
    font-family: var(--font-code);
    white-space: nowrap;
  }}
  .toolbar {{
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }}
  .search-wrap {{
    position: relative;
    flex: 1;
    min-width: 160px;
    display: flex;
    align-items: center;
  }}
  .search {{
    width: 100%;
    background: var(--c-opaque-base);
    color: var(--c-fg);
    border: 1px solid var(--c-bd);
    border-radius: 8px;
    padding: 5px 56px 5px 10px;
    font: inherit;
    font-size: 12.5px;
  }}
  .search:focus-visible {{
    outline: 2px solid var(--c-ring);
    outline-offset: 1px;
    border-color: var(--c-ring);
  }}
  .match-nav {{
    position: absolute;
    right: 6px;
    display: inline-flex;
    align-items: center;
    gap: 2px;
    font-size: 11px;
    font-family: var(--font-code);
    color: var(--c-muted);
  }}
  .match-nav[hidden] {{ display: none; }}
  .seg {{
    display: inline-flex;
    padding: 2px;
    border-radius: 8px;
    border: 1px solid var(--c-bd);
    background: var(--c-sec);
    gap: 2px;
  }}
  .btn {{
    appearance: none;
    border: 1px solid var(--c-bd);
    background: var(--c-opaque-base);
    color: var(--c-fg);
    font: inherit;
    font-size: 12px;
    font-weight: 500;
    padding: 5px 10px;
    border-radius: 8px;
    cursor: pointer;
    white-space: nowrap;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 5px;
    transition: box-shadow 140ms ease-out, border-color 140ms ease-out, scale 120ms ease-out, background-color 140ms ease-out;
  }}
  .btn:hover {{
    border-color: var(--c-acc);
    box-shadow: 0 2px 6px rgb(0 0 0 / 0.1);
  }}
  .btn:active {{ scale: 0.97; }}
  .btn:focus-visible, .mm-node:focus-visible, .mm-toggle:focus-visible {{
    outline: 2px solid CanvasText;
    outline-offset: 2px;
  }}
  .seg .btn {{
    border: none;
    background: transparent;
    padding: 4px 10px;
    border-radius: 6px;
    box-shadow: none;
  }}
  .seg .btn[aria-pressed="true"] {{
    background: var(--c-opaque-base);
    color: var(--c-acc);
    font-weight: 600;
    box-shadow: 0 1px 3px rgb(0 0 0 / 0.12);
  }}
  /* 脑图主舞台：固定高度防止 T3 iframe 高度死循环，点阵背景对标 NotebookLM Studio */
  .stage-shell {{
    position: relative;
    width: 100%;
    height: var(--stage-h);
    border: 1px solid var(--c-bd);
    border-radius: var(--r);
    background-color: color-mix(in oklab, var(--c-bg) 88%, var(--c-opaque-base));
    background-image: radial-gradient(color-mix(in oklab, var(--c-fg) 14%, transparent) 1px, transparent 1px);
    background-size: 20px 20px;
    overflow: hidden;
    touch-action: pan-y;
    user-select: none;
    cursor: grab;
  }}
  .stage-shell.dragging {{ cursor: grabbing; }}
  .stage-shell[hidden] {{ display: none; }}
  .camera {{
    position: absolute;
    left: 0;
    top: 0;
    transform-origin: 0 0;
  }}
  .camera.gesturing {{ will-change: transform; }}
  .links-svg {{
    position: absolute;
    left: 0;
    top: 0;
    overflow: visible;
    pointer-events: none;
  }}
  .mm-link {{
    fill: none;
    stroke-width: 1.6px;
    vector-effect: non-scaling-stroke;
    transition: opacity 160ms ease-out;
  }}
  .mm-link.active {{ stroke-width: 2.5px; }}
  .mm-link.dimmed {{ opacity: 0.28; }}
  .nodes-layer {{
    position: absolute;
    left: 0;
    top: 0;
    width: 1px;
    height: 1px;
  }}
  .mm-node-wrap {{
    position: absolute;
    left: 0;
    top: 0;
    width: max-content;
    transform-origin: 0 0;
    transition: opacity 160ms ease-out;
  }}
  .mm-node-wrap.dimmed {{ opacity: 0.35; }}
  .mm-node {{
    position: relative;
    display: inline-flex;
    align-items: center;
    width: max-content;
    min-width: 64px;
    padding: 8px 14px;
    border-radius: var(--r);
    border: 1.2px solid var(--node-bd, var(--c-bd));
    background: var(--node-bg, var(--c-opaque-base));
    color: var(--node-fg, var(--c-fg));
    font-family: inherit;
    text-align: left;
    cursor: pointer;
    box-shadow: 0 1px 3px rgb(0 0 0 / 0.08), 0 1px 1px rgb(0 0 0 / 0.04);
    transition: box-shadow 140ms ease-out, border-color 140ms ease-out, filter 140ms ease-out;
  }}
  .mm-node.has-kids {{ padding-right: 22px; }}
  .mm-node:hover {{
    box-shadow: 0 4px 12px rgb(0 0 0 / 0.14);
    border-color: var(--node-acc, var(--c-acc));
  }}
  .mm-node.selected {{
    box-shadow: 0 0 0 2px var(--c-ring), 0 6px 16px rgb(0 0 0 / 0.16);
  }}
  .mm-node.hit {{
    box-shadow: 0 0 0 2px var(--warning, #d97706), 0 4px 12px rgb(0 0 0 / 0.14);
  }}
  .mm-node.d0 {{
    max-width: 200px;
    font-size: 15px;
    font-weight: 650;
    line-height: 1.3;
    background: var(--c-acc);
    color: var(--c-acc-fg);
    border-color: color-mix(in oklab, var(--c-acc) 80%, #000);
    box-shadow: 0 4px 14px color-mix(in oklab, var(--c-acc) 28%, transparent);
  }}
  .mm-node.d1 {{
    max-width: 220px;
    font-size: 14px;
    font-weight: 600;
    line-height: 1.35;
  }}
  .mm-node.d2, .mm-node.d3 {{
    max-width: 240px;
    font-size: 13.5px;
    font-weight: 500;
    line-height: 1.35;
  }}
  .mm-text {{
    overflow-wrap: break-word;
    word-break: normal;
  }}
  .mm-text.clamped {{
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }}
  .mm-text mark {{
    background: var(--c-warn-bg);
    color: inherit;
    border-radius: 3px;
    padding: 0 2px;
    box-shadow: inset 0 0 0 1px color-mix(in oklab, var(--warning, #d97706) 45%, transparent);
  }}
  /* NotebookLM 标志性右缘圆形折叠/展开按钮 */
  .mm-toggle {{
    position: absolute;
    right: -10px;
    top: 50%;
    transform: translateY(-50%);
    width: 20px;
    height: 20px;
    border-radius: 999px;
    border: 1.2px solid var(--node-bd, var(--c-bd));
    background: var(--c-opaque-base);
    color: var(--c-fg);
    padding: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    box-shadow: 0 1px 4px rgb(0 0 0 / 0.14);
    z-index: 2;
    transition: background-color 140ms ease-out, border-color 140ms ease-out, scale 120ms ease-out;
  }}
  .mm-toggle::before {{
    content: "";
    position: absolute;
    inset: -7px;
  }}
  .mm-toggle:hover {{
    border-color: var(--node-acc, var(--c-acc));
    background: color-mix(in oklab, var(--node-acc, var(--c-acc)) 14%, var(--c-opaque-base));
  }}
  .mm-toggle:active {{ scale: 0.92; }}
  .mm-toggle svg {{
    width: 11px;
    height: 11px;
    display: block;
    transition: transform 180ms ease-out;
  }}
  .mm-toggle[aria-expanded="true"] svg {{
    transform: rotate(180deg);
  }}
  /* 右下角极简悬浮控制胶囊（对标 NotebookLM Studio） */
  .hud-controls {{
    position: absolute;
    right: 12px;
    bottom: 12px;
    display: inline-flex;
    align-items: center;
    gap: 2px;
    padding: 4px;
    border-radius: 10px;
    background: color-mix(in oklab, var(--c-opaque-base) 92%, transparent);
    border: 1px solid var(--c-bd);
    box-shadow: 0 4px 16px rgb(0 0 0 / 0.14);
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    z-index: 10;
  }}
  .hud-btn {{
    appearance: none;
    border: none;
    background: transparent;
    color: var(--c-fg);
    font: inherit;
    font-size: 12px;
    font-weight: 500;
    min-width: 28px;
    height: 28px;
    padding: 0 7px;
    border-radius: 7px;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 4px;
    transition: background-color 120ms ease-out, scale 100ms ease-out;
  }}
  .hud-btn:hover {{ background: var(--c-sec); }}
  .hud-btn:active {{ scale: 0.95; }}
  .hud-btn:focus-visible {{ outline: 2px solid CanvasText; outline-offset: 1px; }}
  .hud-sep {{
    width: 1px;
    height: 16px;
    background: var(--c-bd);
    margin: 0 2px;
  }}
  .zoom-pct {{
    font-family: var(--font-code);
    font-size: 11.5px;
    color: var(--c-muted);
    min-width: 40px;
    text-align: center;
  }}
  /* 左下角选中节点面包屑与详情条 */
  .detail-bar {{
    position: absolute;
    left: 12px;
    bottom: 12px;
    max-width: calc(100% - 265px);
    padding: 7px 11px;
    border-radius: 10px;
    background: color-mix(in oklab, var(--c-opaque-base) 95%, transparent);
    border: 1px solid var(--c-bd);
    box-shadow: 0 4px 14px rgb(0 0 0 / 0.12);
    display: flex;
    align-items: center;
    gap: 8px;
    z-index: 10;
    font-size: 12px;
  }}
  .detail-bar[hidden] {{ display: none; }}
  .detail-body {{
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }}
  .detail-crumb {{
    font-size: 11px;
    color: var(--c-muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  .detail-text {{
    color: var(--c-fg);
    font-weight: 500;
    overflow-wrap: anywhere;
  }}
  /* 协同手势一次性轻提示 */
  .hint-toast {{
    position: absolute;
    left: 50%;
    top: 12px;
    transform: translateX(-50%);
    padding: 5px 12px;
    border-radius: 999px;
    background: color-mix(in oklab, var(--c-fg) 88%, var(--c-opaque-base));
    color: var(--c-opaque-base);
    font-size: 11.5px;
    font-weight: 500;
    pointer-events: none;
    opacity: 0;
    transition: opacity 200ms ease-out;
    z-index: 12;
    white-space: nowrap;
  }}
  .hint-toast.show {{ opacity: 1; }}
  /* 大纲视图（保留原有折叠树 + 渐进增强静态退路） */
  .outline-wrap {{
    height: var(--stage-h);
    overflow-y: auto;
    background: var(--c-opaque-base);
    border: 1px solid var(--c-bd);
    border-radius: var(--r);
    padding: 12px 14px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }}
  .outline-wrap[hidden] {{ display: none; }}
  .ol-branch {{ display: flex; flex-direction: column; gap: 3px; }}
  .ol-kids {{
    margin-left: 16px;
    padding-left: 12px;
    border-left: 1px solid var(--c-bd);
    display: flex;
    flex-direction: column;
    gap: 3px;
  }}
  .ol-row {{
    display: flex;
    align-items: flex-start;
    gap: 7px;
    padding: 4px 7px;
    border-radius: 6px;
    border: none;
    background: transparent;
    color: inherit;
    font: inherit;
    text-align: left;
    width: 100%;
  }}
  .ol-row.clickable {{ cursor: pointer; }}
  .ol-row.clickable:hover {{ background: var(--c-sec); }}
  .ol-row.hit {{ background: var(--c-warn-bg); }}
  .ol-dot {{
    width: 8px;
    height: 8px;
    border-radius: 999px;
    margin-top: 6px;
    flex-shrink: 0;
  }}
  .ol-cnt {{
    font-size: 11px;
    font-family: var(--font-code);
    color: var(--c-muted);
    padding: 0 6px;
    border-radius: 999px;
    background: var(--c-sec);
    margin-left: auto;
    flex-shrink: 0;
  }}
  /* PNG 导出预览浮层（沙箱禁止直接下载时的优雅降级） */
  .export-modal {{
    position: absolute;
    inset: 0;
    background: rgb(0 0 0 / 0.58);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 16px;
    z-index: 20;
  }}
  .export-modal[hidden] {{ display: none; }}
  .export-card {{
    background: var(--c-opaque-base);
    border: 1px solid var(--c-bd);
    border-radius: 12px;
    padding: 12px;
    max-width: 94%;
    max-height: 92%;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }}
  .export-img {{
    max-width: 100%;
    max-height: 390px;
    object-fit: contain;
    border-radius: 8px;
    border: 1px solid var(--c-bd);
    background: var(--c-bg);
  }}
  .status {{
    font-size: 11.5px;
    color: var(--c-muted);
    min-height: 16px;
  }}
  .status[hidden] {{ display: none; }}
  .measure-bin {{
    position: fixed;
    left: -9999px;
    top: -9999px;
    visibility: hidden;
    pointer-events: none;
  }}
  @media (max-width: 520px) {{
    :root {{ --stage-h: 440px; }}
    .mm-node.d0 {{ max-width: 140px; font-size: 14px; }}
    .mm-node.d1, .mm-node.d2, .mm-node.d3 {{ max-width: 150px; font-size: 12.5px; }}
    .detail-bar {{ max-width: calc(100% - 24px); bottom: 52px; }}
  }}
  @media (pointer: coarse) {{
    .mm-toggle {{
      right: -12px;
      width: 24px;
      height: 24px;
    }}
    .mm-toggle::before {{ inset: -8px; }}
  }}
  @media (prefers-reduced-motion: reduce) {{
    .mm-toggle svg, .btn, .mm-node, .mm-link, .mm-node-wrap {{ transition: none; }}
  }}
  @media (forced-colors: active) {{
    .mm-node {{ border: 2px solid CanvasText; background: Canvas; color: CanvasText; }}
    .mm-node.d0 {{ border: 2px solid Highlight; }}
    .mm-node.selected {{ outline: 2px solid Highlight; }}
    .mm-toggle {{ border: 2px solid ButtonText; background: ButtonFace; color: ButtonText; }}
    .mm-link {{ stroke: CanvasText !important; }}
  }}
</style>
</head>
<body>
<div class="root">
  <div class="head">
    <div class="head-left">
      <span class="tag">Mind Map</span>
      <span id="mm-title" class="title">{static_title}</span>
    </div>
    <div id="mm-meta" class="meta"></div>
  </div>
  <div class="toolbar">
    <div class="search-wrap">
      <input type="text" class="search" id="mm-search" placeholder="搜索节点关键词（回车跳转下一个）..." aria-label="搜索思维导图节点" />
      <span id="match-nav" class="match-nav" hidden></span>
    </div>
    <div class="seg" role="group" aria-label="切换视图">
      <button type="button" class="btn" id="btn-view-map" aria-pressed="true">脑图</button>
      <button type="button" class="btn" id="btn-view-outline" aria-pressed="false">大纲</button>
    </div>
    <button type="button" class="btn" id="btn-expand">展开全部</button>
    <button type="button" class="btn" id="btn-copy">复制大纲</button>
  </div>

  <div id="mm-stage" class="stage-shell" role="region" aria-label="交互式思维导图画布" tabindex="0">
    <div id="mm-camera" class="camera">
      <svg id="mm-links" class="links-svg" width="1" height="1" aria-hidden="true"></svg>
      <div id="mm-nodes" class="nodes-layer"></div>
    </div>
    <div id="mm-toast" class="hint-toast" aria-live="polite">按住 ⌘/Ctrl 滚轮或双指捏合缩放，拖拽平移画布</div>
    <div id="mm-detail" class="detail-bar" hidden>
      <div class="detail-body">
        <div id="detail-crumb" class="detail-crumb"></div>
        <div id="detail-text" class="detail-text"></div>
      </div>
      <button type="button" class="btn" id="btn-copy-node" style="padding:3px 8px;font-size:11.5px;">复制</button>
    </div>
    <div class="hud-controls" role="toolbar" aria-label="画布缩放控制">
      <button type="button" class="hud-btn" id="btn-zoom-out" title="缩小 (-)" aria-label="缩小">−</button>
      <span id="zoom-pct" class="zoom-pct">100%</span>
      <button type="button" class="hud-btn" id="btn-zoom-in" title="放大 (+)" aria-label="放大">+</button>
      <span class="hud-sep"></span>
      <button type="button" class="hud-btn" id="btn-fit" title="适应画布 (0)" aria-label="适应画布">适应</button>
      <button type="button" class="hud-btn" id="btn-reset-depth" title="恢复首屏层级" aria-label="恢复首屏层级">首屏</button>
      <span class="hud-sep"></span>
      <button type="button" class="hud-btn" id="btn-export-png" title="导出高清 PNG" aria-label="导出高清 PNG">PNG</button>
    </div>
    <div id="export-modal" class="export-modal" hidden>
      <div class="export-card">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;">
          <span style="font-weight:600;font-size:13px;">思维导图 PNG（右键或长按保存）</span>
          <button type="button" class="btn" id="btn-close-export" style="padding:3px 9px;">关闭</button>
        </div>
        <img id="export-img" class="export-img" alt="导出的思维导图" />
      </div>
    </div>
  </div>

  <div id="mm-outline" class="outline-wrap" hidden>{static_outline}</div>
  <div id="mm-status" class="status" hidden></div>
</div>
<div id="mm-measure" class="measure-bin" aria-hidden="true"></div>

<script>
const TREE = {safe_json};
const PALETTE = ['var(--c-1)', 'var(--c-2)', 'var(--c-3)', 'var(--c-4)', 'var(--c-5)', 'var(--c-6)'];
const SVG_NS = 'http://www.w3.org/2000/svg';

// 纯布局函数（子树包围盒堆叠，零重叠、可直接单测）
function computeMindMapLayout(root, isCollapsedFn, sizeMap, opts) {{
  const gapX = opts.gapX;
  const gapY = opts.gapY;
  const subtreeGapY = opts.subtreeGapY;

  function visibleChildren(node) {{
    if (!node.children || node.children.length === 0 || isCollapsedFn(node.id)) return [];
    return node.children;
  }}

  function measureSubtree(node) {{
    const sz = sizeMap.get(node.id) || {{ w: 140, h: 38 }};
    const kids = visibleChildren(node);
    if (kids.length === 0) {{
      return {{ node, w: sz.w, h: sz.h, subH: sz.h, kids: [] }};
    }}
    const laidKids = kids.map(measureSubtree);
    let totalKidsH = 0;
    for (let i = 0; i < laidKids.length; i++) {{
      totalKidsH += laidKids[i].subH;
      if (i > 0) {{
        const extra = (laidKids[i - 1].kids.length > 0 || laidKids[i].kids.length > 0) ? subtreeGapY : gapY;
        totalKidsH += extra;
      }}
    }}
    return {{ node, w: sz.w, h: sz.h, subH: Math.max(sz.h, totalKidsH), kids: laidKids }};
  }}

  const placed = new Map();
  const edges = [];

  function assign(box, x, topY) {{
    if (box.kids.length === 0) {{
      const y = topY + (box.subH - box.h) / 2;
      const rect = {{ id: box.node.id, node: box.node, x, y, w: box.w, h: box.h, cy: y + box.h / 2 }};
      placed.set(box.node.id, rect);
      return rect;
    }}
    let kidsTotalH = 0;
    for (let i = 0; i < box.kids.length; i++) {{
      kidsTotalH += box.kids[i].subH;
      if (i > 0) {{
        kidsTotalH += (box.kids[i - 1].kids.length > 0 || box.kids[i].kids.length > 0) ? subtreeGapY : gapY;
      }}
    }}
    let cursorY = topY + (box.subH - kidsTotalH) / 2;
    const childX = x + box.w + gapX;
    const childRects = [];
    for (let i = 0; i < box.kids.length; i++) {{
      if (i > 0) {{
        cursorY += (box.kids[i - 1].kids.length > 0 || box.kids[i].kids.length > 0) ? subtreeGapY : gapY;
      }}
      childRects.push(assign(box.kids[i], childX, cursorY));
      cursorY += box.kids[i].subH;
    }}
    const midY = (childRects[0].cy + childRects[childRects.length - 1].cy) / 2;
    const y = midY - box.h / 2;
    const rect = {{ id: box.node.id, node: box.node, x, y, w: box.w, h: box.h, cy: midY }};
    placed.set(box.node.id, rect);
    for (const cr of childRects) {{
      edges.push({{ source: rect, target: cr }});
    }}
    return rect;
  }}

  const treeBox = measureSubtree(root);
  assign(treeBox, 0, 0);

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const r of placed.values()) {{
    if (r.x < minX) minX = r.x;
    if (r.y < minY) minY = r.y;
    if (r.x + r.w + 14 > maxX) maxX = r.x + r.w + 14;
    if (r.y + r.h > maxY) maxY = r.y + r.h;
  }}
  if (!Number.isFinite(minX)) minX = minY = maxX = maxY = 0;
  return {{ placed, edges, bounds: {{ minX, minY, maxX, maxY, w: maxX - minX, h: maxY - minY }} }};
}}

if (typeof window !== 'undefined') {{
  window.__MM_LAYOUT = computeMindMapLayout;
}}

(function initApp() {{
  if (typeof document === 'undefined') return;

  const titleEl = document.getElementById('mm-title');
  const metaEl = document.getElementById('mm-meta');
  const searchEl = document.getElementById('mm-search');
  const matchNavEl = document.getElementById('match-nav');
  const stageEl = document.getElementById('mm-stage');
  const cameraEl = document.getElementById('mm-camera');
  const linksSvg = document.getElementById('mm-links');
  const nodesLayer = document.getElementById('mm-nodes');
  const outlineEl = document.getElementById('mm-outline');
  const statusEl = document.getElementById('mm-status');
  const toastEl = document.getElementById('mm-toast');
  const detailEl = document.getElementById('mm-detail');
  const detailCrumbEl = document.getElementById('detail-crumb');
  const detailTextEl = document.getElementById('detail-text');
  const zoomPctEl = document.getElementById('zoom-pct');
  const measureBin = document.getElementById('mm-measure');
  const btnViewMap = document.getElementById('btn-view-map');
  const btnViewOutline = document.getElementById('btn-view-outline');
  const btnExpand = document.getElementById('btn-expand');
  const exportModal = document.getElementById('export-modal');
  const exportImg = document.getElementById('export-img');

  const reducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const isCoarsePointer = () => window.matchMedia && window.matchMedia('(pointer: coarse)').matches;

  let seq = 0;
  const nodeById = new Map();
  function annotate(raw, depth, branchIdx, parent) {{
    const id = 'n' + (seq++);
    const node = {{ id, name: raw.name, depth, branchIdx, parent, count: 1, children: [] }};
    node.children = (raw.children || []).map((c, i) =>
      annotate(c, depth + 1, depth === 0 ? i : branchIdx, node)
    );
    node.count = 1 + node.children.reduce((acc, c) => acc + c.count, 0);
    nodeById.set(id, node);
    return node;
  }}

  const ROOT = annotate(TREE, 0, 0, null);
  titleEl.textContent = ROOT.name;

  const collapsed = new Set();
  const searchCollapsed = new Set();
  const sizeMap = new Map();
  const nodeEls = new Map();
  const linkEls = new Map();

  let viewMode = 'map';
  let query = '';
  let normalizedQuery = '';
  let matchIds = [];
  let matchCursor = -1;
  let selectedId = null;
  let isComposing = false;
  let lastCompositionEndAt = null;
  let currentLayout = null;
  let cam = {{ tx: 24, ty: 24, k: 1 }};
  let animFrame = 0;
  let gestureTimer = 0;
  let wheelHintShown = false;
  let wheelHintTimer = 0;

  function normalizeText(s) {{
    return String(s || '').normalize('NFKC').toLowerCase();
  }}

  function layoutOpts() {{
    const narrow = stageEl.clientWidth > 0 && stageEl.clientWidth <= 500;
    return {{ gapX: narrow ? 36 : 56, gapY: 10, subtreeGapY: 16 }};
  }}

  function minScale() {{
    return isCoarsePointer() ? 0.7 : 0.4;
  }}

  function applyCamera(snap) {{
    const dpr = window.devicePixelRatio || 1;
    const tx = snap ? Math.round(cam.tx * dpr) / dpr : cam.tx;
    const ty = snap ? Math.round(cam.ty * dpr) / dpr : cam.ty;
    cameraEl.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + cam.k + ')';
    zoomPctEl.textContent = Math.round(cam.k * 100) + '%';
  }}

  function beginGesture() {{
    if (gestureTimer) {{
      clearTimeout(gestureTimer);
      gestureTimer = 0;
    }}
    cameraEl.classList.add('gesturing');
  }}

  function endGesture() {{
    if (gestureTimer) clearTimeout(gestureTimer);
    gestureTimer = setTimeout(() => {{
      cameraEl.classList.remove('gesturing');
      applyCamera(true);
      gestureTimer = 0;
    }}, 120);
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

  function showWheelHintOnce() {{
    if (wheelHintShown) return;
    wheelHintShown = true;
    toastEl.classList.add('show');
    if (wheelHintTimer) clearTimeout(wheelHintTimer);
    wheelHintTimer = setTimeout(() => toastEl.classList.remove('show'), 2400);
  }}

  function branchStyleVars(node) {{
    if (node.depth === 0) return {{}};
    const col = PALETTE[node.branchIdx % PALETTE.length];
    const pct = node.depth === 1 ? '18%' : (node.depth === 2 ? '11%' : '6%');
    return {{
      '--node-acc': col,
      '--node-bg': 'color-mix(in oklab, ' + col + ' ' + pct + ', var(--c-opaque-base))',
      '--node-bd': 'color-mix(in oklab, ' + col + ' 42%, var(--c-bd))',
    }};
  }}

  function fillHighlightedText(container, text, qNorm) {{
    container.textContent = '';
    if (!qNorm) {{
      container.textContent = text;
      return;
    }}
    // 安全 DOM 文本节点拆分，避免字符串 HTML 拼接
    const raw = String(text || '');
    const lowerRaw = raw.toLowerCase();
    const qSimple = qNorm;
    let idx = lowerRaw.indexOf(qSimple);
    if (idx === -1) {{
      // NFKC 命中但原始索引不一致时整体高亮
      if (normalizeText(raw).includes(qNorm)) {{
        const mk = document.createElement('mark');
        mk.textContent = raw;
        container.appendChild(mk);
      }} else {{
        container.textContent = raw;
      }}
      return;
    }}
    let pos = 0;
    while (idx !== -1) {{
      if (idx > pos) container.appendChild(document.createTextNode(raw.slice(pos, idx)));
      const mk = document.createElement('mark');
      mk.textContent = raw.slice(idx, idx + qSimple.length);
      container.appendChild(mk);
      pos = idx + qSimple.length;
      idx = lowerRaw.indexOf(qSimple, pos);
    }}
    if (pos < raw.length) container.appendChild(document.createTextNode(raw.slice(pos)));
  }}

  function makeChevronSvg() {{
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', '0 0 12 12');
    svg.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', 'M4.2 2.5L7.8 6L4.2 9.5');
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke', 'currentColor');
    path.setAttribute('stroke-width', '1.8');
    path.setAttribute('stroke-linecap', 'round');
    path.setAttribute('stroke-linejoin', 'round');
    svg.appendChild(path);
    return svg;
  }}

  function createNodeWrap(node) {{
    const wrap = document.createElement('div');
    wrap.className = 'mm-node-wrap';
    wrap.dataset.id = node.id;

    const btn = document.createElement('button');
    btn.type = 'button';
    const dCls = node.depth === 0 ? 'd0' : (node.depth === 1 ? 'd1' : (node.depth === 2 ? 'd2' : 'd3'));
    const hasKids = node.children.length > 0;
    btn.className = 'mm-node ' + dCls + (hasKids ? ' has-kids' : '');
    btn.dataset.nodeId = node.id;
    const vars = branchStyleVars(node);
    for (const [k, v] of Object.entries(vars)) btn.style.setProperty(k, v);

    const txt = document.createElement('span');
    txt.className = 'mm-text' + (node.name.length > 120 ? ' clamped' : '');
    txt.textContent = node.name;
    btn.appendChild(txt);
    wrap.appendChild(btn);

    if (hasKids) {{
      const tog = document.createElement('button');
      tog.type = 'button';
      tog.className = 'mm-toggle';
      tog.dataset.toggleId = node.id;
      for (const [k, v] of Object.entries(vars)) tog.style.setProperty(k, v);
      const subCount = node.count - 1;
      tog.title = '展开或收起（含 ' + subCount + ' 个子节点）';
      tog.setAttribute('aria-label', node.name + '，包含 ' + subCount + ' 个子节点');
      tog.appendChild(makeChevronSvg());
      wrap.appendChild(tog);
    }}
    return wrap;
  }}

  function measureAllNodes() {{
    measureBin.textContent = '';
    const narrow = stageEl.clientWidth > 0 && stageEl.clientWidth <= 500;
    measureBin.style.width = narrow ? '360px' : '900px';
    const temp = [];
    for (const node of nodeById.values()) {{
      const wrap = createNodeWrap(node);
      measureBin.appendChild(wrap);
      temp.push([node.id, wrap.firstElementChild]);
    }}
    for (const [id, btn] of temp) {{
      sizeMap.set(id, {{ w: Math.ceil(btn.offsetWidth), h: Math.ceil(btn.offsetHeight) }});
    }}
    measureBin.textContent = '';
  }}

  function activeCollapseSet() {{
    return normalizedQuery ? searchCollapsed : collapsed;
  }}

  function isCollapsed(id) {{
    return activeCollapseSet().has(id);
  }}

  // 预算规则：按层逐级展开，直到 1.0x 下超出视口 85% 为止（63 节点样例稳定落在第 1 层）
  function applyBudgetDefaultCollapse() {{
    collapsed.clear();
    const vw = Math.max(stageEl.clientWidth || 728, 320);
    const vh = Math.max(stageEl.clientHeight || {STAGE_HEIGHT_DESKTOP}, 360);
    const maxDepthInTree = Math.max(...Array.from(nodeById.values()).map((n) => n.depth));
    let chosenDepth = 1;
    for (let d = 1; d <= maxDepthInTree; d++) {{
      const testCollapsed = new Set();
      for (const n of nodeById.values()) {{
        if (n.depth >= d && n.children.length > 0) testCollapsed.add(n.id);
      }}
      const res = computeMindMapLayout(ROOT, (id) => testCollapsed.has(id), sizeMap, layoutOpts());
      if (res.bounds.w <= vw * 0.85 && res.bounds.h <= vh * 0.85) {{
        chosenDepth = d;
      }} else {{
        break;
      }}
    }}
    for (const n of nodeById.values()) {{
      if (n.depth >= chosenDepth && n.children.length > 0) collapsed.add(n.id);
    }}
  }}

  function linkPathD(sx, sy, tx, ty) {{
    const x1 = sx + 10; // 从右缘折叠钮右侧出发
    const mx = x1 + (tx - x1) * 0.5;
    return 'M ' + x1.toFixed(1) + ' ' + sy.toFixed(1) + ' C ' + mx.toFixed(1) + ' ' + sy.toFixed(1) + ', ' + mx.toFixed(1) + ' ' + ty.toFixed(1) + ', ' + tx.toFixed(1) + ' ' + ty.toFixed(1);
  }}

  function getPathFocusSet(id) {{
    if (!id || !nodeById.has(id)) return null;
    const set = new Set();
    let cur = nodeById.get(id);
    while (cur) {{
      set.add(cur.id);
      cur = cur.parent;
    }}
    const walkDown = (n) => {{
      set.add(n.id);
      for (const c of n.children) walkDown(c);
    }};
    walkDown(nodeById.get(id));
    return set;
  }}

  function updateExpandButtonLabel() {{
    const set = activeCollapseSet();
    let anyExpandableCollapsed = false;
    for (const n of nodeById.values()) {{
      if (n.children.length > 0 && set.has(n.id)) {{
        anyExpandableCollapsed = true;
        break;
      }}
    }}
    btnExpand.textContent = anyExpandableCollapsed ? '展开全部' : '收起全部';
  }}

  function updateDetailBar() {{
    if (!selectedId || !nodeById.has(selectedId)) {{
      detailEl.hidden = true;
      return;
    }}
    const node = nodeById.get(selectedId);
    const crumbs = [];
    let cur = node;
    while (cur) {{
      crumbs.unshift(cur.name);
      cur = cur.parent;
    }}
    detailCrumbEl.textContent = crumbs.slice(0, -1).join(' / ') || '根节点';
    detailTextEl.textContent = node.name;
    detailEl.hidden = false;
  }}

  function updateMetaAndStyles() {{
    const hitSet = new Set(matchIds);
    const focusSet = selectedId ? getPathFocusSet(selectedId) : (normalizedQuery && matchIds.length ? (() => {{
      const s = new Set();
      for (const id of matchIds) {{
        let c = nodeById.get(id);
        while (c) {{ s.add(c.id); c = c.parent; }}
      }}
      return s;
    }})() : null);

    if (normalizedQuery) {{
      metaEl.textContent = '匹配 ' + matchIds.length + ' / ' + (ROOT.count - 1) + ' 节点';
      if (matchIds.length > 0) {{
        matchNavEl.hidden = false;
        matchNavEl.textContent = (matchCursor + 1) + '/' + matchIds.length;
      }} else {{
        matchNavEl.hidden = false;
        matchNavEl.textContent = '0/0';
      }}
    }} else {{
      metaEl.textContent = ROOT.children.length + ' 分支 · ' + (ROOT.count - 1) + ' 节点';
      matchNavEl.hidden = true;
    }}

    for (const [id, wrap] of nodeEls.entries()) {{
      const node = nodeById.get(id);
      const btn = wrap.firstElementChild;
      const txt = btn.querySelector('.mm-text');
      fillHighlightedText(txt, node.name, normalizedQuery);
      btn.classList.toggle('selected', id === selectedId);
      btn.classList.toggle('hit', hitSet.has(id));
      wrap.classList.toggle('dimmed', Boolean(focusSet && !focusSet.has(id)));
      const tog = wrap.querySelector('.mm-toggle');
      if (tog) tog.setAttribute('aria-expanded', isCollapsed(id) ? 'false' : 'true');
    }}

    for (const [key, pathEl] of linkEls.entries()) {{
      const [sid, tid] = key.split('->');
      const onFocus = Boolean(focusSet && focusSet.has(sid) && focusSet.has(tid));
      const dimmed = Boolean(focusSet && !onFocus);
      const tNode = nodeById.get(tid);
      const col = PALETTE[tNode.branchIdx % PALETTE.length];
      pathEl.style.stroke = onFocus ? col : 'color-mix(in oklab, ' + col + ' 48%, var(--c-bd))';
      pathEl.classList.toggle('active', onFocus);
      pathEl.classList.toggle('dimmed', dimmed);
    }}

    updateExpandButtonLabel();
    updateDetailBar();
  }}

  function fitView(opts) {{
    if (!currentLayout) return;
    const b = currentLayout.bounds;
    const vw = stageEl.clientWidth || 728;
    const vh = stageEl.clientHeight || {STAGE_HEIGHT_DESKTOP};
    const pad = 28;
    const maxFitK = (opts && opts.maxK) || 1.0;
    const minFitK = (opts && opts.minK) || 0.6;
    const rawK = Math.min((vw - pad * 2) / Math.max(b.w, 1), (vh - pad * 2) / Math.max(b.h, 1));
    const targetK = Math.max(minFitK, Math.min(maxFitK, rawK));
    const targetTx = (opts && opts.leftAlign)
      ? 24 - b.minX * targetK
      : (vw - b.w * targetK) / 2 - b.minX * targetK;
    const targetTy = (vh - b.h * targetK) / 2 - b.minY * targetK;
    animateCameraTo(targetTx, targetTy, targetK, opts && opts.instant);
  }}

  function focusNodeOnCanvas(id, minTargetK) {{
    if (!currentLayout || !currentLayout.placed.has(id)) return;
    const r = currentLayout.placed.get(id);
    const vw = stageEl.clientWidth || 728;
    const vh = stageEl.clientHeight || {STAGE_HEIGHT_DESKTOP};
    const targetK = Math.max(cam.k, minTargetK || 0.85);
    const targetTx = vw * 0.45 - (r.x + r.w / 2) * targetK;
    const targetTy = vh * 0.48 - r.cy * targetK;
    animateCameraTo(targetTx, targetTy, targetK, false);
  }}

  function animateCameraTo(targetTx, targetTy, targetK, instant) {{
    if (instant || reducedMotion) {{
      cam.tx = targetTx;
      cam.ty = targetTy;
      cam.k = targetK;
      applyCamera(true);
      return;
    }}
    const sTx = cam.tx, sTy = cam.ty, sK = cam.k;
    const t0 = performance.now();
    const dur = 220;
    beginGesture();
    const step = (now) => {{
      const u = Math.min(1, (now - t0) / dur);
      const e = 1 - Math.pow(1 - u, 3);
      cam.tx = sTx + (targetTx - sTx) * e;
      cam.ty = sTy + (targetTy - sTy) * e;
      cam.k = sK + (targetK - sK) * e;
      applyCamera(u >= 1);
      if (u < 1) requestAnimationFrame(step);
      else endGesture();
    }};
    requestAnimationFrame(step);
  }}

  function renderMap(anchorId, animate) {{
    const prevPlaced = currentLayout ? currentLayout.placed : new Map();
    const anchorOldRect = anchorId ? prevPlaced.get(anchorId) : null;
    const nextLayout = computeMindMapLayout(ROOT, isCollapsed, sizeMap, layoutOpts());
    currentLayout = nextLayout;

    // 锚点保持：点击展开/收起时，被点节点在屏幕上的坐标保持不动，再最小平移让新子树进入视口
    let targetTx = cam.tx;
    let targetTy = cam.ty;
    if (anchorId && anchorOldRect && nextLayout.placed.has(anchorId)) {{
      const anchorNew = nextLayout.placed.get(anchorId);
      targetTx += (anchorOldRect.x - anchorNew.x) * cam.k;
      targetTy += (anchorOldRect.cy - anchorNew.cy) * cam.k;
      if (!isCollapsed(anchorId)) {{
        const subIds = [];
        const collectVis = (n) => {{
          if (!nextLayout.placed.has(n.id)) return;
          subIds.push(n.id);
          if (!isCollapsed(n.id)) n.children.forEach(collectVis);
        }};
        collectVis(nodeById.get(anchorId));
        let sMinX = Infinity, sMaxX = -Infinity, sMinY = Infinity, sMaxY = -Infinity;
        for (const sid of subIds) {{
          const r = nextLayout.placed.get(sid);
          sMinX = Math.min(sMinX, r.x);
          sMaxX = Math.max(sMaxX, r.x + r.w + 18);
          sMinY = Math.min(sMinY, r.y);
          sMaxY = Math.max(sMaxY, r.y + r.h);
        }}
        const vw = stageEl.clientWidth || 728;
        const vh = stageEl.clientHeight || {STAGE_HEIGHT_DESKTOP};
        const pad = 28;
        const screenRight = sMaxX * cam.k + targetTx;
        const screenLeft = sMinX * cam.k + targetTx;
        const screenBottom = sMaxY * cam.k + targetTy;
        const screenTop = sMinY * cam.k + targetTy;
        if (screenRight > vw - pad) targetTx -= Math.min(screenRight - (vw - pad), Math.max(0, screenLeft - pad));
        if (screenBottom > vh - pad) targetTy -= Math.min(screenBottom - (vh - pad), Math.max(0, screenTop - pad));
        if (screenTop < pad) targetTy += Math.min(pad - screenTop, Math.max(0, (vh - pad) - screenBottom));
      }}
    }}

    // 同步 DOM 节点集合（进入 / 保留 / 退出）
    const entering = new Set();
    for (const [id, rect] of nextLayout.placed.entries()) {{
      if (!nodeEls.has(id)) {{
        const wrap = createNodeWrap(rect.node);
        nodesLayer.appendChild(wrap);
        nodeEls.set(id, wrap);
        entering.add(id);
      }}
    }}
    const exiting = [];
    for (const [id, wrap] of nodeEls.entries()) {{
      if (!nextLayout.placed.has(id)) {{
        exiting.push([id, wrap, prevPlaced.get(id)]);
      }}
    }}

    // 同步 SVG 路径集合
    const nextEdgeMap = new Map();
    for (const e of nextLayout.edges) nextEdgeMap.set(e.source.id + '->' + e.target.id, e);
    for (const [key, e] of nextEdgeMap.entries()) {{
      if (!linkEls.has(key)) {{
        const p = document.createElementNS(SVG_NS, 'path');
        p.setAttribute('class', 'mm-link');
        linksSvg.appendChild(p);
        linkEls.set(key, p);
      }}
    }}
    const exitingEdges = [];
    for (const [key, pathEl] of linkEls.entries()) {{
      if (!nextEdgeMap.has(key)) {{
        exitingEdges.push([key, pathEl]);
      }}
    }}

    updateMetaAndStyles();

    const anchorParentPos = (id) => {{
      let cur = nodeById.get(id);
      while (cur && cur.parent) {{
        cur = cur.parent;
        if (prevPlaced.has(cur.id)) {{
          const pr = prevPlaced.get(cur.id);
          return {{ x: pr.x + pr.w, y: pr.cy - 18, cy: pr.cy }};
        }}
        if (nextLayout.placed.has(cur.id)) {{
          const nr = nextLayout.placed.get(cur.id);
          return {{ x: nr.x + nr.w, y: nr.cy - 18, cy: nr.cy }};
        }}
      }}
      return {{ x: 0, y: 0, cy: 0 }};
    }};

    const shouldAnim = animate && !reducedMotion && prevPlaced.size > 0;
    if (animFrame) cancelAnimationFrame(animFrame);

    if (!shouldAnim) {{
      for (const [id, wrap] of exiting) {{ wrap.remove(); nodeEls.delete(id); }}
      for (const [key, p] of exitingEdges) {{ p.remove(); linkEls.delete(key); }}
      for (const [id, r] of nextLayout.placed.entries()) {{
        const wrap = nodeEls.get(id);
        wrap.style.transform = 'translate(' + r.x.toFixed(1) + 'px,' + r.y.toFixed(1) + 'px)';
        wrap.style.opacity = '';
      }}
      for (const [key, e] of nextEdgeMap.entries()) {{
        linkEls.get(key).setAttribute('d', linkPathD(e.source.x + e.source.w, e.source.cy, e.target.x, e.target.cy));
      }}
      cam.tx = targetTx;
      cam.ty = targetTy;
      applyCamera(true);
      return;
    }}

    // 同一 rAF 循环同时驱动 HTML 节点 FLIP 与 SVG 贝塞尔路径重算，零脱节
    const sTx = cam.tx, sTy = cam.ty;
    const t0 = performance.now();
    const dur = 220;
    beginGesture();

    const frame = (now) => {{
      const u = Math.min(1, (now - t0) / dur);
      const ease = 1 - Math.pow(1 - u, 3);
      cam.tx = sTx + (targetTx - sTx) * ease;
      cam.ty = sTy + (targetTy - sTy) * ease;
      applyCamera(u >= 1);

      const interpRects = new Map();
      for (const [id, nr] of nextLayout.placed.entries()) {{
        const wrap = nodeEls.get(id);
        let sx, sy;
        if (entering.has(id)) {{
          const ap = anchorParentPos(id);
          sx = ap.x;
          sy = ap.y;
          wrap.style.opacity = ease.toFixed(3);
        }} else {{
          const pr = prevPlaced.get(id) || nr;
          sx = pr.x;
          sy = pr.y;
          wrap.style.opacity = '';
        }}
        const cx = sx + (nr.x - sx) * ease;
        const cy = sy + (nr.y - sy) * ease;
        wrap.style.transform = 'translate(' + cx.toFixed(1) + 'px,' + cy.toFixed(1) + 'px)';
        interpRects.set(id, {{ x: cx, y: cy, w: nr.w, h: nr.h, cy: cy + nr.h / 2 }});
      }}

      for (const [id, wrap, pr] of exiting) {{
        const dest = anchorParentPos(id);
        const sx = pr ? pr.x : dest.x;
        const sy = pr ? pr.y : dest.y;
        const cx = sx + (dest.x - sx) * ease;
        const cy = sy + (dest.y - sy) * ease;
        wrap.style.transform = 'translate(' + cx.toFixed(1) + 'px,' + cy.toFixed(1) + 'px)';
        wrap.style.opacity = (1 - ease).toFixed(3);
      }}

      for (const [key, e] of nextEdgeMap.entries()) {{
        const sr = interpRects.get(e.source.id) || e.source;
        const tr = interpRects.get(e.target.id) || e.target;
        const pathEl = linkEls.get(key);
        pathEl.setAttribute('d', linkPathD(sr.x + sr.w, sr.cy, tr.x, tr.cy));
        if (entering.has(e.target.id)) pathEl.style.opacity = ease.toFixed(3);
        else pathEl.style.opacity = '';
      }}

      for (const [key, pathEl] of exitingEdges) {{
        pathEl.style.opacity = (1 - ease).toFixed(3);
      }}

      if (u < 1) {{
        animFrame = requestAnimationFrame(frame);
      }} else {{
        animFrame = 0;
        for (const [id, wrap] of exiting) {{ wrap.remove(); nodeEls.delete(id); }}
        for (const [key, p] of exitingEdges) {{ p.remove(); linkEls.delete(key); }}
        for (const wrap of nodeEls.values()) wrap.style.opacity = '';
        for (const p of linkEls.values()) p.style.opacity = '';
        endGesture();
      }}
    }};
    animFrame = requestAnimationFrame(frame);
  }}

  // 大纲视图渲染（纯 DOM API 构建，避免 HTML 拼接）
  function renderOutline() {{
    outlineEl.textContent = '';
    const hitSet = new Set(matchIds);
    const needed = new Set();
    if (normalizedQuery) {{
      for (const id of matchIds) {{
        let c = nodeById.get(id);
        while (c) {{ needed.add(c.id); c = c.parent; }}
      }}
    }}

    function buildBranch(node) {{
      if (normalizedQuery && !needed.has(node.id)) return null;
      const kids = node.children.filter((c) => !normalizedQuery || needed.has(c.id));
      const hasKids = kids.length > 0;
      const collapsedNow = isCollapsed(node.id);
      const wrap = document.createElement('div');
      wrap.className = 'ol-branch';

      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'ol-row' + (hasKids ? ' clickable' : '') + (hitSet.has(node.id) ? ' hit' : '');
      if (hasKids) row.dataset.olToggle = node.id;

      const chev = document.createElement('span');
      chev.style.cssText = 'width:14px;color:var(--c-muted);font-family:var(--font-code);font-size:11px;flex-shrink:0;margin-top:2px;';
      chev.textContent = hasKids ? (collapsedNow ? '+' : '−') : '·';
      row.appendChild(chev);

      const dot = document.createElement('span');
      dot.className = 'ol-dot';
      dot.style.background = node.depth === 0 ? 'var(--c-acc)' : PALETTE[node.branchIdx % PALETTE.length];
      row.appendChild(dot);

      const lbl = document.createElement('span');
      lbl.style.fontWeight = node.depth <= 1 ? '600' : '450';
      fillHighlightedText(lbl, node.name, normalizedQuery);
      row.appendChild(lbl);

      if (hasKids) {{
        const cnt = document.createElement('span');
        cnt.className = 'ol-cnt';
        cnt.textContent = String(node.count - 1);
        row.appendChild(cnt);
      }}
      wrap.appendChild(row);

      if (hasKids && !collapsedNow) {{
        const kidsEl = document.createElement('div');
        kidsEl.className = 'ol-kids';
        for (const c of kids) {{
          const childEl = buildBranch(c);
          if (childEl) kidsEl.appendChild(childEl);
        }}
        wrap.appendChild(kidsEl);
      }}
      return wrap;
    }}

    if (normalizedQuery && matchIds.length === 0) {{
      const empty = document.createElement('div');
      empty.style.cssText = 'color:var(--c-muted);padding:12px 4px;';
      empty.textContent = '无匹配节点';
      outlineEl.appendChild(empty);
      return;
    }}
    for (const c of ROOT.children) {{
      const el = buildBranch(c);
      if (el) outlineEl.appendChild(el);
    }}
  }}

  function runSearch(nextQ) {{
    query = nextQ;
    normalizedQuery = normalizeText(query.trim());
    matchIds = [];
    matchCursor = -1;
    searchCollapsed.clear();
    setStatus('');
    if (normalizedQuery) {{
      for (const n of nodeById.values()) {{
        if (normalizeText(n.name).includes(normalizedQuery)) matchIds.push(n.id);
      }}
      // 默认折叠无关分支，仅展开通往命中节点的祖先链路
      const keepExpanded = new Set();
      for (const id of matchIds) {{
        let c = nodeById.get(id).parent;
        while (c) {{ keepExpanded.add(c.id); c = c.parent; }}
      }}
      for (const n of nodeById.values()) {{
        if (n.children.length > 0 && !keepExpanded.has(n.id)) searchCollapsed.add(n.id);
      }}
      if (matchIds.length > 0) {{
        matchCursor = 0;
        selectedId = matchIds[0];
      }}
    }}
    renderMap(null, true);
    if (viewMode === 'outline') renderOutline();
    if (normalizedQuery && matchIds.length > 0 && viewMode === 'map') {{
      focusNodeOnCanvas(matchIds[0], 0.85);
    }}
  }}

  function toMarkdown(node, indent) {{
    const depth = indent || 0;
    const prefix = depth === 0 ? '# ' : ('  '.repeat(depth - 1) + '- ');
    const lines = [prefix + node.name];
    for (const c of node.children) lines.push(toMarkdown(c, depth + 1));
    return lines.join('\\n');
  }}

  // 指针拖拽平移 & 触摸双指缩放（支持 mouse / touch，touch-action: pan-y 避免劫持移动端竖滚）
  const activePointers = new Map();
  let dragState = null;
  let pinchState = null;

  stageEl.addEventListener('pointerdown', (e) => {{
    if (e.target.closest('.hud-controls, .detail-bar, .export-modal, .mm-toggle')) return;
    if (e.button !== 0 && e.pointerType === 'mouse') return;
    activePointers.set(e.pointerId, {{ x: e.clientX, y: e.clientY }});
    if (activePointers.size === 2) {{
      const pts = Array.from(activePointers.values());
      const dist = Math.hypot(pts[1].x - pts[0].x, pts[1].y - pts[0].y);
      const mid = {{ x: (pts[0].x + pts[1].x) / 2, y: (pts[0].y + pts[1].y) / 2 }};
      pinchState = {{ startDist: Math.max(dist, 1), startK: cam.k, startTx: cam.tx, startTy: cam.ty, mid }};
      dragState = null;
      beginGesture();
      return;
    }}
    dragState = {{
      id: e.pointerId,
      pointerType: e.pointerType,
      sx: e.clientX,
      sy: e.clientY,
      startTx: cam.tx,
      startTy: cam.ty,
      moved: false,
      threshold: e.pointerType === 'touch' ? 8 : 4,
    }};
  }});

  stageEl.addEventListener('pointermove', (e) => {{
    if (activePointers.has(e.pointerId)) {{
      activePointers.set(e.pointerId, {{ x: e.clientX, y: e.clientY }});
    }}
    if (pinchState && activePointers.size === 2) {{
      const pts = Array.from(activePointers.values());
      const dist = Math.hypot(pts[1].x - pts[0].x, pts[1].y - pts[0].y);
      const rect = stageEl.getBoundingClientRect();
      const midX = (pts[0].x + pts[1].x) / 2 - rect.left;
      const midY = (pts[0].y + pts[1].y) / 2 - rect.top;
      const nextK = Math.max(minScale(), Math.min(2.0, pinchState.startK * (dist / pinchState.startDist)));
      const ratio = nextK / pinchState.startK;
      cam.k = nextK;
      cam.tx = midX - (midX - pinchState.startTx) * ratio;
      cam.ty = midY - (midY - pinchState.startTy) * ratio;
      applyCamera(false);
      return;
    }}
    if (!dragState || dragState.id !== e.pointerId) return;
    const dx = e.clientX - dragState.sx;
    const dy = e.clientY - dragState.sy;
    if (!dragState.moved) {{
      // 触摸单指竖滑交还给宿主聊天滚动，仅横向或鼠标拖拽接管平移
      if (dragState.pointerType === 'touch' && Math.abs(dy) > Math.abs(dx) * 1.25 && Math.abs(dy) > dragState.threshold) {{
        dragState = null;
        return;
      }}
      if (Math.hypot(dx, dy) < dragState.threshold) return;
      dragState.moved = true;
      stageEl.classList.add('dragging');
      beginGesture();
      try {{ stageEl.setPointerCapture(e.pointerId); }} catch {{}}
    }}
    cam.tx = dragState.startTx + dx;
    cam.ty = dragState.startTy + dy;
    applyCamera(false);
  }});

  function clearPointer(e) {{
    activePointers.delete(e.pointerId);
    if (pinchState && activePointers.size < 2) {{
      pinchState = null;
      endGesture();
    }}
    if (dragState && dragState.id === e.pointerId) {{
      const wasMoved = dragState.moved;
      dragState = null;
      stageEl.classList.remove('dragging');
      if (wasMoved) {{
        try {{ stageEl.releasePointerCapture(e.pointerId); }} catch {{}}
        endGesture();
        stageEl.dataset.justDragged = '1';
        setTimeout(() => {{ delete stageEl.dataset.justDragged; }}, 60);
      }}
    }}
  }}
  stageEl.addEventListener('pointerup', clearPointer);
  stageEl.addEventListener('pointercancel', clearPointer);

  // 滚轮协同手势：仅 Ctrl/Cmd 滚轮（含 Mac 触控板捏合）缩放，普通滚轮不劫持 T3 聊天流
  stageEl.addEventListener('wheel', (e) => {{
    if (!e.ctrlKey && !e.metaKey) {{
      showWheelHintOnce();
      return;
    }}
    e.preventDefault();
    const rect = stageEl.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const factor = Math.exp(-e.deltaY * 0.008);
    const nextK = Math.max(minScale(), Math.min(2.0, cam.k * factor));
    const ratio = nextK / cam.k;
    beginGesture();
    cam.tx = px - (px - cam.tx) * ratio;
    cam.ty = py - (py - cam.ty) * ratio;
    cam.k = nextK;
    applyCamera(false);
    endGesture();
  }}, {{ passive: false }});

  // Safari 触控板捏合 gesture 事件兜底
  let safariGestureStartK = 1;
  stageEl.addEventListener('gesturestart', (e) => {{
    e.preventDefault();
    safariGestureStartK = cam.k;
    beginGesture();
  }}, {{ passive: false }});
  stageEl.addEventListener('gesturechange', (e) => {{
    e.preventDefault();
    const rect = stageEl.getBoundingClientRect();
    const px = rect.width / 2;
    const py = rect.height / 2;
    const nextK = Math.max(minScale(), Math.min(2.0, safariGestureStartK * e.scale));
    const ratio = nextK / cam.k;
    cam.tx = px - (px - cam.tx) * ratio;
    cam.ty = py - (py - cam.ty) * ratio;
    cam.k = nextK;
    applyCamera(false);
  }}, {{ passive: false }});
  stageEl.addEventListener('gestureend', () => endGesture());

  // 画布点击：节点选中、折叠钮展开收起、点击空白取消选中
  stageEl.addEventListener('click', (e) => {{
    if (stageEl.dataset.justDragged === '1') return;
    if (e.target.closest('.hud-controls, .detail-bar, .export-modal')) return;
    const tog = e.target.closest('.mm-toggle');
    if (tog) {{
      e.stopPropagation();
      const id = tog.dataset.toggleId;
      const set = activeCollapseSet();
      if (set.has(id)) set.delete(id);
      else set.add(id);
      renderMap(id, true);
      if (viewMode === 'outline') renderOutline();
      return;
    }}
    const nodeBtn = e.target.closest('.mm-node');
    if (nodeBtn) {{
      const id = nodeBtn.dataset.nodeId;
      selectedId = (selectedId === id) ? null : id;
      updateMetaAndStyles();
      return;
    }}
    if (selectedId) {{
      selectedId = null;
      updateMetaAndStyles();
    }}
  }});

  stageEl.addEventListener('dblclick', (e) => {{
    if (e.target.closest('.hud-controls, .detail-bar, .export-modal')) return;
    const nodeBtn = e.target.closest('.mm-node');
    if (nodeBtn) {{
      const id = nodeBtn.dataset.nodeId;
      const n = nodeById.get(id);
      if (n && n.children.length > 0) {{
        const set = activeCollapseSet();
        if (set.has(id)) set.delete(id);
        else set.add(id);
        renderMap(id, true);
      }}
      return;
    }}
    fitView({{ maxK: 1.0, minK: 0.6 }});
  }});

  // 键盘导航与快捷键
  stageEl.addEventListener('keydown', (e) => {{
    if (e.key === '+' || e.key === '=') {{
      e.preventDefault();
      zoomAtCenter(1.18);
    }} else if (e.key === '-' || e.key === '_') {{
      e.preventDefault();
      zoomAtCenter(1 / 1.18);
    }} else if (e.key === '0') {{
      e.preventDefault();
      fitView({{ maxK: 1.0, minK: 0.6 }});
    }} else if (e.key === 'Escape') {{
      selectedId = null;
      exportModal.hidden = true;
      updateMetaAndStyles();
    }} else if (['ArrowRight', 'ArrowLeft', 'ArrowUp', 'ArrowDown', 'Enter', ' '].includes(e.key)) {{
      e.preventDefault();
      const cur = nodeById.get(selectedId || ROOT.id);
      if (!cur) return;
      const set = activeCollapseSet();
      if (e.key === 'Enter' || e.key === ' ') {{
        if (cur.children.length > 0) {{
          if (set.has(cur.id)) set.delete(cur.id);
          else set.add(cur.id);
          renderMap(cur.id, true);
        }}
      }} else if (e.key === 'ArrowRight') {{
        if (cur.children.length > 0) {{
          if (set.has(cur.id)) {{ set.delete(cur.id); renderMap(cur.id, true); }}
          else {{ selectedId = cur.children[0].id; updateMetaAndStyles(); focusNodeOnCanvas(selectedId, 0.8); }}
        }}
      }} else if (e.key === 'ArrowLeft') {{
        if (cur.children.length > 0 && !set.has(cur.id)) {{
          set.add(cur.id);
          renderMap(cur.id, true);
        }} else if (cur.parent) {{
          selectedId = cur.parent.id;
          updateMetaAndStyles();
          focusNodeOnCanvas(selectedId, 0.8);
        }}
      }} else if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && cur.parent) {{
        const sibs = cur.parent.children;
        const idx = sibs.indexOf(cur);
        const nextIdx = e.key === 'ArrowDown' ? Math.min(sibs.length - 1, idx + 1) : Math.max(0, idx - 1);
        selectedId = sibs[nextIdx].id;
        updateMetaAndStyles();
        focusNodeOnCanvas(selectedId, 0.8);
      }}
    }}
  }});

  function zoomAtCenter(factor) {{
    const vw = stageEl.clientWidth || 728;
    const vh = stageEl.clientHeight || {STAGE_HEIGHT_DESKTOP};
    const px = vw / 2, py = vh / 2;
    const nextK = Math.max(minScale(), Math.min(2.0, cam.k * factor));
    const ratio = nextK / cam.k;
    animateCameraTo(px - (px - cam.tx) * ratio, py - (py - cam.ty) * ratio, nextK, false);
  }}

  document.getElementById('btn-zoom-in').addEventListener('click', () => zoomAtCenter(1.2));
  document.getElementById('btn-zoom-out').addEventListener('click', () => zoomAtCenter(1 / 1.2));
  document.getElementById('btn-fit').addEventListener('click', () => fitView({{ maxK: 1.0, minK: 0.6 }}));
  document.getElementById('btn-reset-depth').addEventListener('click', () => {{
    searchCollapsed.clear();
    if (query) {{ searchEl.value = ''; query = ''; normalizedQuery = ''; matchIds = []; }}
    selectedId = null;
    applyBudgetDefaultCollapse();
    renderMap(null, true);
    fitView({{ maxK: 1.0, minK: 0.6, leftAlign: true }});
    if (viewMode === 'outline') renderOutline();
  }});

  btnViewMap.addEventListener('click', () => {{
    viewMode = 'map';
    btnViewMap.setAttribute('aria-pressed', 'true');
    btnViewOutline.setAttribute('aria-pressed', 'false');
    stageEl.hidden = false;
    outlineEl.hidden = true;
    renderMap(null, false);
  }});
  btnViewOutline.addEventListener('click', () => {{
    viewMode = 'outline';
    btnViewMap.setAttribute('aria-pressed', 'false');
    btnViewOutline.setAttribute('aria-pressed', 'true');
    stageEl.hidden = true;
    outlineEl.hidden = false;
    renderOutline();
  }});

  btnExpand.addEventListener('click', () => {{
    const set = activeCollapseSet();
    let anyCollapsed = false;
    for (const n of nodeById.values()) {{
      if (n.children.length > 0 && set.has(n.id)) {{ anyCollapsed = true; break; }}
    }}
    if (anyCollapsed) {{
      set.clear();
    }} else {{
      for (const n of nodeById.values()) {{
        if (n.depth >= 1 && n.children.length > 0) set.add(n.id);
      }}
    }}
    renderMap(null, true);
    fitView({{ maxK: 1.0, minK: 0.6, leftAlign: !anyCollapsed }});
    if (viewMode === 'outline') renderOutline();
  }});

  outlineEl.addEventListener('click', (e) => {{
    const row = e.target.closest('[data-ol-toggle]');
    if (!row) return;
    const id = row.dataset.olToggle;
    const set = activeCollapseSet();
    if (set.has(id)) set.delete(id);
    else set.add(id);
    renderOutline();
    updateExpandButtonLabel();
  }});

  // IME 安全搜索框（同时兼容 Safari WebKit bug 165004 的 keyCode 229 与 timestamp 保护）
  searchEl.addEventListener('compositionstart', () => {{ isComposing = true; }});
  searchEl.addEventListener('compositionend', (e) => {{
    isComposing = false;
    lastCompositionEndAt = e.timeStamp;
    runSearch(e.target.value);
  }});
  searchEl.addEventListener('input', (e) => {{
    if (isComposing || e.isComposing) return;
    runSearch(e.target.value);
  }});
  searchEl.addEventListener('keydown', (e) => {{
    if (e.key === 'Enter') {{
      e.preventDefault();
      if (isComposing || e.isComposing || e.keyCode === 229) return;
      if (lastCompositionEndAt !== null && Math.abs(e.timeStamp - lastCompositionEndAt) < 50) return;
      if (matchIds.length > 0) {{
        matchCursor = e.shiftKey
          ? (matchCursor - 1 + matchIds.length) % matchIds.length
          : (matchCursor + 1) % matchIds.length;
        selectedId = matchIds[matchCursor];
        updateMetaAndStyles();
        if (viewMode === 'map') focusNodeOnCanvas(selectedId, 0.85);
      }}
    }}
  }});

  async function copyText(txt, okMsg) {{
    try {{
      await navigator.clipboard.writeText(txt);
      setStatus(okMsg);
    }} catch {{
      setStatus('当前沙箱未授权剪贴板写入');
    }}
  }}
  document.getElementById('btn-copy').addEventListener('click', () => copyText(toMarkdown(ROOT, 0), '已复制 Markdown 大纲到剪贴板'));
  document.getElementById('btn-copy-node').addEventListener('click', () => {{
    if (selectedId && nodeById.has(selectedId)) copyText(nodeById.get(selectedId).name, '已复制当前节点文本');
  }});

  // 高清 PNG 导出：使用 Range.getClientRects 读取 DOM 真实折行坐标，按 16.7M 像素上限控制倍率，下载受限时降级为浮层保存
  function extractDomLines(textEl) {{
    const lines = [];
    const walker = document.createTreeWalker(textEl, NodeFilter.SHOW_TEXT);
    const parentRect = textEl.getBoundingClientRect();
    const scale = cam.k || 1;
    let node;
    while ((node = walker.nextNode())) {{
      const val = node.nodeValue || '';
      if (!val) continue;
      let start = 0;
      let prevTop = null;
      let buf = '';
      let lineX = 0;
      let lineY = 0;
      for (let i = 0; i < val.length; i++) {{
        const r = document.createRange();
        r.setStart(node, i);
        r.setEnd(node, i + 1);
        const cr = r.getClientRects()[0];
        if (!cr) continue;
        const top = Math.round((cr.top - parentRect.top) / scale);
        const left = (cr.left - parentRect.left) / scale;
        if (prevTop !== null && Math.abs(top - prevTop) > 4) {{
          lines.push({{ text: buf, x: lineX, y: lineY }});
          buf = val[i];
          lineX = left;
          lineY = top + (cr.height / scale) * 0.78;
          prevTop = top;
        }} else {{
          if (prevTop === null) {{
            lineX = left;
            lineY = top + (cr.height / scale) * 0.78;
            prevTop = top;
          }}
          buf += val[i];
        }}
        start = i + 1;
      }}
      if (buf) lines.push({{ text: buf, x: lineX, y: lineY }});
    }}
    return lines;
  }}

  function exportPng() {{
    if (!currentLayout) return;
    try {{
      const b = currentLayout.bounds;
      const pad = 36;
      const logicalW = Math.ceil(b.w + pad * 2);
      const logicalH = Math.ceil(b.h + pad * 2);
      const maxPixels = 16700000;
      let dpr = 2;
      if (logicalW * logicalH * dpr * dpr > maxPixels) {{
        dpr = Math.max(1, Math.floor(Math.sqrt(maxPixels / Math.max(logicalW * logicalH, 1)) * 10) / 10);
      }}
      const canvas = document.createElement('canvas');
      canvas.width = Math.ceil(logicalW * dpr);
      canvas.height = Math.ceil(logicalH * dpr);
      const ctx = canvas.getContext('2d');
      ctx.scale(dpr, dpr);

      const stageStyle = getComputedStyle(stageEl);
      ctx.fillStyle = stageStyle.backgroundColor || '#ffffff';
      ctx.fillRect(0, 0, logicalW, logicalH);
      ctx.translate(pad - b.minX, pad - b.minY);

      // 1. 绘制连线
      for (const e of currentLayout.edges) {{
        const key = e.source.id + '->' + e.target.id;
        const pEl = linkEls.get(key);
        const pStyle = pEl ? getComputedStyle(pEl) : null;
        ctx.beginPath();
        const x1 = e.source.x + e.source.w + 10;
        const y1 = e.source.cy;
        const x2 = e.target.x;
        const y2 = e.target.cy;
        const mx = x1 + (x2 - x1) * 0.5;
        ctx.moveTo(x1, y1);
        ctx.bezierCurveTo(mx, y1, mx, y2, x2, y2);
        ctx.strokeStyle = (pStyle && pStyle.stroke && pStyle.stroke !== 'none') ? pStyle.stroke : '#94a3b8';
        ctx.lineWidth = 1.6;
        ctx.stroke();
      }}

      // 2. 绘制圆角胶囊节点与 DOM 精确折行文字
      for (const [id, r] of currentLayout.placed.entries()) {{
        const wrap = nodeEls.get(id);
        const btn = wrap && wrap.firstElementChild;
        if (!btn) continue;
        const st = getComputedStyle(btn);
        const rad = parseFloat(st.borderTopLeftRadius) || 10;
        ctx.beginPath();
        ctx.roundRect(r.x, r.y, r.w, r.h, rad);
        ctx.fillStyle = st.backgroundColor || '#ffffff';
        ctx.fill();
        ctx.lineWidth = 1.2;
        ctx.strokeStyle = st.borderColor || '#cbd5e1';
        ctx.stroke();

        const txtEl = btn.querySelector('.mm-text');
        const txtSt = getComputedStyle(txtEl);
        const padL = parseFloat(st.paddingLeft) || 12;
        const padT = parseFloat(st.paddingTop) || 8;
        ctx.font = txtSt.fontWeight + ' ' + txtSt.fontSize + ' ' + txtSt.fontFamily;
        ctx.fillStyle = txtSt.color || '#18181b';
        const lines = extractDomLines(txtEl);
        if (lines.length > 0) {{
          for (const ln of lines) ctx.fillText(ln.text, r.x + padL + ln.x, r.y + padT + ln.y);
        }} else {{
          ctx.fillText(r.node.name, r.x + padL, r.y + r.h / 2 + 5);
        }}
      }}

      const dataUrl = canvas.toDataURL('image/png');
      exportImg.src = dataUrl;
      exportModal.hidden = false;
      try {{
        const a = document.createElement('a');
        a.href = dataUrl;
        a.download = 'notebooklm-mind-map.png';
        a.click();
        setStatus('已生成高清 PNG（如未自动下载，可在弹层右键保存）');
      }} catch {{
        setStatus('已生成高清 PNG，请右键或长按图片保存');
      }}
    }} catch (err) {{
      setStatus('当前环境无法导出 PNG');
    }}
  }}

  document.getElementById('btn-export-png').addEventListener('click', exportPng);
  document.getElementById('btn-close-export').addEventListener('click', () => {{ exportModal.hidden = true; }});

  // 初始化：等字体就绪后全量测量 -> 首屏预算展开 -> 1.0x 左对齐居中渲染 + ResizeObserver 响应宿主变动
  function boot() {{
    measureAllNodes();
    applyBudgetDefaultCollapse();
    renderMap(null, false);
    fitView({{ maxK: 1.0, minK: 0.6, leftAlign: true, instant: true }});

    if (typeof ResizeObserver !== 'undefined') {{
      let lastW = stageEl.clientWidth;
      const ro = new ResizeObserver(() => {{
        const curW = stageEl.clientWidth;
        if (curW > 0 && Math.abs(curW - lastW) > 12) {{
          lastW = curW;
          measureAllNodes();
          renderMap(null, false);
        }}
      }});
      ro.observe(stageEl);
    }}
  }}

  if (document.fonts && document.fonts.ready) {{
    document.fonts.ready.then(boot, boot);
  }} else {{
    boot();
  }}
}})();
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
