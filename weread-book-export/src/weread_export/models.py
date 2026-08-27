"""跨模块共享的不可变领域模型。"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class BookRef:
    book_id: str
    canonical_url: str


@dataclass(frozen=True, slots=True)
class CanvasRect:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height


@dataclass(frozen=True, slots=True)
class CanvasGlyph:
    text: str
    x: float
    y: float
    sequence: int
    canvas_id: str = "canvas-0"
    transform: tuple[float, float, float, float, float, float] = (1, 0, 0, 1, 0, 0)


@dataclass(frozen=True, slots=True)
class TextLine:
    text: str
    rect: CanvasRect
    sequence: int


@dataclass(frozen=True, slots=True)
class ImageOccurrence:
    occurrence_id: str
    src: str
    normalized_identity: str
    rect: CanvasRect
    sequence: int
    natural_width: int = 0
    natural_height: int = 0


@dataclass(frozen=True, slots=True)
class ContentBlock:
    kind: Literal["paragraph", "image"]
    sequence: int
    text: str | None = None
    image: ImageOccurrence | None = None

    def __post_init__(self) -> None:
        if (self.kind == "paragraph") != (self.text is not None):
            raise ValueError("paragraph 内容块必须且只能包含 text")
        if (self.kind == "image") != (self.image is not None):
            raise ValueError("image 内容块必须且只能包含 image")


@dataclass(frozen=True, slots=True)
class PageCapture:
    chapter_title: str
    position: str
    glyphs: tuple[CanvasGlyph, ...]
    images: tuple[ImageOccurrence, ...]
    fingerprint: str
    blocks: tuple[ContentBlock, ...] = ()

    @property
    def canonical_text(self) -> str:
        return "".join(block.text or "" for block in self.blocks if block.kind == "paragraph")


@dataclass(frozen=True, slots=True)
class ChapterCapture:
    title: str
    index: int
    pages: tuple[PageCapture, ...]

    @property
    def canonical_text(self) -> str:
        values: list[str] = []
        for page_index, page in enumerate(self.pages):
            for block_index, block in enumerate(page.blocks):
                if block.kind != "paragraph":
                    continue
                value = block.text or ""
                if page_index == 0 and block_index == 0 and value.strip() == self.title:
                    continue
                values.append(value)
        return "".join(values)


@dataclass(frozen=True, slots=True)
class BookCapture:
    book_id: str
    title: str
    author: str
    chapters: tuple[ChapterCapture, ...]
    reached_end: bool
    catalog_titles: tuple[str, ...] = ()
    source_character_count: int = 0

    @property
    def canonical_text(self) -> str:
        return "".join(chapter.canonical_text for chapter in self.chapters)

    @property
    def page_fingerprints(self) -> tuple[str, ...]:
        return tuple(page.fingerprint for chapter in self.chapters for page in chapter.pages)

    @property
    def image_occurrences(self) -> tuple[ImageOccurrence, ...]:
        return tuple(
            image for chapter in self.chapters for page in chapter.pages for image in page.images
        )


@dataclass(frozen=True, slots=True)
class ImageFile:
    occurrence_id: str
    relative_path: Path
    sha256: str
    media_type: str
    width: int
    height: int
    size_bytes: int
    source_identity: str


@dataclass(frozen=True, slots=True)
class RenderedBook:
    markdown: str
    canonical_text: str
    chapter_markdown: tuple[str, ...]
    images: tuple[ImageFile, ...]


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    passed: bool
    details: Mapping[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.passed


@dataclass(frozen=True, slots=True)
class ValidationReport:
    valid: bool
    checks: tuple[CheckResult, ...]
    source_characters: int
    rendered_characters: int
    image_occurrences: int
    image_files: int

    def require_success(self) -> None:
        if self.valid:
            return
        from .errors import ExitCode, ExportError

        failed = [check.name for check in self.checks if not check.passed]
        raise ExportError(
            reason="validation_failed",
            message="导出完整性校验失败",
            exit_code=ExitCode.VALIDATION_FAILED,
            details={"failed_checks": failed},
        )

    def to_json(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "checks": [
                {"name": check.name, "passed": check.passed, "details": dict(check.details)}
                for check in self.checks
            ],
            "source_characters": self.source_characters,
            "rendered_characters": self.rendered_characters,
            "image_occurrences": self.image_occurrences,
            "image_files": self.image_files,
        }


@dataclass(frozen=True, slots=True)
class ExportRequest:
    book_ref: BookRef
    output_dir: Path | None = None
    force: bool = False


class JobStatus(StrEnum):
    CREATED = "created"
    BOOTSTRAPPING = "bootstrapping"
    WAITING_FOR_AUTH = "waiting_for_auth"
    CAPTURING = "capturing"
    DOWNLOADING_IMAGES = "downloading_images"
    VALIDATING = "validating"
    PUBLISHING = "publishing"
    COMPLETED = "completed"
    FAILED = "failed"
    VALIDATION_FAILED = "validation_failed"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class JobRecord:
    job_id: str
    book_id: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    error_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ResultRecord:
    result_id: str
    book_id: str
    title: str
    path: Path
    managed_root: Path
    manifest_sha256: str
    created_at: datetime
    retained_until: datetime
    size_bytes: int = 0
    kept: bool = False
    modified: bool = False


@dataclass(frozen=True, slots=True)
class PublishTransaction:
    job_id: str
    target: Path
    staging: Path
    backup: Path
    phase: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ExportResult:
    status: Literal["completed", "reused"]
    book_id: str
    title: str
    output_dir: Path
    markdown_path: Path
    manifest_path: Path
    chapters: int
    characters: int
    images: int
    retained_until: datetime | None
    validation: ValidationReport | None = None

    def to_json(self) -> dict[str, object]:
        validation = self.validation.to_json() if self.validation else {"valid": True}
        return {
            "status": self.status,
            "book_id": self.book_id,
            "title": self.title,
            "output_dir": str(self.output_dir),
            "markdown_path": str(self.markdown_path),
            "manifest_path": str(self.manifest_path),
            "chapters": self.chapters,
            "characters": self.characters,
            "images": self.images,
            "retained_until": self.retained_until.isoformat() if self.retained_until else None,
            "validation": validation,
        }
