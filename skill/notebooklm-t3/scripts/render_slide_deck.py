#!/usr/bin/env python3
"""Render a directory of slide images (PNG/JPG) into a T3 Code themed interactive slide viewer HTML.

All slide <img> tags are emitted statically with absolute paths so T3 Code's html_preview /
html_render inliner converts each image once into a data URI, and JS clones those already-inlined
srcs into thumbnails for zero broken images and instant page switching.
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path
from typing import Any


def _natural_key(path: Path) -> list[Any]:
    return [int(tok) if tok.isdigit() else tok.lower() for tok in re.split(r"(\d+)", path.name)]


def build_html(title: str, image_paths: list[Path]) -> str:
    safe_title = html.escape(title)
    total = len(image_paths)
    stage_imgs = "\n".join(
        f'    <img class="slide-img{" active" if i == 0 else ""}" data-slide="{i}" src="{html.escape(str(p.resolve()), quote=True)}" alt="第 {i + 1} 页" />'
        for i, p in enumerate(image_paths)
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{safe_title}</title>
<style>
  :root {{
    --c-fg: var(--foreground, #e6edf3);
    --c-muted: var(--muted-foreground, #8b949e);
    --c-card: var(--card, rgba(127, 127, 127, 0.08));
    --c-sec: var(--secondary, rgba(127, 127, 127, 0.14));
    --c-bd: var(--border, rgba(127, 127, 127, 0.28));
    --c-acc: var(--primary, var(--accent, #3b82f6));
    --c-acc-fg: var(--primary-foreground, #ffffff);
    --c-acc-bg: var(--accent-surface, color-mix(in srgb, var(--c-acc) 14%, transparent));
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
    line-height: 1.45;
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
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  .counter {{
    font-size: 12px;
    color: var(--c-muted);
    font-family: var(--font-code);
  }}
  .stage {{
    width: 100%;
    background: var(--c-card);
    border: 1px solid var(--c-bd);
    border-radius: var(--r);
    overflow: hidden;
    aspect-ratio: 16 / 9;
    position: relative;
  }}
  .slide-img {{
    display: none;
    width: 100%;
    height: 100%;
    object-fit: contain;
  }}
  .slide-img.active {{
    display: block;
  }}
  .controls {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    flex-wrap: wrap;
  }}
  .btn-group {{
    display: flex;
    align-items: center;
    gap: 6px;
  }}
  .btn {{
    appearance: none;
    border: 1px solid var(--c-bd);
    background: transparent;
    color: var(--c-fg);
    font: inherit;
    font-size: 12px;
    font-weight: 500;
    padding: 5px 11px;
    border-radius: 7px;
    cursor: pointer;
  }}
  .btn:hover:not(:disabled) {{ border-color: var(--c-acc); }}
  .btn:disabled {{ opacity: 0.45; cursor: not-allowed; }}
  .btn.primary {{
    background: var(--c-acc);
    border-color: var(--c-acc);
    color: var(--c-acc-fg);
  }}
  .tip {{
    font-size: 11.5px;
    color: var(--c-muted);
  }}
  .thumbs {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
    gap: 6px;
  }}
  .thumb {{
    appearance: none;
    padding: 0;
    border: 1.5px solid var(--c-bd);
    border-radius: 6px;
    overflow: hidden;
    background: var(--c-card);
    cursor: pointer;
    position: relative;
    aspect-ratio: 16 / 9;
  }}
  .thumb.active {{
    border-color: var(--c-acc);
    box-shadow: 0 0 0 1px var(--c-acc);
  }}
  .thumb img {{
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }}
  .thumb-num {{
    position: absolute;
    right: 4px;
    bottom: 3px;
    font-size: 10px;
    font-family: var(--font-code);
    padding: 0 4px;
    border-radius: 4px;
    background: rgba(0, 0, 0, 0.7);
    color: #fff;
  }}
</style>
</head>
<body>
<div class="root">
  <div class="head">
    <div class="head-left">
      <span class="tag">Slide Deck</span>
      <span class="title">{safe_title}</span>
    </div>
    <span id="deck-counter" class="counter">1 / {total}</span>
  </div>
  <div class="stage" id="stage">
{stage_imgs}
  </div>
  <div class="controls">
    <span class="tip">支持键盘左右方向键翻页或点击下方缩略图跳转</span>
    <div class="btn-group">
      <button type="button" class="btn" id="btn-prev" disabled>上一页</button>
      <button type="button" class="btn primary" id="btn-next"{" disabled" if total <= 1 else ""}>下一页</button>
    </div>
  </div>
  <div id="thumbs" class="thumbs"></div>
</div>
<script>
const slides = Array.from(document.querySelectorAll('.slide-img'));
const counterEl = document.getElementById('deck-counter');
const prevBtn = document.getElementById('btn-prev');
const nextBtn = document.getElementById('btn-next');
const thumbsEl = document.getElementById('thumbs');
let idx = 0;

const thumbButtons = slides.map((slideImg, i) => {{
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'thumb' + (i === 0 ? ' active' : '');
  btn.setAttribute('aria-label', `第 ${{i + 1}} 页`);
  const img = document.createElement('img');
  img.src = slideImg.getAttribute('src') || slideImg.src;
  img.alt = `第 ${{i + 1}} 页缩略图`;
  const num = document.createElement('span');
  num.className = 'thumb-num';
  num.textContent = String(i + 1);
  btn.appendChild(img);
  btn.appendChild(num);
  btn.addEventListener('click', () => {{ idx = i; render(); }});
  thumbsEl.appendChild(btn);
  return btn;
}});

function render() {{
  slides.forEach((img, i) => img.classList.toggle('active', i === idx));
  thumbButtons.forEach((b, i) => b.classList.toggle('active', i === idx));
  counterEl.textContent = `${{idx + 1}} / ${{slides.length}}`;
  prevBtn.disabled = idx <= 0;
  nextBtn.disabled = idx >= slides.length - 1;
}}

prevBtn.addEventListener('click', () => {{ if (idx > 0) {{ idx -= 1; render(); }} }});
nextBtn.addEventListener('click', () => {{ if (idx < slides.length - 1) {{ idx += 1; render(); }} }});

window.addEventListener('keydown', (e) => {{
  const tag = (e.target && e.target.tagName) ? e.target.tagName.toLowerCase() : '';
  if (tag === 'input' || tag === 'textarea') return;
  if (e.key === 'ArrowLeft' && idx > 0) {{ idx -= 1; render(); }}
  else if (e.key === 'ArrowRight' && idx < slides.length - 1) {{ idx += 1; render(); }}
}});
</script>
</body>
</html>
"""


def main() -> int:
    if len(sys.argv) < 3 or len(sys.argv) > 4:
        print("Usage: render_slide_deck.py <images_dir> <output.html> [title]", file=sys.stderr)
        return 2
    img_dir = Path(sys.argv[1])
    out_html = Path(sys.argv[2])
    title = sys.argv[3] if len(sys.argv) == 4 else "NotebookLM Slide Deck"

    exts = {".png", ".jpg", ".jpeg", ".webp"}
    images = (
        sorted(
            [p for p in img_dir.iterdir() if p.is_file() and p.suffix.lower() in exts],
            key=_natural_key,
        )
        if img_dir.is_dir()
        else []
    )

    if not images:
        print(f"ERROR: No slide images found in {img_dir}", file=sys.stderr)
        return 1

    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(build_html(title, images), encoding="utf-8")
    print(f"OK: rendered slide deck ({len(images)} slides) -> {out_html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
