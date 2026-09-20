# Reading Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add, validate, globally install, and publish three evidence-aware reading Skills for inspectional, analytical, and thematic reading.

**Architecture:** Three root-level, independently installable Skill directories share an evidence contract but no runtime code, scripts, or external dependencies. A root-level unittest contract checks structure and non-negotiable boundaries. Scenario cards and a concise validation report record real-source forward tests without committing the book content.

**Tech Stack:** Markdown, YAML, Python standard-library unittest, skill-creator initializer and validator, uv, Git, and the skills CLI.

**Spec:** docs/superpowers/specs/2026-09-21-reading-skills-design.md

## Global Constraints

- Create exactly reading-inspection, reading-analysis, and reading-synthesis as root-level directories.
- Preserve original-text-first. Label key conclusions as 原文陈述, AI 推断, or 待核验问题 with stable source locators.
- Do not fetch, export, search, modify, or persist source material unless a user expressly requests that separate operation.
- Do not commit exported book text, images, authentication state, manifests, absolute local paths, or private annotations.
- Keep automatic discovery enabled and descriptions narrow enough to avoid ordinary recommendation or summary requests.
- Use topic-specific analysis modes for knowledge and literary works. Never force a thesis-and-evidence structure onto literature.
- Use a WeRead export only when validation.valid is true.
- Install globally only after all three directories are pushed and validated. Preserve unrelated global Skills.

## Review Focus

- Title-only input produces a pre-reading plan, not source-grounded analysis; Task 2 pins this.
- Analysis does not evaluate before reconstruction; Task 3 pins this.
- Literary material routes to a literary framework rather than an invented argument chain; Task 3 pins this.
- Topic reading with fewer than two readable sources or no user question stops and explains what is missing; Task 4 pins this.
- External knowledge stays distinct from supplied material and only appears after authorization; Tasks 2 through 4 pin this.

---

### Task 1: Add a test-first contract and manual scenario cards

**Files:**

- Create: tests/reading-skills/test_skill_contract.py
- Create: tests/reading-skills/scenarios.md

**Interfaces:**

- Consumes: root-level Skill directories and /Users/hezhenyu/.agents/skills/.system/skill-creator/scripts/quick_validate.py.
- Produces: a repeatable gate runnable with uv run --project weread-book-export python -m unittest discover -s tests/reading-skills -p test_*.py -v.

- [ ] **Step 1: Write the failing contract test**

Create tests/reading-skills/test_skill_contract.py:

~~~python
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
~~~

Create tests/reading-skills/scenarios.md with six scenarios: title-only inspection, complete nonfiction inspection, knowledge-work analysis, literary analysis routing, requested evaluation after reconstruction, and three-source thematic comparison. Each records input type, goal, mandatory labels, prohibited behavior, and pass criteria. It may contain Book IDs and public titles, never excerpts or local paths.

- [ ] **Step 2: Run it before creating a Skill**

~~~bash
uv run --project weread-book-export python -m unittest discover -s tests/reading-skills -p 'test_*.py' -v
~~~

Expected: FAIL because all three Skill directories are absent.

- [ ] **Step 3: Commit the red contract**

~~~bash
git add tests/reading-skills
git commit -m "test: define reading skill contracts"
~~~

### Task 2: Create reading-inspection

**Files:**

- Create: reading-inspection/SKILL.md
- Create: reading-inspection/agents/openai.yaml
- Modify: tests/reading-skills/scenarios.md

**Interfaces:**

- Consumes: one readable material and an optional reading goal or time budget.
- Produces: scope, classification, structural map, evidence-tagged reading decision, and next-step recommendation.

- [ ] **Step 1: Confirm its contract is red**

Run the Task 1 test command and confirm the inspection subtest fails because its directory is absent.

- [ ] **Step 2: Initialize via skill-creator**

~~~bash
uv run --project weread-book-export python /Users/hezhenyu/.agents/skills/.system/skill-creator/scripts/init_skill.py reading-inspection --path . --interface 'display_name=检视阅读' --interface 'short_description=快速判断材料的结构、价值与阅读路径' --interface 'default_prompt=使用 $reading-inspection 检视我提供的材料，判断应读多深并给出路径。'
~~~

- [ ] **Step 3: Replace scaffolding with the focused workflow**

