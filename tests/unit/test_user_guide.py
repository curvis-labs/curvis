"""The user guide (docs/user-guide) is complete: every page is linked from its index,
every link and picture it uses exists, and every picture is one the screenshot script
makes (scripts/user_guide_screenshots.py), so none goes stale unnoticed."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
GUIDE = ROOT / "docs" / "user-guide"
PAGES = sorted(p for p in GUIDE.glob("*.md") if p.name != "README.md")
LINK = re.compile(r"\]\(([^)#\s]+)(#[^)\s]*)?\)")


def links(page: Path) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2) or "") for m in LINK.finditer(page.read_text("utf-8"))]


def anchors(page: Path) -> set[str]:
    """GitHub's anchors for the headings of ``page``."""
    found = set()
    for line in page.read_text("utf-8").splitlines():
        if line.startswith("#"):
            title = line.lstrip("#").strip().lower()
            found.add("#" + re.sub(r"[^\w\- ]", "", title).replace(" ", "-"))
    return found


def test_every_page_is_in_the_index() -> None:
    listed = {target for target, _ in links(GUIDE / "README.md")}
    assert PAGES
    assert {p.name for p in PAGES} <= listed


def test_links_and_pictures_exist() -> None:
    for page in [GUIDE / "README.md", *PAGES]:
        for target, anchor in links(page):
            if target.startswith("http"):
                continue
            path = (page.parent / target).resolve()
            assert path.exists(), f"{page.name}: {target} does not exist"
            if anchor and path.suffix == ".md":
                assert anchor in anchors(path), f"{page.name}: no heading {anchor} in {target}"


def test_every_picture_is_made_by_the_screenshot_script() -> None:
    script = (ROOT / "scripts" / "user_guide_screenshots.py").read_text("utf-8")
    made = set(re.findall(r'"([a-z-]+)": "[a-z_]+",', script))
    made |= set(re.findall(r'window_shot\(settings, app, "([a-z-]+)"', script))
    used = {
        Path(target).stem
        for page in [*PAGES, GUIDE / "README.md"]
        for target, _ in links(page)
        if target.startswith("images/")
    }
    assert used <= made, f"pictures the script does not make: {used - made}"
    on_disk = {p.stem for p in (GUIDE / "images").glob("*.png")}
    assert on_disk == made, f"stale or missing pictures: {on_disk ^ made}"
