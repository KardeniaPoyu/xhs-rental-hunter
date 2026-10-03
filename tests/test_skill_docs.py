"""保证根目录 SKILL.md 与 skills/xhs-rental-hunter/SKILL.md 同步，且引用的文件都存在。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _expected_subskill() -> str:
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    text = text.replace("](references/", "](../../references/")
    return text.replace(
        "所有命令在本 Skill 根目录执行。",
        "所有命令在仓库根目录执行（本文件与根目录 SKILL.md 内容一致）。",
    )


def test_subskill_in_sync() -> None:
    actual = (ROOT / "skills" / "xhs-rental-hunter" / "SKILL.md").read_text(encoding="utf-8")
    assert actual == _expected_subskill(), "请用根目录 SKILL.md 重新生成子技能文件（见 CLAUDE.md）"


def test_links_exist() -> None:
    for md in [ROOT / "SKILL.md", ROOT / "skills" / "xhs-rental-hunter" / "SKILL.md"]:
        for link in re.findall(r"\]\(([^)#]+\.md)\)", md.read_text(encoding="utf-8")):
            assert (md.parent / link).resolve().exists(), f"{md} 引用了不存在的 {link}"


def test_frontmatter() -> None:
    head = (ROOT / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
    assert re.search(r"^name: xhs-rental-hunter$", head, re.M)
    assert "description:" in head
