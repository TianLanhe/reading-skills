#!/usr/bin/env python3
"""对真实导出结果做不泄露正文的离线验收。"""

import argparse
import hashlib
import json
import re
from pathlib import Path

from PIL import Image

IMAGE_LINK_RE = re.compile(r"!\[[^]]*]\(([^)]+)\)")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_result(root: Path, kind: str) -> dict[str, object]:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files", [])
    errors: list[str] = []
    image_paths: list[Path] = []
    markdown_path: Path | None = None
    for entry in files:
        relative = Path(entry["path"])
        path = root / relative
        if not path.is_file() or path.is_symlink():
            errors.append(f"missing:{relative.as_posix()}")
            continue
        if path.stat().st_size != entry["size_bytes"] or file_hash(path) != entry["sha256"]:
            errors.append(f"hash:{relative.as_posix()}")
        if entry["media_type"] == "text/markdown":
            markdown_path = path
        elif str(entry["media_type"]).startswith("image/"):
            image_paths.append(path)
            try:
                with Image.open(path) as decoded:
                    decoded.verify()
            except Exception as error:
                errors.append(f"decode:{relative.as_posix()}:{type(error).__name__}")

    links: set[str] = set()
    if markdown_path is None:
        errors.append("missing_markdown")
    else:
        markdown = markdown_path.read_text(encoding="utf-8")
        links = set(IMAGE_LINK_RE.findall(markdown))
        for link in links:
            link_path = Path(link)
            if (
                link_path.is_absolute()
                or ".." in link_path.parts
                or not (root / link_path).is_file()
            ):
                errors.append(f"broken_link:{link}")

    validation = manifest.get("validation", {})
    if not validation.get("valid"):
        errors.append("manifest_validation_failed")
    for check in validation.get("checks", []):
        if not check.get("passed"):
            errors.append(f"check:{check.get('name', 'unknown')}")
    statistics = manifest.get("statistics", {})
    if statistics.get("image_files") != len(image_paths):
        errors.append("image_count")
    if len(links) != len(image_paths):
        errors.append("link_count")

    return {
        "kind": kind,
        "book_id": manifest.get("book", {}).get("book_id"),
        "title": manifest.get("book", {}).get("title"),
        "manifest_sha256": file_hash(manifest_path),
        "chapters": statistics.get("chapters"),
        "characters": statistics.get("characters"),
        "image_occurrences": statistics.get("image_occurrences"),
        "image_files": statistics.get("image_files"),
        "valid": not errors,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("--kind", choices=("text", "mixed", "image-heavy"), required=True)
    args = parser.parse_args()
    report = validate_result(args.result.expanduser().resolve(), args.kind)
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0 if report["valid"] else 31


if __name__ == "__main__":
    raise SystemExit(main())