Use apply_patch to remove every initializer placeholder. Require material-access and scope declaration; review of title, foreword, contents, index, selected chapters, and summary or ending when present; classification with uncertainty disclosure; the three evidence labels; title-only downgrade to a pre-reading plan; browsing, selected-reading, or deep-analysis recommendation; no external retrieval; and no automatic handoff.

Keep agents/openai.yaml quoted and implicitly invocable. Add scenario assertions that title-only input never yields source-grounded claims and complete nonfiction inspection states what it did and did not inspect.

- [ ] **Step 4: Verify its green boundary**

Run the Task 1 test command. Expected: inspection passes while analysis and synthesis remain red because they are absent.

- [ ] **Step 5: Commit**

~~~bash
git add reading-inspection tests/reading-skills/scenarios.md
git commit -m "feat: add inspection reading skill"
~~~

### Task 3: Create reading-analysis

**Files:**

- Create: reading-analysis/SKILL.md
- Create: reading-analysis/agents/openai.yaml
- Create: reading-analysis/references/workflow.md
- Modify: tests/reading-skills/test_skill_contract.py
- Modify: tests/reading-skills/scenarios.md

**Interfaces:**

- Consumes: one readable material or selected section and an optional goal.
- Produces: source scope, type-specific reconstruction, evidence-tagged conclusions, and optional evaluation separate from reconstruction.

- [ ] **Step 1: Extend the red contract**

Add assertions that references/workflow.md is linked from SKILL.md, the Skill contains both 知识类 and 文学类, and the first occurrence of 评议 follows the first occurrence of 重构. Run the test command; expected failure is the missing analysis directory.

- [ ] **Step 2: Initialize via skill-creator**

~~~bash
uv run --project weread-book-export python /Users/hezhenyu/.agents/skills/.system/skill-creator/scripts/init_skill.py reading-analysis --path . --resources references --interface 'display_name=分析阅读' --interface 'short_description=重构材料的结构、概念、主张与表达方式' --interface 'default_prompt=使用 $reading-analysis 分析我提供的材料，并以原文定位区分事实、推断与问题。'
~~~

- [ ] **Step 3: Write entrypoint and reference**

Use apply_patch to replace scaffolding. SKILL.md states that the default goal is understanding the author; knowledge works yield category, synopsis, outline, author question, terms, propositions, arguments, and resolved or unresolved questions; literary works yield structure, narration or characters, themes, imagery, and experience without invented arguments; partial material constrains claims; evaluation is opt-in and follows reconstruction.

Write references/workflow.md as an original concise procedure: establish scope and type, map the whole, interpret claims or literary elements, create the evidence ledger, then apply requested evaluation. Include a minimal output skeleton and the three evidence labels.

- [ ] **Step 4: Verify its green boundary**

Run the test command. Expected: inspection and analysis pass while synthesis remains red because it is absent.

- [ ] **Step 5: Commit**

~~~bash
git add reading-analysis tests/reading-skills
git commit -m "feat: add analytical reading skill"
~~~

### Task 4: Create reading-synthesis

**Files:**

- Create: reading-synthesis/SKILL.md
- Create: reading-synthesis/agents/openai.yaml
- Create: reading-synthesis/references/workflow.md
- Modify: tests/reading-skills/test_skill_contract.py
- Modify: tests/reading-skills/scenarios.md

**Interfaces:**

- Consumes: one user-defined question and at least two readable materials.
- Produces: corpus ledger, neutral vocabulary, question set, position or controversy map, ordered synthesis, and source locators.

- [ ] **Step 1: Extend the red contract**

Add assertions that references/workflow.md is linked, 问题 and 至少两份 are required, and 中立词汇 appears before 争议. Run the test command; expected failure is the missing synthesis directory.

- [ ] **Step 2: Initialize via skill-creator**

~~~bash
uv run --project weread-book-export python /Users/hezhenyu/.agents/skills/.system/skill-creator/scripts/init_skill.py reading-synthesis --path . --resources references --interface 'display_name=主题阅读' --interface 'short_description=围绕一个问题比较多份材料并梳理争议' --interface 'default_prompt=使用 $reading-synthesis 围绕我的问题比较这些材料，建立中立框架并保留出处。'
~~~

- [ ] **Step 3: Write entrypoint and reference**

Use apply_patch to replace scaffolding. Require a pause for no question or fewer than two readable materials; record included, excluded, and incomplete sources; identify relevant sections; establish neutral vocabulary and answerable questions before positions; classify agreement, disagreement, silence, and inference; order results from common ground to disputes; distinguish literary from knowledge evidence; and avoid choosing a winner unless asked.

