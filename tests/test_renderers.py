"""三套确定性 HTML 渲染器的结构、4 题型、XSS 对抗与异常退出码回归测试。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skill" / "notebooklm-t3" / "scripts"


def _extract_inline_scripts(html_text: str) -> list[str]:
    return re.findall(r"<script[^>]*>(.*?)</script>", html_text, flags=re.DOTALL | re.IGNORECASE)


class TestRenderers(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.out_dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _assert_js_valid(self, html_path: Path) -> None:
        html_text = html_path.read_text(encoding="utf-8")
        scripts = _extract_inline_scripts(html_text)
        self.assertTrue(len(scripts) >= 1)
        # 无论机器是否安装 node，都必须断言整份 HTML 中 <script>/<\/script> 配对且无未转义恶意标签
        html_lower = html_text.lower()
        self.assertEqual(len(re.findall(r"<script\b", html_lower)), html_lower.count("</script>"))
        self.assertNotIn("<img src=x onerror", html_lower)
        for js in scripts:
            self.assertNotIn("<!--<script>", js.lower())
        node = shutil.which("node")
        if node:
            for idx, js in enumerate(scripts):
                js_file = self.out_dir / f"{html_path.stem}_{idx}.js"
                js_file.write_text(js, encoding="utf-8")
                proc = subprocess.run([node, "--check", str(js_file)], capture_output=True, text=True, check=False)
                self.assertEqual(proc.returncode, 0, f"JS syntax check failed for {html_path}: {proc.stderr}")

    def test_quiz_four_types_and_xss_adversarial(self) -> None:
        quiz_json = self.out_dir / "quiz.json"
        quiz_html = self.out_dir / "quiz.html"
        quiz_json.write_text(
            json.dumps(
                {
                    "title": 'Adversarial <!--<script></script><img src=x onerror=alert(1)> "中文" & false',
                    "questions": [
                        {
                            "questionType": "multiple_choice",
                            "question": "Q1 <!--<script> 测试",
                            "answerOptions": [
                                {"text": "A </script>", "isCorrect": True, "rationale": "ok"},
                                {"text": "B", "isCorrect": False},
                            ],
                        },
                        {
                            "questionType": "multiple_select",
                            "question": "Q2 多选",
                            "answerOptions": [
                                {"text": "C++", "isCorrect": True},
                                {"text": "C#", "isCorrect": True},
                                {"text": "No", "isCorrect": False},
                            ],
                        },
                        {
                            "questionType": "fill_in_the_blank",
                            "question": "Fill ___",
                            "correctAnswer": "C++",
                        },
                        {
                            "questionType": "short_answer",
                            "question": "Explain MCP",
                            "correctAnswer": "Model Context Protocol",
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "render_study_card.py"), str(quiz_json), str(quiz_html)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(quiz_html.is_file())
        self._assert_js_valid(quiz_html)

    def test_flashcards_nested_content_block_and_xss(self) -> None:
        fc_json = self.out_dir / "flashcards.json"
        fc_html = self.out_dir / "flashcards.html"
        fc_json.write_text(
            json.dumps(
                {
                    "title": "闪卡测试 </script><!--<script>",
                    "cards": [
                        {
                            "front": {"flashcardContentBlock": [{"content": "正面 & \"引号\""}]},
                            "back": {"flashcardContentBlock": [{"content": "背面 false"}]},
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "render_study_card.py"), str(fc_json), str(fc_html)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self._assert_js_valid(fc_html)

    def test_mind_map_and_xss_adversarial(self) -> None:
        mm_json = self.out_dir / "mm.json"
        mm_html = self.out_dir / "mm.html"
        mm_json.write_text(
            json.dumps(
                {
                    "name": "根节点 <!--<script></script>",
                    "children": [
                        {"name": "分支 A & 中文", "children": [{"name": "叶子 \"false\""}]},
                        {"name": "分支 B"},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "render_mind_map.py"), str(mm_json), str(mm_html)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self._assert_js_valid(mm_html)

    def test_slide_deck_emits_static_img_tags(self) -> None:
        slides = self.out_dir / "slides"
        slides.mkdir()
        (slides / "slide-1.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        (slides / "slide-2.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        out_html = self.out_dir / "deck.html"
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "render_slide_deck.py"), str(slides), str(out_html), "Test Deck"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = out_html.read_text(encoding="utf-8")
        self.assertIn(str((slides / "slide-1.jpg").resolve()), text)
        self.assertIn(str((slides / "slide-2.jpg").resolve()), text)
        self._assert_js_valid(out_html)

    def test_malformed_json_returns_nonzero(self) -> None:
        bad = self.out_dir / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        for script in ("render_study_card.py", "render_mind_map.py"):
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / script), str(bad), str(self.out_dir / "out.html")],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0, f"{script} must fail cleanly on malformed JSON")


if __name__ == "__main__":
    unittest.main()
