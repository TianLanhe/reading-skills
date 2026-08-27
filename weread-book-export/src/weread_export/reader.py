"""微信读书阅读器 DOM 适配、稳定翻页和末尾判断。"""

import hashlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass

from playwright.async_api import BrowserContext, Page

from .capture import CANVAS_HOOK, build_page_blocks, normalize_image_identity, page_fingerprint
from .chapters import assemble_chapters
from .errors import ExitCode, ExportError
from .models import BookCapture, BookRef, CanvasGlyph, CanvasRect, ImageOccurrence, PageCapture

Progress = Callable[[str, Mapping[str, object]], Awaitable[None] | None]

CHAPTER_TITLE_SELECTOR = ".renderTargetPageInfo_header_chapterTitle"
CATALOG_BUTTON_SELECTOR = "button.readerControls_item.catalog"
CATALOG_ITEM_SELECTOR = ".readerCatalog_list_item"
CATALOG_ITEM_TITLE_SELECTOR = ".readerCatalog_list_item_title_text"
CATALOG_SCROLL_SELECTOR = '.readerCatalog_list_scroll_area, [class*="readerCatalog_list_scroll"]'
READER_IMAGE_SELECTOR = 'img[class*="wr_readerImage"]'
RIGHT_PAGE_BUTTON_SELECTOR = "button.renderTarget_pager_button_right"

SNAPSHOT_JS = r"""
() => {
  const canvases = Array.from(document.querySelectorAll('canvas')).filter((canvas) => {
    const rect = canvas.getBoundingClientRect();
    return rect.width > 0 && rect.height > 300;
  });
  const canvasIds = new Set(
    canvases.map((canvas) => canvas.dataset.wereadExportCanvasId).filter(Boolean)
  );
  const hook = window.__wereadExportCanvasHook;
  const glyphs = hook ? hook.snapshot().filter((glyph) => canvasIds.has(glyph.canvas_id)) : [];
  const rects = canvases.map((canvas) => {
    const rect = canvas.getBoundingClientRect();
    return {
      canvas_id: canvas.dataset.wereadExportCanvasId || '',
      x: rect.left, y: rect.top, width: rect.width, height: rect.height
    };
  }).filter((item) => item.canvas_id);
  const images = Array.from(document.querySelectorAll('img[class*="wr_readerImage"]'))
    .map((image) => {
      const rect = image.getBoundingClientRect();
      const style = getComputedStyle(image);
      return {
        src: image.currentSrc || image.src || image.getAttribute('data-src') || '',
        x: rect.left, y: rect.top, width: rect.width, height: rect.height,
        natural_width: image.naturalWidth || image.width || 0,
        natural_height: image.naturalHeight || image.height || 0,
        visible: rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0 &&
          rect.top < innerHeight && rect.left < innerWidth && style.display !== 'none' &&
          style.visibility !== 'hidden',
        complete: image.complete
      };
    }).filter((item) => item.visible && item.src);
  return {
    chapter_title: document.querySelector('.renderTargetPageInfo_header_chapterTitle')
      ?.textContent?.trim() || '',
    position: String(window.__fixturePageIndex ??
      document.querySelector('[data-page-index]')?.getAttribute('data-page-index') ??
      hook?.generation() ?? ''),
    glyphs, rects, images,
    pending: document.fonts?.status !== 'loaded' || images.some((item) => !item.complete)
  };
}
"""


@dataclass(frozen=True, slots=True)
class ReaderSnapshot:
    chapter_title: str
    position: str
    glyphs: tuple[CanvasGlyph, ...]
    images: tuple[ImageOccurrence, ...]
    rects: Mapping[str, CanvasRect]
    fingerprint: str
    pending: bool

    def to_page_capture(self) -> PageCapture:
        return PageCapture(
            chapter_title=self.chapter_title,
            position=self.position,
            glyphs=self.glyphs,
            images=self.images,
            fingerprint=self.fingerprint,
            blocks=build_page_blocks(self.glyphs, self.images, self.rects),
        )


