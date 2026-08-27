"""Canvas 文字、图片与视觉页面的无损规范化。"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from .models import CanvasGlyph, CanvasRect, ContentBlock, ImageOccurrence, TextLine

CANVAS_HOOK = r"""
(() => {
  if (window.__wereadExportCanvasHook) return;
  const state = {glyphs: [], sequence: 0, nextCanvasId: 0, generation: 0};
  const canvasIds = new WeakMap();
  const canvas_id = (canvas) => {
    if (!canvasIds.has(canvas)) {
      const id = `canvas-${state.nextCanvasId++}`;
      canvasIds.set(canvas, id);
      canvas.dataset.wereadExportCanvasId = id;
    }
    return canvasIds.get(canvas);
  };
  const original = CanvasRenderingContext2D.prototype.fillText;
  const originalClear = CanvasRenderingContext2D.prototype.clearRect;
  const clear_canvas = (canvas) => {
    const id = canvas_id(canvas);
    state.glyphs = state.glyphs.filter((item) => item.canvas_id !== id);
    state.generation += 1;
  };
  CanvasRenderingContext2D.prototype.clearRect = function(x, y, width, height) {
    if (x <= 0 && y <= 0 && width >= this.canvas.width && height >= this.canvas.height) {
      clear_canvas(this.canvas);
    }
    return originalClear.apply(this, arguments);
  };
  for (const property of ['width', 'height']) {
    const descriptor = Object.getOwnPropertyDescriptor(HTMLCanvasElement.prototype, property);
    if (!descriptor?.get || !descriptor?.set || descriptor.configurable === false) continue;
    Object.defineProperty(HTMLCanvasElement.prototype, property, {
      configurable: descriptor.configurable,
      enumerable: descriptor.enumerable,
      get: descriptor.get,
      set(value) {
        clear_canvas(this);
        return descriptor.set.call(this, value);
      }
    });
  }
  CanvasRenderingContext2D.prototype.fillText = function(text, x, y, maxWidth) {
    if (text !== undefined && text !== null && String(text).length) {
      const matrix = this.getTransform();
      state.glyphs.push({
        text: String(text), x, y,
        sequence: state.sequence++,
        canvas_id: canvas_id(this.canvas),
        transform: [matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f],
        font: this.font,
        textAlign: this.textAlign,
        textBaseline: this.textBaseline
      });
    }
    return original.apply(this, arguments);
  };
  window.__wereadExportCanvasHook = {
    snapshot: () => state.glyphs.map((item) => ({...item})),
    reset: () => { state.glyphs = []; state.sequence = 0; },
    count: () => state.glyphs.length,
    generation: () => state.generation
  };
})();
"""


def normalize_image_identity(url: str) -> str:
    """移除短期签名参数，但保留资源的 scheme/host/path 身份。"""
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", ""))


def _visual_point(glyph: CanvasGlyph) -> tuple[float, float]:
    a, b, c, d, e, f = glyph.transform
    return (a * glyph.x + c * glyph.y + e, b * glyph.x + d * glyph.y + f)


def glyphs_to_lines(
    glyphs: Sequence[CanvasGlyph], y_tolerance: float = 3.0
) -> tuple[TextLine, ...]:
    """按 canvas 和视觉 y 坐标分行；不做内容去重。"""
    rows: dict[tuple[str, int], list[tuple[CanvasGlyph, float, float]]] = {}
    for glyph in glyphs:
        if not glyph.text:
            continue
        x, y = _visual_point(glyph)
        key = (glyph.canvas_id, round(y / y_tolerance))
        rows.setdefault(key, []).append((glyph, x, y))

    lines: list[TextLine] = []
    for items in rows.values():
        ordered = sorted(items, key=lambda item: (item[1], item[0].sequence))
        text = "".join(item[0].text for item in ordered)
        if not text.strip():
            continue
        xs = [item[1] for item in ordered]
        ys = [item[2] for item in ordered]
        lines.append(
            TextLine(
                text=text.strip(),
                rect=CanvasRect(min(xs), min(ys), max(max(xs) - min(xs), 1), y_tolerance),
                sequence=min(item[0].sequence for item in ordered),
            )
        )
    return tuple(sorted(lines, key=lambda line: (line.rect.y, line.sequence)))


def split_visual_pages(
    glyphs: Sequence[CanvasGlyph], canvas_rects: Mapping[str, CanvasRect]
) -> tuple[tuple[CanvasGlyph, ...], ...]:
    """按 DOM canvas 位置拆页；单 canvas 仅在强 y 回绕证据存在时拆分。"""
    by_canvas: dict[str, list[CanvasGlyph]] = {}
    for glyph in glyphs:
        by_canvas.setdefault(glyph.canvas_id, []).append(glyph)
    if len(by_canvas) > 1:
        canvas_order = sorted(
            by_canvas,
            key=lambda canvas_id: (
                canvas_rects.get(canvas_id, CanvasRect(0, 0, 0, 0)).x,
                canvas_rects.get(canvas_id, CanvasRect(0, 0, 0, 0)).y,
            ),
        )
        return tuple(
            tuple(sorted(by_canvas[canvas_id], key=lambda glyph: glyph.sequence))
            for canvas_id in canvas_order
        )

    ordered = tuple(sorted(glyphs, key=lambda glyph: glyph.sequence))
    if len(ordered) < 20:
        return (ordered,)
    canvas_id = ordered[0].canvas_id
    height = canvas_rects.get(canvas_id, CanvasRect(0, 0, 0, 800)).height or 800
    for index in range(10, len(ordered) - 9):
        previous_y = _visual_point(ordered[index - 1])[1]
        current_y = _visual_point(ordered[index])[1]
        if previous_y > height * 0.6 and current_y < height * 0.3:
            return (ordered[:index], ordered[index:])
    return (ordered,)


def _image_belongs_to_rect(image: ImageOccurrence, rect: CanvasRect) -> bool:
    center_x = image.rect.x + image.rect.width / 2
    center_y = image.rect.y + image.rect.height / 2
    return rect.x <= center_x <= rect.right and rect.y <= center_y <= rect.bottom


def build_page_blocks(
    glyphs: Sequence[CanvasGlyph],
    images: Sequence[ImageOccurrence],
    canvas_rects: Mapping[str, CanvasRect],
) -> tuple[ContentBlock, ...]:
    """将视觉页按左到右、页内从上到下转成内容块。"""
    visual_pages = split_visual_pages(glyphs, canvas_rects)
    blocks: list[ContentBlock] = []
    emitted_images: set[str] = set()
    sequence = 0
    for visual_page in visual_pages:
        canvas_ids = {glyph.canvas_id for glyph in visual_page}
        page_rects = [canvas_rects[item] for item in canvas_ids if item in canvas_rects]
        page_images = [
            image
            for image in images
            if image.occurrence_id not in emitted_images
            and (not page_rects or any(_image_belongs_to_rect(image, rect) for rect in page_rects))
        ]
        items: list[tuple[float, int, str, TextLine | ImageOccurrence]] = []
        for line in glyphs_to_lines(visual_page):
            offset = page_rects[0].y if len(page_rects) == 1 else 0
            items.append((line.rect.y + offset, line.sequence, "paragraph", line))
        for image in page_images:
            items.append((image.rect.y, image.sequence, "image", image))
        for _y, _source_sequence, kind, payload in sorted(
            items, key=lambda item: (item[0], item[1])
        ):
            if kind == "paragraph":
                line = payload
                assert isinstance(line, TextLine)
                blocks.append(ContentBlock(kind="paragraph", text=line.text, sequence=sequence))
            else:
                image = payload
                assert isinstance(image, ImageOccurrence)
                emitted_images.add(image.occurrence_id)
                blocks.append(ContentBlock(kind="image", image=image, sequence=sequence))
            sequence += 1

    for image in images:
        if image.occurrence_id not in emitted_images:
            blocks.append(ContentBlock(kind="image", image=image, sequence=sequence))
            sequence += 1
    return tuple(blocks)


def page_fingerprint(
    chapter_title: str,
    position: str,
    glyphs: Sequence[CanvasGlyph],
    images: Sequence[ImageOccurrence],
) -> str:
    payload = {
        "chapter": chapter_title,
        "position": position,
        "glyphs": [
            (glyph.text, round(glyph.x, 1), round(glyph.y, 1), glyph.canvas_id) for glyph in glyphs
        ],
        "images": [
            (
                normalize_image_identity(image.src),
                round(image.rect.x),
                round(image.rect.y),
                round(image.rect.width),
                round(image.rect.height),
            )
            for image in images
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
