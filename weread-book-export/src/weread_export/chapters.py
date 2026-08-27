"""章节归属和保守 Markdown 渲染。"""

import re
from collections.abc import Mapping, Sequence, Set
from pathlib import Path, PurePosixPath

from .models import (
    BookCapture,
    ChapterCapture,
    ImageFile,
    PageCapture,
    RenderedBook,
    TextLine,
)

IMAGE_LINE_RE = re.compile(r"^!\[[^]]*]\([^)]+\)$")
HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")


def assemble_chapters(
    pages: Sequence[PageCapture], catalog: Sequence[str]
) -> tuple[ChapterCapture, ...]:
    """按稳定页面标题分组；目录仅作为辅助证据，不要求数量相等。"""
    del catalog
    chapters: list[ChapterCapture] = []
    current_title: str | None = None
    current_pages: list[PageCapture] = []

    def flush() -> None:
        if not current_pages or current_title is None:
            return
        chapters.append(
            ChapterCapture(title=current_title, index=len(chapters) + 1, pages=tuple(current_pages))
        )

    for page in pages:
        title = page.chapter_title.strip() or current_title or "未命名章节"
        if current_title is not None and title != current_title:
            flush()
            current_pages = []
        current_title = title
        current_pages.append(page)
    flush()
    return tuple(chapters)


def should_join_lines(previous: TextLine, current: TextLine, page_width: float) -> bool:
    """仅在缩进、行距和占宽都支持软换行时拼接。"""
    if page_width <= 0 or previous.rect.height <= 0:
        return False
    same_indent = abs(previous.rect.x - current.rect.x) <= 2
    gap = current.rect.y - previous.rect.y
    normal_gap = 0 < gap <= previous.rect.height * 1.6
    previous_fills_width = previous.rect.width / page_width >= 0.82
    current_is_not_indented = current.rect.x / page_width < 0.08
    return same_indent and normal_gap and previous_fills_width and current_is_not_indented


def _safe_image_path(value: str | Path) -> str:
    normalized = str(value).replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "images":
        raise ValueError("图片必须使用 images/ 下的安全相对路径")
    return path.as_posix()


def render_book_markdown(
    capture: BookCapture,
    image_files: Mapping[str, ImageFile | str | Path],
) -> RenderedBook:
    """按捕获顺序渲染全书，不按内容或 URL 去重。"""
    document: list[str] = [f"# {capture.title}"]
    if capture.author:
        document.extend(("", f"> 作者：{capture.author}"))
    chapter_documents: list[str] = []

    for chapter in capture.chapters:
        chapter_lines: list[str] = [f"## {chapter.title}"]
        for page in chapter.pages:
            for block_index, block in enumerate(
                sorted(page.blocks, key=lambda item: item.sequence)
            ):
                if block.kind == "paragraph":
                    value = block.text or ""
                    if block_index == 0 and value.strip() == chapter.title:
                        continue
                    if value:
                        chapter_lines.extend(("", value))
                    continue
                assert block.image is not None
                image_value = image_files.get(block.image.occurrence_id)
                if image_value is None:
                    raise ValueError(f"图片 occurrence 未下载：{block.image.occurrence_id}")
                relative = (
                    image_value.relative_path if isinstance(image_value, ImageFile) else image_value
                )
                chapter_lines.extend(("", f"![图]({_safe_image_path(relative)})"))
        chapter_text = "\n".join(chapter_lines).rstrip() + "\n"
        chapter_documents.append(chapter_text)
        document.extend(("", chapter_text.rstrip()))

    markdown = "\n".join(document).rstrip() + "\n"
    structural_titles = {capture.title, *(chapter.title for chapter in capture.chapters)}
    downloaded = tuple(value for value in image_files.values() if isinstance(value, ImageFile))
    return RenderedBook(
        markdown=markdown,
        canonical_text=canonical_body_text(markdown, structural_titles),
        chapter_markdown=tuple(chapter_documents),
        images=downloaded,
    )


def canonical_body_text(markdown: str, structural_titles: Set[str]) -> str:
    """只移除由渲染器生成的结构，不改写正文字符。"""
    body: list[str] = []
    for raw_line in markdown.splitlines():
        line = raw_line.removesuffix("  ")
        if not line:
            continue
        heading = HEADING_RE.fullmatch(line)
        if heading and heading.group(1) in structural_titles:
            continue
        if line.startswith("> 作者：") or IMAGE_LINE_RE.fullmatch(line):
            continue
        body.append(line)
    return "".join(body)
