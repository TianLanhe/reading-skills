"""正文、页面、章节、图片和链接的强完整性校验。"""

import hashlib
import re
from pathlib import Path

from .images import validate_image
from .models import BookCapture, CheckResult, RenderedBook, ValidationReport

MARKDOWN_IMAGE_RE = re.compile(r"!\[[^]]*]\(([^)]+)\)")


def _check(name: str, passed: bool, **details: object) -> CheckResult:
    return CheckResult(name=name, passed=passed, details=details)


def _catalog_order(capture: BookCapture) -> bool:
    if not capture.catalog_titles:
        return True
    cursor = 0
    for title in (chapter.title for chapter in capture.chapters):
        try:
            cursor = capture.catalog_titles.index(title, cursor) + 1
        except ValueError:
            return False
    return True


def validate_export(
    capture: BookCapture,
    rendered: RenderedBook,
    root: Path,
) -> ValidationReport:
    source_text = capture.canonical_text
    fingerprints = capture.page_fingerprints
    chapter_non_empty = all(
        chapter.canonical_text or any(page.images for page in chapter.pages)
        for chapter in capture.chapters
    )

    image_checks: list[bool] = []
    hash_checks: list[bool] = []
    expected_paths: set[str] = set()
    for image in rendered.images:
        path = root / image.relative_path
        expected_paths.add(image.relative_path.as_posix())
        image_checks.append(path.is_file() and validate_image(path, image.media_type).ok)
        hash_checks.append(
            path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == image.sha256
        )

    links = set(MARKDOWN_IMAGE_RE.findall(rendered.markdown))
    safe_links = all(
        not Path(link).is_absolute() and ".." not in Path(link).parts and link.startswith("images/")
        for link in links
    )
    occurrence_count = len(capture.image_occurrences)
    checks = (
        _check("reached_end", capture.reached_end),
        _check("has_chapters", bool(capture.chapters)),
        _check(
            "character_conservation",
            source_text == rendered.canonical_text,
            source_characters=len(source_text),
            rendered_characters=len(rendered.canonical_text),
        ),
        _check("unique_pages", len(fingerprints) == len(set(fingerprints))),
        _check("chapter_order", _catalog_order(capture)),
        _check("non_empty_chapters", chapter_non_empty),
        _check(
            "image_occurrence_count",
            occurrence_count == len(rendered.images),
            occurrences=occurrence_count,
            files=len(rendered.images),
        ),
        _check("images_decode", all(image_checks), checked=len(image_checks)),
        _check("image_hashes", all(hash_checks), checked=len(hash_checks)),
        _check(
            "markdown_image_links",
            safe_links and links == expected_paths,
            links=len(links),
            files=len(expected_paths),
        ),
    )
    return ValidationReport(
        valid=all(check.passed for check in checks),
        checks=checks,
        source_characters=len(source_text),
        rendered_characters=len(rendered.canonical_text),
        image_occurrences=occurrence_count,
        image_files=len(rendered.images),
    )