- [ ] **Step 4: Verify full contract green**

Run the test command. Expected: every test passes, every Skill passes the bundled validator, and no scaffold placeholder remains.

- [ ] **Step 5: Commit**

~~~bash
git add reading-synthesis tests/reading-skills
git commit -m "feat: add thematic reading skill"
~~~

### Task 5: Forward-test and refine with six authorized WeRead exports

**Files:**

- Create: docs/validation/2026-09-21-reading-skills.md
- Modify: the owning Skill only when a scenario demonstrates an observed defect.

**Interfaces:**

- Consumes: manifests and Markdown outside the repository only where validation.valid is true.
- Produces: a concise, non-copyrighting validation report with title, Book ID, validation state, scenario, outcome, and observed change.

- [ ] **Step 1: Verify sources before reading**

For IDs 272326407169ff22272934f, 552323d0813abbc5cg01570e, eb332820813ab9c71g011e20, bbc32a50813ab7953g01522a, 0bf32020813ab7e6bg016510, and 42432e707190d338424666e, inspect final exporter JSON or manifest.json. Include only valid exports and record title, chapter count, character count, image count, and scenario role. Do not copy body text, images, manifests, or paths.

- [ ] **Step 2: Test inspection**

Apply reading-inspection to complete 如何阅读一本书 and one valid image-containing export. Verify scope, locators, appropriate depth, and no claim that inspection equals deep reading. Run title-only input and verify downgrade.

- [ ] **Step 3: Test analysis**

Apply reading-analysis to 如何阅读一本书 and another knowledge-oriented export. Verify structure, terms, claims or arguments, and unresolved questions with locators. Run requested evaluation and verify it is distinct and later. If a valid literary export exists, run literary routing; otherwise record the missing coverage.

- [ ] **Step 4: Test synthesis**

Using three valid relevant exports, ask: “这些材料分别主张 AI 在阅读或学习中承担什么角色，哪些环节仍必须由读者完成？” Verify corpus scope, neutral vocabulary before positions, silence versus inference, and no unrequested winner.

- [ ] **Step 5: Fix only observed failures**

For every observed failure, add a precise assertion to scenarios.md, change only the owning Skill with apply_patch, rerun the contract and failed scenario, then record before or after result. Do not generalize from one source without observed evidence.

- [ ] **Step 6: Verify and commit**

~~~bash
uv run --project weread-book-export python -m unittest discover -s tests/reading-skills -p 'test_*.py' -v
for skill in reading-inspection reading-analysis reading-synthesis; do
  uv run --project weread-book-export python /Users/hezhenyu/.agents/skills/.system/skill-creator/scripts/quick_validate.py "$skill"
done
git diff --check
~~~

Expected: all tests and validators pass; no private source material is staged.

~~~bash
git add reading-inspection reading-analysis reading-synthesis tests/reading-skills docs/validation
git commit -m "test: validate reading skills with authorized sources"
~~~

### Task 6: Publish and install

**Files:**

- Modify: repository root with the three intended Skill directories and validation support only.
- Modify outside repository: global Skill registration managed by the skills CLI.

**Interfaces:**

- Consumes: clean, passing local main and the user-approved TianLanhe/read-skills remote.
- Produces: pushed root-level Skill directories and durable global Codex discovery.

- [ ] **Step 1: Audit before pushing**

~~~bash
git status --short
git log --oneline origin/main..HEAD
git diff --check origin/main...HEAD
git diff --name-only origin/main...HEAD
~~~

Expected: only design, plan, test, validation documentation and three intended Skill directories changed.

- [ ] **Step 2: Push without force**

~~~bash
git push origin main
~~~

- [ ] **Step 3: Install three published Skills globally**

~~~bash
npx --yes skills@1.7.0 add git@github.com:TianLanhe/read-skills.git --global --agent codex --skill reading-inspection reading-analysis reading-synthesis --yes --json
~~~

Expected: only requested registrations are created or updated under /Users/hezhenyu/.agents/skills.

- [ ] **Step 4: Verify remote and discovery**

~~~bash
npx --yes skills@1.7.0 list --global --agent codex --json
git ls-remote origin main
~~~

Confirm every name appears once, remote main equals local HEAD, and unrelated global Skills remain visible.
