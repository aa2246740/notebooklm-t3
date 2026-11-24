#!/usr/bin/env python3
"""Compile NotebookLM Quiz (4 question types) or Flashcards JSON into a T3 Code themed HTML card."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return False


def _safe_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = _safe_str(item)
        if text:
            out.append(text)
    return out


def _extract_block_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        blocks = value.get("flashcardContentBlock")
        if isinstance(blocks, list):
            parts = []
            for blk in blocks:
                if isinstance(blk, dict):
                    txt = _safe_str(blk.get("content") or blk.get("text"))
                    if txt:
                        parts.append(txt)
                else:
                    txt = _safe_str(blk)
                    if txt:
                        parts.append(txt)
            return "\n\n".join(parts)
        return _safe_str(value.get("content") or value.get("text"))
    if isinstance(value, list):
        parts = [_extract_block_text(item) for item in value]
        return "\n\n".join(p for p in parts if p)
    return _safe_str(value)


def _safe_inline_json(payload: Any) -> str:
    return (
        json.dumps(payload, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _parse_quiz(data: dict[str, Any]) -> tuple[dict[str, Any] | None, int]:
    raw_questions = data.get("questions")
    if not isinstance(raw_questions, list):
        raw_questions = data.get("quiz")
    if not isinstance(raw_questions, list) or not raw_questions:
        return None, 0

    questions: list[dict[str, Any]] = []
    skipped = 0
    for item in raw_questions:
        if not isinstance(item, dict):
            skipped += 1
            continue
        prompt = _safe_str(item.get("question") or item.get("prompt"))
        if not prompt:
            skipped += 1
            continue
        q_type = _safe_str(item.get("type")).lower()
        hint = _safe_str(item.get("hint"))

        raw_opts = item.get("answerOptions")
        if not isinstance(raw_opts, list):
            raw_opts = item.get("options")

        if isinstance(raw_opts, list) and raw_opts:
            options: list[dict[str, Any]] = []
            for opt in raw_opts:
                if not isinstance(opt, dict):
                    continue
                text = _safe_str(opt.get("text") or opt.get("label"))
                if not text:
                    continue
                options.append(
                    {
                        "text": text,
                        "isCorrect": _safe_bool(opt.get("isCorrect", False)),
                        "rationale": _safe_str(opt.get("rationale") or opt.get("explanation")),
                    }
                )
            if not options:
                skipped += 1
                continue
            correct_cnt = sum(1 for o in options if o["isCorrect"])
            resolved_type = "multiple_select" if (q_type == "multiple_select" or correct_cnt > 1) else "multiple_choice"
            questions.append(
                {
                    "type": resolved_type,
                    "question": prompt,
                    "options": options,
                    "hint": hint,
                }
            )
            continue

        if q_type == "fill_in_the_blank" or item.get("bestAnswer") is not None:
            best = _safe_str(item.get("bestAnswer"))
            acceptable = _safe_str_list(item.get("acceptableAnswers"))
            if best or acceptable:
                questions.append(
                    {
                        "type": "fill_in_the_blank",
                        "question": prompt,
                        "bestAnswer": best or acceptable[0],
                        "acceptableAnswers": acceptable,
                        "rationale": _safe_str(item.get("rationale")),
                        "hint": hint,
                    }
                )
                continue

        grading = item.get("grading") if isinstance(item.get("grading"), dict) else {}
        model_answer = _safe_str(grading.get("modelAnswer") or item.get("modelAnswer") or item.get("answer"))
        if q_type == "short_answer" or model_answer:
            questions.append(
                {
                    "type": "short_answer",
                    "question": prompt,
                    "modelAnswer": model_answer or "(无参考答案)",
                    "rationale": _safe_str(grading.get("rationale") or item.get("rationale")),
                    "requiredAttributes": _safe_str_list(grading.get("requiredAttributes")),
                    "hint": hint,
                }
            )
            continue

        skipped += 1

    if not questions:
        return None, skipped

    return (
        {
            "kind": "quiz",
            "title": _safe_str(data.get("title"), "NotebookLM Quiz"),
            "items": questions,
        },
        skipped,
    )


def _parse_flashcards(data: dict[str, Any]) -> tuple[dict[str, Any] | None, int]:
    raw_cards = data.get("cards")
    if not isinstance(raw_cards, list):
        raw_cards = data.get("flashcards")
    if not isinstance(raw_cards, list) or not raw_cards:
        return None, 0

    cards: list[dict[str, str]] = []
    skipped = 0
    for item in raw_cards:
        if not isinstance(item, dict):
            skipped += 1
            continue
        raw_front = item.get("front") if "front" in item else item.get("f")
        raw_back = item.get("back") if "back" in item else item.get("b")
        front = _extract_block_text(raw_front)
        back = _extract_block_text(raw_back)
        if not front and not back:
            skipped += 1
            continue
        cards.append({"front": front or "(空)", "back": back or "(空)"})

    if not cards:
        return None, skipped

    return (
        {
            "kind": "flashcards",
            "title": _safe_str(data.get("title"), "NotebookLM Flashcards"),
            "items": cards,
        },
        skipped,
    )


def load_payload(src: Path) -> tuple[dict[str, Any], int]:
    parsed = json.loads(src.read_text(encoding="utf-8"))
    if isinstance(parsed, list):
        if parsed and isinstance(parsed[0], dict) and (
            "answerOptions" in parsed[0] or "question" in parsed[0] or "bestAnswer" in parsed[0]
        ):
            parsed = {"questions": parsed}
        else:
            parsed = {"cards": parsed}
    if not isinstance(parsed, dict):
        raise ValueError("JSON root must be an object or non-empty array")

    payload, skipped = _parse_quiz(parsed)
    if payload is None:
        payload, skipped = _parse_flashcards(parsed)
    if payload is None:
        raise ValueError(
            "Unrecognized quiz/flashcards JSON schema; fall back to downloading --format markdown"
        )
    return payload, skipped


def build_html(payload: dict[str, Any]) -> str:
    safe_json = _safe_inline_json(payload)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>NotebookLM Study Card</title>
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
    --c-ok: var(--success, #22c55e);
    --c-ok-bg: color-mix(in srgb, var(--c-ok) 12%, transparent);
    --c-err: var(--destructive, #ef4444);
    --c-err-bg: var(--destructive-surface, color-mix(in srgb, var(--c-err) 14%, transparent));
    --c-warn: var(--warning, #eab308);
    --c-warn-bg: var(--warning-surface, color-mix(in srgb, var(--c-warn) 14%, transparent));
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
    font-size: 14px;
    line-height: 1.55;
    -webkit-font-smoothing: antialiased;
  }}
  .root {{
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }}
  .head {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
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
  .q-badge {{
    font-size: 11px;
    padding: 1px 7px;
    border-radius: 999px;
    background: var(--c-sec);
    color: var(--c-muted);
    display: inline-block;
    margin-bottom: 4px;
  }}
  .title {{
    font-size: 15px;
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  .stats {{
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 12px;
    color: var(--c-muted);
    font-family: var(--font-code);
  }}
  .progress {{
    width: 100%;
    height: 4px;
    border-radius: 999px;
    background: var(--c-sec);
    overflow: hidden;
  }}
  .progress-bar {{
    height: 100%;
    background: var(--c-acc);
    transition: width 180ms ease;
  }}
  .notice {{
    font-size: 12px;
    padding: 6px 10px;
    border-radius: 7px;
    background: var(--c-ok-bg);
    border: 1px solid var(--c-ok);
    color: var(--c-fg);
  }}
  .dots {{
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }}
  .dot {{
    appearance: none;
    min-width: 26px;
    height: 24px;
    padding: 0 6px;
    border-radius: 6px;
    border: 1px solid var(--c-bd);
    background: transparent;
    color: var(--c-muted);
    font-family: var(--font-code);
    font-size: 11px;
    cursor: pointer;
  }}
  .dot.active {{
    border-color: var(--c-acc);
    color: var(--c-fg);
    background: var(--c-acc-bg);
    font-weight: 600;
  }}
  .dot.ok {{ border-color: var(--c-ok); color: var(--c-ok); }}
  .dot.err {{ border-color: var(--c-err); color: var(--c-err); }}
  .panel {{
    background: var(--c-card);
    border: 1px solid var(--c-bd);
    border-radius: var(--r);
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }}
  .q-text {{
    font-size: 15px;
    font-weight: 600;
    line-height: 1.5;
  }}
  .opts {{
    display: flex;
    flex-direction: column;
    gap: 8px;
  }}
  .opt {{
    appearance: none;
    width: 100%;
    text-align: left;
    padding: 10px 12px;
    border-radius: 8px;
    border: 1px solid var(--c-bd);
    background: transparent;
    color: var(--c-fg);
    font: inherit;
    cursor: pointer;
    display: flex;
    gap: 10px;
    align-items: flex-start;
    transition: border-color 140ms ease, background-color 140ms ease;
  }}
  .opt:hover:not(:disabled) {{
    border-color: var(--c-acc);
    background: var(--c-acc-bg);
  }}
  .opt.picked {{
    border-color: var(--c-acc);
    background: var(--c-acc-bg);
  }}
  .opt:disabled {{ cursor: default; }}
  .opt-key {{
    width: 22px;
    height: 22px;
    border-radius: 5px;
    border: 1px solid var(--c-bd);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-family: var(--font-code);
    font-size: 11px;
    flex-shrink: 0;
    margin-top: 1px;
  }}
  .opt-body {{ flex: 1; min-width: 0; word-break: break-word; }}
  .opt-rationale {{
    margin-top: 6px;
    font-size: 12px;
    color: var(--c-muted);
    line-height: 1.45;
  }}
  .opt.correct {{
    border-color: var(--c-ok);
    background: var(--c-ok-bg);
  }}
  .opt.correct .opt-key {{
    border-color: var(--c-ok);
    color: var(--c-ok);
    font-weight: 700;
  }}
  .opt.wrong {{
    border-color: var(--c-err);
    background: var(--c-err-bg);
  }}
  .opt.wrong .opt-key {{
    border-color: var(--c-err);
    color: var(--c-err);
    font-weight: 700;
  }}
  .input-row {{
    display: flex;
    gap: 8px;
    align-items: stretch;
  }}
  .ctrl {{
    width: 100%;
    background: transparent;
    color: var(--c-fg);
    border: 1px solid var(--c-bd);
    border-radius: 8px;
    padding: 8px 11px;
    font: inherit;
    outline: none;
  }}
  .ctrl:focus {{ border-color: var(--c-acc); }}
  textarea.ctrl {{
    min-height: 76px;
    resize: vertical;
  }}
  .feedback {{
    padding: 10px 12px;
    border-radius: 8px;
    border: 1px solid var(--c-bd);
    background: var(--c-sec);
    font-size: 13px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }}
  .feedback.ok {{
    border-color: var(--c-ok);
    background: var(--c-ok-bg);
  }}
  .feedback.err {{
    border-color: var(--c-err);
    background: var(--c-err-bg);
  }}
  .feedback ul {{
    margin: 2px 0 0 18px;
    padding: 0;
  }}
  .hint-box {{
    padding: 10px 12px;
    border-radius: 8px;
    background: var(--c-warn-bg);
    border: 1px solid var(--c-warn);
    font-size: 13px;
  }}
  .fc-stage {{
    min-height: 168px;
    background: var(--c-card);
    border: 1px solid var(--c-bd);
    border-radius: var(--r);
    padding: 20px 16px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 14px;
    cursor: pointer;
    user-select: none;
  }}
  .fc-side {{
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--c-muted);
    display: flex;
    justify-content: space-between;
  }}
  .fc-content {{
    font-size: 16px;
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: break-word;
  }}
  .fc-tip {{
    font-size: 12px;
    color: var(--c-muted);
  }}
  .foot {{
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
    flex-wrap: wrap;
  }}
  .btn {{
    appearance: none;
    border: 1px solid var(--c-bd);
    background: transparent;
    color: var(--c-fg);
    font: inherit;
    font-size: 12px;
    font-weight: 500;
    padding: 6px 11px;
    border-radius: 7px;
    cursor: pointer;
    white-space: nowrap;
  }}
  .btn:hover:not(:disabled) {{ border-color: var(--c-acc); }}
  .btn:disabled {{ opacity: 0.45; cursor: not-allowed; }}
  .btn.primary {{
    background: var(--c-acc);
    border-color: var(--c-acc);
    color: var(--c-acc-fg);
  }}
  .btn.ok {{ border-color: var(--c-ok); color: var(--c-ok); }}
  .btn.warn {{ border-color: var(--c-warn); color: var(--c-warn); }}
</style>
</head>
<body>
<div id="app" class="root"></div>
<script>
const DATA = {safe_json};
const app = document.getElementById('app');

function esc(s) {{
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
}}

function normAnswer(s) {{
  const raw = String(s ?? '').normalize('NFKC').toLowerCase().trim();
  const cleaned = raw
    .replace(/[^\\p{{L}}\\p{{N}}+#\\s]/gu, ' ')
    .replace(/\\s+/g, ' ')
    .trim();
  return cleaned || raw;
}}

const TYPE_LABELS = {{
  multiple_choice: '单选题',
  multiple_select: '多选题',
  fill_in_the_blank: '填空题',
  short_answer: '简答题',
}};

if (DATA.kind === 'quiz') {{
  let idx = 0;
  let onlyWrong = false;
  const results = new Map();
  const drafts = new Map();
  const hints = new Set();

  function visibleIndices() {{
    const all = DATA.items.map((_, i) => i);
    if (!onlyWrong) return all;
    const wrong = all.filter((i) => results.has(i) && !results.get(i).correct);
    return wrong.length ? wrong : all;
  }}

  function score() {{
    let ok = 0;
    for (const res of results.values()) if (res.correct) ok += 1;
    return ok;
  }}

  function renderQuiz() {{
    const indices = visibleIndices();
    if (!indices.includes(idx)) idx = indices[0];
    const pos = indices.indexOf(idx);
    const q = DATA.items[idx];
    const res = results.get(idx) || null;
    const answered = results.size;
    const okCount = score();
    const wrongCount = answered - okCount;
    const pct = Math.round((answered / DATA.items.length) * 100);

    const dotsHtml = indices.map((qi) => {{
      const cls = ['dot'];
      if (qi === idx) cls.push('active');
      if (results.has(qi)) cls.push(results.get(qi).correct ? 'ok' : 'err');
      return `<button type="button" class="${{cls.join(' ')}}" data-jump="${{qi}}">${{qi + 1}}</button>`;
    }}).join('');

    let bodyHtml = '';
    if (q.type === 'multiple_choice' || q.type === 'multiple_select') {{
      const pickedSet = res
        ? new Set(Array.isArray(res.value) ? res.value : [res.value])
        : (drafts.get(idx) instanceof Set ? drafts.get(idx) : new Set());

      const optsHtml = q.options.map((opt, oi) => {{
        const letter = String.fromCharCode(65 + oi);
        const cls = ['opt'];
        const isPicked = pickedSet.has(oi);
        if (res) {{
          if (opt.isCorrect) cls.push('correct');
          else if (isPicked) cls.push('wrong');
        }} else if (isPicked) {{
          cls.push('picked');
        }}
        const showRat = Boolean(res) && opt.rationale && (isPicked || opt.isCorrect);
        return `
          <button type="button" class="${{cls.join(' ')}}" data-opt="${{oi}}" ${{res ? 'disabled' : ''}}>
            <span class="opt-key">${{letter}}</span>
            <span class="opt-body">
              <div>${{esc(opt.text)}}</div>
              ${{showRat ? `<div class="opt-rationale">${{esc(opt.rationale)}}</div>` : ''}}
            </span>
          </button>`;
      }}).join('');

      const submitBtn = q.type === 'multiple_select' && !res
        ? `<div><button type="button" class="btn primary" id="btn-submit-ms" ${{pickedSet.size === 0 ? 'disabled' : ''}}>提交多选 (${{pickedSet.size}})</button></div>`
        : '';
      bodyHtml = `<div class="opts">${{optsHtml}}</div>${{submitBtn}}`;
    }} else if (q.type === 'fill_in_the_blank') {{
      const curVal = res ? res.value : (typeof drafts.get(idx) === 'string' ? drafts.get(idx) : '');
      const fbHtml = res
        ? `<div class="feedback ${{res.correct ? 'ok' : 'err'}}">
             <div><strong>${{res.correct ? '回答正确' : '参考答案：' + esc(q.bestAnswer)}}</strong></div>
             ${{q.rationale ? `<div>${{esc(q.rationale)}}</div>` : ''}}
           </div>`
        : '';
      bodyHtml = `
        <div class="input-row">
          <input type="text" class="ctrl" id="blank-input" placeholder="输入填空答案..." value="${{esc(curVal)}}" ${{res ? 'disabled' : ''}} />
          ${{!res ? `<button type="button" class="btn primary" id="btn-submit-blank">检查</button>` : ''}}
        </div>${{fbHtml}}`;
    }} else {{
      const curVal = typeof drafts.get(idx) === 'string' ? drafts.get(idx) : (res?.value || '');
      const revealed = Boolean(res) || drafts.get(`rev:${{idx}}`) === true;
      const reqs = Array.isArray(q.requiredAttributes) && q.requiredAttributes.length
        ? `<div>评分要点：<ul>${{q.requiredAttributes.map((r) => `<li>${{esc(r)}}</li>`).join('')}}</ul></div>`
        : '';
      const selfGrade = revealed && !res
        ? `<div class="btn-group">
             <button type="button" class="btn ok" id="btn-sa-ok">自评：已掌握</button>
             <button type="button" class="btn warn" id="btn-sa-err">自评：待巩固</button>
           </div>`
        : '';
      const fbHtml = revealed
        ? `<div class="feedback ${{res ? (res.correct ? 'ok' : 'err') : ''}}">
             <div><strong>参考答案：</strong>${{esc(q.modelAnswer)}}</div>
             ${{q.rationale ? `<div>${{esc(q.rationale)}}</div>` : ''}}
             ${{reqs}}
           </div>${{selfGrade}}`
        : `<div><button type="button" class="btn primary" id="btn-reveal-sa">查看参考答案</button></div>`;
      bodyHtml = `
        <textarea class="ctrl" id="sa-input" placeholder="可先写下你的思路，再对照参考答案自评..." ${{res ? 'disabled' : ''}}>${{esc(curVal)}}</textarea>
        ${{fbHtml}}`;
    }}

    app.innerHTML = `
      <div class="head">
        <div class="head-left">
          <span class="tag">Quiz</span>
          <span class="title">${{esc(DATA.title)}}</span>
        </div>
        <div class="stats">
          <span>进度 ${{answered}}/${{DATA.items.length}}</span>
          <span>正确 ${{okCount}}</span>
        </div>
      </div>
      <div class="progress"><div class="progress-bar" style="width:${{pct}}%"></div></div>
      <div class="dots">${{dotsHtml}}</div>
      <div class="panel">
        <div>
          <span class="q-badge">${{TYPE_LABELS[q.type] || '题目'}}</span>
          <div class="q-text">${{idx + 1}}. ${{esc(q.question)}}</div>
        </div>
        ${{bodyHtml}}
        ${{q.hint && hints.has(idx) ? `<div class="hint-box">提示：${{esc(q.hint)}}</div>` : ''}}
      </div>
      <div class="foot">
        <div class="btn-group">
          ${{q.hint ? `<button type="button" class="btn" id="btn-hint">${{hints.has(idx) ? '隐藏提示' : '查看提示'}}</button>` : ''}}
          ${{wrongCount > 0 ? `<button type="button" class="btn warn" id="btn-wrong">${{onlyWrong ? '查看全部' : `仅看错题 (${{wrongCount}})`}}</button>` : ''}}
          ${{answered > 0 ? `<button type="button" class="btn" id="btn-reset">重置</button>` : ''}}
        </div>
        <div class="btn-group">
          <button type="button" class="btn" id="btn-prev" ${{pos <= 0 ? 'disabled' : ''}}>上一题</button>
          <button type="button" class="btn primary" id="btn-next" ${{pos >= indices.length - 1 ? 'disabled' : ''}}>下一题</button>
        </div>
      </div>`;

    app.querySelectorAll('[data-jump]').forEach((el) => el.addEventListener('click', () => {{
      idx = Number(el.dataset.jump);
      renderQuiz();
    }}));
    app.querySelectorAll('[data-opt]').forEach((el) => el.addEventListener('click', () => {{
      if (results.has(idx)) return;
      const oi = Number(el.dataset.opt);
      if (q.type === 'multiple_choice') {{
        results.set(idx, {{ correct: Boolean(q.options[oi]?.isCorrect), value: oi }});
      }} else {{
        const cur = drafts.get(idx) instanceof Set ? drafts.get(idx) : new Set();
        if (cur.has(oi)) cur.delete(oi); else cur.add(oi);
        drafts.set(idx, cur);
      }}
      renderQuiz();
    }}));
    document.getElementById('btn-submit-ms')?.addEventListener('click', () => {{
      const picked = drafts.get(idx) instanceof Set ? drafts.get(idx) : new Set();
      const expected = q.options.map((o, i) => (o.isCorrect ? i : -1)).filter((i) => i >= 0);
      const isOk = picked.size === expected.length && expected.every((i) => picked.has(i));
      results.set(idx, {{ correct: isOk, value: Array.from(picked) }});
      renderQuiz();
    }});
    const blankEl = document.getElementById('blank-input');
    blankEl?.addEventListener('input', (e) => drafts.set(idx, e.target.value));
    const submitBlank = () => {{
      const val = String(drafts.get(idx) || '').trim();
      if (!val) return;
      const normVal = normAnswer(val);
      const accepted = [q.bestAnswer, ...(q.acceptableAnswers || [])].map(normAnswer).filter(Boolean);
      results.set(idx, {{ correct: accepted.includes(normVal), value: val }});
      renderQuiz();
    }};
    document.getElementById('btn-submit-blank')?.addEventListener('click', submitBlank);
    blankEl?.addEventListener('keydown', (e) => {{
      if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) submitBlank();
    }});

    const saEl = document.getElementById('sa-input');
    saEl?.addEventListener('input', (e) => drafts.set(idx, e.target.value));
    document.getElementById('btn-reveal-sa')?.addEventListener('click', () => {{
      drafts.set(`rev:${{idx}}`, true);
      renderQuiz();
    }});
    document.getElementById('btn-sa-ok')?.addEventListener('click', () => {{
      results.set(idx, {{ correct: true, value: drafts.get(idx) || '' }});
      renderQuiz();
    }});
    document.getElementById('btn-sa-err')?.addEventListener('click', () => {{
      results.set(idx, {{ correct: false, value: drafts.get(idx) || '' }});
      renderQuiz();
    }});

    document.getElementById('btn-hint')?.addEventListener('click', () => {{
      if (hints.has(idx)) hints.delete(idx); else hints.add(idx);
      renderQuiz();
    }});
    document.getElementById('btn-wrong')?.addEventListener('click', () => {{
      onlyWrong = !onlyWrong;
      renderQuiz();
    }});
    document.getElementById('btn-reset')?.addEventListener('click', () => {{
      results.clear();
      drafts.clear();
      hints.clear();
      onlyWrong = false;
      idx = 0;
      renderQuiz();
    }});
    document.getElementById('btn-prev')?.addEventListener('click', () => {{
      if (pos > 0) {{ idx = indices[pos - 1]; renderQuiz(); }}
    }});
    document.getElementById('btn-next')?.addEventListener('click', () => {{
      if (pos < indices.length - 1) {{ idx = indices[pos + 1]; renderQuiz(); }}
    }});
  }}
  renderQuiz();
}} else {{
  let order = DATA.items.map((_, i) => i);
  let pos = 0;
  let flipped = false;
  let onlyPending = false;
  const mastered = new Set();

  function activeOrder() {{
    if (!onlyPending) return {{ list: order, fellBack: false }};
    const pending = order.filter((i) => !mastered.has(i));
    if (pending.length) return {{ list: pending, fellBack: false }};
    return {{ list: order, fellBack: true }};
  }}

  function renderCards() {{
    const {{ list, fellBack }} = activeOrder();
    if (pos >= list.length) pos = Math.max(0, list.length - 1);
    const cardIdx = list[pos];
    const card = DATA.items[cardIdx];
    const isDone = mastered.has(cardIdx);
    const pct = Math.round((mastered.size / DATA.items.length) * 100);

    app.innerHTML = `
      <div class="head">
        <div class="head-left">
          <span class="tag">Flashcards</span>
          <span class="title">${{esc(DATA.title)}}</span>
        </div>
        <div class="stats">
          <span>卡片 ${{pos + 1}}/${{list.length}}</span>
          <span>已掌握 ${{mastered.size}}/${{DATA.items.length}}</span>
        </div>
      </div>
      <div class="progress"><div class="progress-bar" style="width:${{pct}}%"></div></div>
      ${{fellBack ? '<div class="notice">全部卡片已标记掌握，当前展示全量卡片供复习。</div>' : ''}}
      <div class="fc-stage" id="fc-stage" tabindex="0" role="button" aria-label="翻转卡片">
        <div class="fc-side">
          <span>${{flipped ? '背面 · 解答' : '正面 · 问题'}}</span>
          <span>${{isDone ? '已掌握' : '待复习'}}</span>
        </div>
        <div class="fc-content">${{esc(flipped ? card.back : card.front)}}</div>
        <div class="fc-tip">点击卡片或按空格键翻面</div>
      </div>
      <div class="foot">
        <div class="btn-group">
          <button type="button" class="btn ${{isDone ? 'ok' : ''}}" id="btn-master">${{isDone ? '取消已掌握' : '标记已掌握'}}</button>
          <button type="button" class="btn" id="btn-shuffle">乱序</button>
          <button type="button" class="btn" id="btn-filter">${{onlyPending ? '全部卡片' : '仅看待复习'}}</button>
        </div>
        <div class="btn-group">
          <button type="button" class="btn" id="btn-prev" ${{pos <= 0 ? 'disabled' : ''}}>上一张</button>
          <button type="button" class="btn primary" id="btn-next" ${{pos >= list.length - 1 ? 'disabled' : ''}}>下一张</button>
        </div>
      </div>`;

    const stageEl = document.getElementById('fc-stage');
    stageEl?.addEventListener('click', () => {{
      flipped = !flipped;
      renderCards();
    }});
    stageEl?.addEventListener('keydown', (e) => {{
      if (e.repeat) return;
      if (e.key === 'Enter' || e.code === 'Space') {{
        e.preventDefault();
        flipped = !flipped;
        renderCards();
        document.getElementById('fc-stage')?.focus();
      }}
    }});
    document.getElementById('btn-master')?.addEventListener('click', () => {{
      if (mastered.has(cardIdx)) mastered.delete(cardIdx);
      else {{
        mastered.add(cardIdx);
        if (onlyPending) flipped = false;
      }}
      renderCards();
    }});
    document.getElementById('btn-shuffle')?.addEventListener('click', () => {{
      for (let i = order.length - 1; i > 0; i--) {{
        const j = Math.floor(Math.random() * (i + 1));
        [order[i], order[j]] = [order[j], order[i]];
      }}
      pos = 0;
      flipped = false;
      renderCards();
    }});
    document.getElementById('btn-filter')?.addEventListener('click', () => {{
      onlyPending = !onlyPending;
      pos = 0;
      flipped = false;
      renderCards();
    }});
    document.getElementById('btn-prev')?.addEventListener('click', () => {{
      if (pos > 0) {{ pos -= 1; flipped = false; renderCards(); }}
    }});
    document.getElementById('btn-next')?.addEventListener('click', () => {{
      if (pos < list.length - 1) {{ pos += 1; flipped = false; renderCards(); }}
    }});
  }}

  window.addEventListener('keydown', (e) => {{
    if (e.repeat || e.code !== 'Space') return;
    const tag = (e.target && e.target.tagName) ? e.target.tagName.toLowerCase() : '';
    if (tag === 'button' || tag === 'input' || tag === 'textarea' || tag === 'select' || e.target?.id === 'fc-stage') {{
      return;
    }}
    e.preventDefault();
    flipped = !flipped;
    renderCards();
  }});
  renderCards();
}}
</script>
</body>
</html>
"""


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: render_study_card.py <input.json> <output.html>", file=sys.stderr)
        return 2
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    try:
        payload, skipped = load_payload(src)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if skipped > 0:
        print(f"WARN: skipped {skipped} unrecognized item(s) in {src.name}", file=sys.stderr)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(build_html(payload), encoding="utf-8")
    print(f"OK: rendered {payload['kind']} ({len(payload['items'])} items) -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
