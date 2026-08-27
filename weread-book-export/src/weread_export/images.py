"""带微信读书认证上下文的图片下载、识别和解码。"""

import asyncio
import hashlib
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from playwright.async_api import APIRequestContext

from .errors import ExitCode, ExportError
from .models import ImageFile, ImageOccurrence

Progress = Callable[[str, Mapping[str, object]], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class ImageFormat:
    extension: str
    mime_type: str


@dataclass(frozen=True, slots=True)
class ImageValidation:
    ok: bool
    media_type: str | None = None
    width: int = 0
    height: int = 0
    reason: str | None = None


MAGIC_FORMATS = (
    (b"\x89PNG\r\n\x1a\n", ImageFormat("png", "image/png")),
    (b"\xff\xd8\xff", ImageFormat("jpg", "image/jpeg")),
    (b"GIF87a", ImageFormat("gif", "image/gif")),
    (b"GIF89a", ImageFormat("gif", "image/gif")),
)


def identify_image_format(body: bytes, declared_type: str = "") -> ImageFormat:
    for magic, image_format in MAGIC_FORMATS:
        if body.startswith(magic):
            return image_format
    if len(body) >= 12 and body.startswith(b"RIFF") and body[8:12] == b"WEBP":
        return ImageFormat("webp", "image/webp")
    raise ExportError(
        reason="invalid_image",
        message="下载内容不是支持的图片格式",
        exit_code=ExitCode.VALIDATION_FAILED,
        details={"declared_type": declared_type.split(";", 1)[0]},
    )


def validate_image(path: Path, declared_type: str = "") -> ImageValidation:
    try:
        body = path.read_bytes()
        detected = identify_image_format(body, declared_type)
        with Image.open(path) as decoded:
            width, height = decoded.size
            decoded.verify()
        if width <= 0 or height <= 0:
            return ImageValidation(False, reason="empty_dimensions")
        return ImageValidation(True, detected.mime_type, width, height)
    except (ExportError, OSError, UnidentifiedImageError, ValueError) as error:
        return ImageValidation(False, reason=type(error).__name__)


async def download_one(
    request: APIRequestContext,
    occurrence: ImageOccurrence,
    destination: Path,
    index: int,
) -> ImageFile:
    response = await request.get(
        occurrence.src,
        headers={"Referer": "https://weread.qq.com/"},
        timeout=20_000,
        fail_on_status_code=True,
    )
    try:
        body = await response.body()
        declared_type = response.headers.get("content-type", "")
    finally:
        await response.dispose()
    image_format = identify_image_format(body, declared_type)
    safe_occurrence = re.sub(r"[^A-Za-z0-9_-]", "_", occurrence.occurrence_id)[:48]
    filename = f"img-{index:05d}-{safe_occurrence}.{image_format.extension}"
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = destination / filename
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_bytes(body)
    partial.replace(path)
    report = validate_image(path, image_format.mime_type)
    if not report.ok:
        path.unlink(missing_ok=True)
        raise ExportError(
            reason="invalid_image",
            message="图片无法解码",
            exit_code=ExitCode.VALIDATION_FAILED,
            details={"occurrence_id": occurrence.occurrence_id},
        )
    return ImageFile(
        occurrence_id=occurrence.occurrence_id,
        relative_path=Path("images") / filename,
        sha256=hashlib.sha256(body).hexdigest(),
        media_type=report.media_type or image_format.mime_type,
        width=report.width,
        height=report.height,
        size_bytes=len(body),
        source_identity=occurrence.normalized_identity,
    )


async def download_images(
    request: APIRequestContext,
    occurrences: Sequence[ImageOccurrence],
    target: Path,
    progress: Progress | None = None,
) -> dict[str, ImageFile]:
    semaphore = asyncio.Semaphore(8)

    async def run(index: int, occurrence: ImageOccurrence) -> ImageFile:
        async with semaphore:
            last_error: Exception | None = None
            for attempt in range(1, 4):
                try:
                    result = await download_one(request, occurrence, target, index)
                    if progress:
                        emitted = progress(
                            "image_downloaded",
                            {"occurrence_id": occurrence.occurrence_id, "attempt": attempt},
                        )
                        if emitted is not None:
                            await emitted
                    return result
                except Exception as error:
                    last_error = error
                    if attempt < 3:
                        await asyncio.sleep(0.2 * attempt)
            assert last_error is not None
            raise last_error

    files = await asyncio.gather(
        *(run(index, occurrence) for index, occurrence in enumerate(occurrences, 1))
    )
    return {
        occurrence.occurrence_id: image
        for occurrence, image in zip(occurrences, files, strict=True)
    }