class WeReadReader:
    def __init__(self, max_page_turns: int = 20_000) -> None:
        self.max_page_turns = max_page_turns

    async def capture(
        self,
        book_ref: BookRef,
        context: BrowserContext,
        checkpoint=None,
        progress: Progress | None = None,
    ) -> BookCapture:
        page = await context.new_page()
        await page.add_init_script(CANVAS_HOOK)
        try:
            await page.goto(book_ref.canonical_url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(500)
            await self._assert_access(page)
            title, author = await self._book_metadata(page)
            catalog = await self._catalog(page)
            pages: list[PageCapture] = []
            unchanged_attempts = 0
            empty_pages = 0
            last_seen_fingerprint: str | None = None

            for turn in range(self.max_page_turns):
                snapshot = await self.wait_for_stable_snapshot(page)
                if snapshot.fingerprint == last_seen_fingerprint:
                    unchanged_attempts += 1
                else:
                    unchanged_attempts = 0
                    last_seen_fingerprint = snapshot.fingerprint

                captured_page = snapshot.to_page_capture()
                if captured_page.blocks and (
                    not pages or captured_page.fingerprint != pages[-1].fingerprint
                ):
                    pages.append(captured_page)
                    empty_pages = 0
                    if checkpoint is not None:
                        checkpoint.save(tuple(pages))
                    await _emit(progress, "page_captured", {"pages": len(pages)})
                elif not captured_page.blocks:
                    empty_pages += 1

                if unchanged_attempts >= 3:
                    if pages and self._at_catalog_end(snapshot, catalog) and not snapshot.pending:
                        chapters = assemble_chapters(tuple(pages), catalog)
                        provisional = BookCapture(
                            book_id=book_ref.book_id,
                            title=title or book_ref.book_id,
                            author=author,
                            chapters=chapters,
                            reached_end=True,
                            catalog_titles=catalog,
                        )
                        return BookCapture(
                            book_id=provisional.book_id,
                            title=provisional.title,
                            author=provisional.author,
                            chapters=provisional.chapters,
                            reached_end=True,
                            catalog_titles=provisional.catalog_titles,
                            source_character_count=len(provisional.canonical_text),
                        )
                    raise ExportError(
                        reason="page_turn_stuck",
                        message="翻页连续三次无变化，且无法确认已到全书末尾",
                        exit_code=ExitCode.CAPTURE_FAILED,
                    )
                if empty_pages >= 10:
                    raise ExportError(
                        reason="too_many_empty_pages",
                        message="连续空白结构页超过安全上限",
                        exit_code=ExitCode.CAPTURE_FAILED,
                    )
                await self.turn_page(page, turn)
            raise ExportError(
                reason="page_limit_exceeded",
                message="超过安全翻页上限，未确认到达全书末尾",
                exit_code=ExitCode.CAPTURE_FAILED,
            )
        finally:
            await page.close()

    async def wait_for_stable_snapshot(self, page: Page) -> ReaderSnapshot:
        last_signature = ""
        stable_frames = 0
        last_snapshot: ReaderSnapshot | None = None
        for _ in range(80):
            raw = await page.evaluate(SNAPSHOT_JS)
            snapshot = _parse_snapshot(raw)
            signature = snapshot.fingerprint + ("-pending" if snapshot.pending else "-ready")
            if signature == last_signature:
                stable_frames += 1
            else:
                last_signature = signature
                stable_frames = 1
            last_snapshot = snapshot
            has_content = bool(snapshot.glyphs or snapshot.images)
            if stable_frames >= 3 and (has_content or stable_frames >= 20):
                return snapshot
            await page.wait_for_timeout(100)
        if last_snapshot is not None:
            return last_snapshot
        raise ExportError(
            reason="capture_failed",
            message="页面内容未能稳定",
            exit_code=ExitCode.CAPTURE_FAILED,
        )

    async def turn_page(self, page: Page, turn: int) -> None:
        del turn
        try:
            await page.locator(RIGHT_PAGE_BUTTON_SELECTOR).first.click(timeout=1_000)
        except Exception:
            await page.keyboard.press("ArrowRight")
        await page.wait_for_timeout(250)

    async def _catalog(self, page: Page) -> tuple[str, ...]:
        try:
            await page.locator(CATALOG_BUTTON_SELECTOR).click(timeout=3_000)
            await page.wait_for_timeout(200)
            scroll = page.locator(CATALOG_SCROLL_SELECTOR).first
            await scroll.wait_for(state="visible", timeout=2_000)
            await scroll.evaluate("element => { element.scrollTop = 0; }")
            await page.wait_for_timeout(150)
            titles: tuple[str, ...] = ()
            previous_top = -1.0
            for _ in range(2_000):
                raw_titles = await page.locator(CATALOG_ITEM_SELECTOR).evaluate_all(
                    """(items, titleSelector) => items.map((item) =>
                      item.querySelector(titleSelector)?.textContent?.trim() ||
                      item.textContent?.trim() || '')""",
                    CATALOG_ITEM_TITLE_SELECTOR,
                )
                batch = tuple(title.strip() for title in raw_titles if title.strip())
                titles = _merge_catalog_batch(titles, batch)
                metrics = await scroll.evaluate(
                    """element => ({
                      top: element.scrollTop,
                      height: element.clientHeight,
                      total: element.scrollHeight
                    })"""
                )
                top = float(metrics["top"])
                height = float(metrics["height"])
                total = float(metrics["total"])
                if top + height >= total - 2 or top == previous_top:
                    break
                previous_top = top
                next_top = min(total, top + max(height * 0.8, 200))
                await scroll.evaluate(
                    "(element, value) => { element.scrollTop = value; }", next_top
                )
                await page.wait_for_timeout(100)
            if titles:
                await scroll.evaluate("element => { element.scrollTop = 0; }")
                await page.wait_for_timeout(150)
                first = page.locator(CATALOG_ITEM_SELECTOR).first
                await first.scroll_into_view_if_needed(timeout=1_000)
                await first.click(timeout=2_000)
                await page.wait_for_timeout(300)
            return titles
        except Exception:
            with suppress(Exception):
                await page.keyboard.press("Escape")
            return ()

    async def _book_metadata(self, page: Page) -> tuple[str, str]:
        result = await page.evaluate(
            """() => ({
              title: document.querySelector(
                '.readerCatalog_bookInfo_title_txt, .bookInfo_right_header_title'
              )?.textContent?.trim() || document.title.replace(/-.*$/, '').trim(),
              author: document.querySelector(
                '.readerCatalog_bookInfo_author, .bookInfo_author a'
              )?.textContent?.trim() || ''
            })"""
        )
        return result.get("title", ""), result.get("author", "")

    async def _assert_access(self, page: Page) -> None:
        if "login" in page.url.lower():
            raise ExportError("auth_required", "登录状态已失效", ExitCode.AUTH_FAILED)
        body = (await page.locator("body").inner_text(timeout=3_000)).replace("\n", " ")
        if any(signal in body for signal in ("无权阅读", "试读结束", "去 App 阅读")):
            raise ExportError(
                "access_denied",
                "当前账号无法在网页版阅读完整内容",
                ExitCode.ACCESS_DENIED,
            )

    @staticmethod
    def _at_catalog_end(snapshot: ReaderSnapshot, catalog: Sequence[str]) -> bool:
        return bool(catalog) and snapshot.chapter_title == catalog[-1]


def _merge_catalog_batch(collected: tuple[str, ...], batch: tuple[str, ...]) -> tuple[str, ...]:
    if not batch:
        return collected
    overlap_limit = min(len(collected), len(batch))
    for overlap in range(overlap_limit, 0, -1):
        if collected[-overlap:] == batch[:overlap]:
            return collected + batch[overlap:]
    if batch == collected[: len(batch)]:
        return collected
    return collected + batch


def _parse_snapshot(raw: Mapping[str, object]) -> ReaderSnapshot:
    glyphs = tuple(
        CanvasGlyph(
            text=str(item["text"]),
            x=float(item["x"]),
            y=float(item["y"]),
            sequence=int(item["sequence"]),
            canvas_id=str(item["canvas_id"]),
            transform=tuple(float(value) for value in item.get("transform", (1, 0, 0, 1, 0, 0))),
        )
        for item in raw.get("glyphs", [])
    )
    rects = {
        str(item["canvas_id"]): CanvasRect(
            float(item["x"]), float(item["y"]), float(item["width"]), float(item["height"])
        )
        for item in raw.get("rects", [])
    }
    position = str(raw.get("position", ""))
    images: list[ImageOccurrence] = []
    for index, item in enumerate(raw.get("images", []), 1):
        src = str(item["src"])
        identity = normalize_image_identity(src)
        occurrence_seed = f"{position}:{index}:{identity}".encode()
        occurrence_hash = hashlib.sha256(occurrence_seed).hexdigest()[:8]
        images.append(
            ImageOccurrence(
                occurrence_id=f"p{position or 'unknown'}-i{index}-{occurrence_hash}",
                src=src,
                normalized_identity=identity,
                rect=CanvasRect(
                    float(item["x"]),
                    float(item["y"]),
                    float(item["width"]),
                    float(item["height"]),
                ),
                sequence=len(glyphs) + index,
                natural_width=int(item.get("natural_width", 0)),
                natural_height=int(item.get("natural_height", 0)),
            )
        )
    chapter_title = str(raw.get("chapter_title", "")).strip()
    fingerprint = page_fingerprint(chapter_title, position, glyphs, images)
    return ReaderSnapshot(
        chapter_title,
        position,
        glyphs,
        tuple(images),
        rects,
        fingerprint,
        bool(raw.get("pending", False)),
    )


async def _emit(progress: Progress | None, event: str, details: Mapping[str, object]) -> None:
    if progress is None:
        return
    emitted = progress(event, details)
    if emitted is not None:
        await emitted
