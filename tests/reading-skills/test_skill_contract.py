from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = Path("/Users/hezhenyu/.agents/skills/.system/skill-creator/scripts/quick_validate.py")
EXPECTED = {
    "reading-inspection": ("检视阅读", "原文陈述", "AI 推断", "待核验问题", "不自动"),
    "reading-analysis": ("分析阅读", "原文陈述", "AI 推断", "待核验问题", "评议"),
    "reading-synthesis": ("主题阅读", "原文陈述", "AI 推断", "待核验问题", "至少两份"),
}


class ReadingSkillContractTests(unittest.TestCase):
    def test_each_skill_is_valid_and_exposes_declared_boundary(self) -> None:
        for skill_name, phrases in EXPECTED.items():
            with self.subTest(skill_name=skill_name):
                skill_dir = ROOT / skill_name
                self.assertTrue((skill_dir / "SKILL.md").is_file())
                self.assertTrue((skill_dir / "agents" / "openai.yaml").is_file())
                result = subprocess.run(
                    [sys.executable, str(VALIDATOR), str(skill_dir)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                text = (skill_dir / "SKILL.md").read_text()
                for phrase in phrases:
                    self.assertIn(phrase, text)

    def test_reference_layout_is_deliberate(self) -> None:
        self.assertFalse((ROOT / "reading-inspection" / "references").exists())
        self.assertTrue((ROOT / "reading-analysis" / "references" / "workflow.md").is_file())
        self.assertTrue((ROOT / "reading-synthesis" / "references" / "workflow.md").is_file())

    def test_analysis_routes_before_optional_evaluation(self) -> None:
        skill_path = ROOT / "reading-analysis" / "SKILL.md"
        self.assertTrue(skill_path.is_file())
        text = skill_path.read_text()
        self.assertIn("references/workflow.md", text)
        self.assertIn("知识类", text)
        self.assertIn("文学类", text)
        self.assertGreater(text.find("评议"), text.find("重构"))

    def test_synthesis_builds_a_neutral_frame_before_disputes(self) -> None:
        skill_path = ROOT / "reading-synthesis" / "SKILL.md"
        self.assertTrue(skill_path.is_file())
        text = skill_path.read_text()
        self.assertIn("references/workflow.md", text)
        self.assertIn("问题", text)
        self.assertIn("至少两份", text)
        self.assertGreater(text.find("争议"), text.find("中立词汇"))
