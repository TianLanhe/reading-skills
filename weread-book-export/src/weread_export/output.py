"""Manifest、托管标记和可恢复的同文件系统原子发布。"""

import fcntl
import hashlib
import json
import os
import shutil
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .models import (
    BookCapture,
    ExportResult,
    PublishTransaction,
    RenderedBook,
    ResultRecord,
    ValidationReport,
)
from .registry import Registry

MANIFEST_NAME = "manifest.json"
MARKER_NAME = ".weread-export-managed"


@dataclass(frozen=True, slots=True)
class VerificationResult:
    ok: bool
    reason: str = "ok"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_sha256(path: Path) -> str:
    return file_sha256(path)


def build_manifest(
    capture: BookCapture,
    rendered: RenderedBook,
    validation: ValidationReport,
    root: Path,
    markdown_path: Path,
    exported_at: datetime,
    retained_until: datetime | None,
) -> dict[str, object]:
    files: list[dict[str, object]] = []
    relative_markdown = markdown_path.relative_to(root).as_posix()
    files.append(_file_entry(markdown_path, relative_markdown, "text/markdown"))
    for image in rendered.images:
        path = root / image.relative_path
        files.append(_file_entry(path, image.relative_path.as_posix(), image.media_type))
    return {
        "schema_version": 1,
        "app_version": __version__,
        "book": {
            "book_id": capture.book_id,
            "title": capture.title,
            "author": capture.author,
            "source_url": f"https://weread.qq.com/web/reader/{capture.book_id}",
        },
        "exported_at": exported_at.astimezone(UTC).isoformat(),
        "status": "validated",
        "statistics": {
            "chapters": len(capture.chapters),
            "characters": len(rendered.canonical_text),
            "image_occurrences": len(capture.image_occurrences),
            "image_files": len(rendered.images),
        },
        "files": files,
        "validation": validation.to_json(),
        "retention": {
            "managed": retained_until is not None,
            "retained_until": retained_until.astimezone(UTC).isoformat()
            if retained_until
            else None,
        },
    }


def _file_entry(path: Path, relative: str, media_type: str) -> dict[str, object]:
    return {
        "path": relative,
        "size_bytes": path.stat().st_size,
        "sha256": file_sha256(path),
        "media_type": media_type,
    }


def write_json_atomic(path: Path, payload: Mapping[str, object], mode: int = 0o600) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        path.chmod(mode)
    finally:
        temporary.unlink(missing_ok=True)


def managed_root_id(managed_root: Path) -> str:
    return hashlib.sha256(str(managed_root.resolve()).encode()).hexdigest()


def write_managed_marker(
    root: Path,
    book_id: str,
    result_id: str,
    managed_root: Path,
) -> Path:
    manifest = root / MANIFEST_NAME
    marker = root / MARKER_NAME
    write_json_atomic(
        marker,
        {
            "schema_version": 1,
            "book_id": book_id,
            "result_id": result_id,
            "manifest_sha256": manifest_sha256(manifest),
            "managed_root_id": managed_root_id(managed_root),
        },
    )
    return marker


def verify_managed_result(record: ResultRecord) -> VerificationResult:
    root = Path(os.path.abspath(record.path))
    managed_root = Path(os.path.abspath(record.managed_root))
    if not root.is_relative_to(managed_root) or root == managed_root:
        return VerificationResult(False, "outside_managed_root")
    if _has_symlink_component(managed_root, root):
        return VerificationResult(False, "symlink")
    if not root.is_dir():
        return VerificationResult(False, "missing")
    manifest_path = root / MANIFEST_NAME
    marker_path = root / MARKER_NAME
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return VerificationResult(False, "metadata_invalid")
    actual_manifest_hash = manifest_sha256(manifest_path)
    if actual_manifest_hash != record.manifest_sha256:
        return VerificationResult(False, "manifest_modified")
    if (
        marker.get("book_id") != record.book_id
        or marker.get("result_id") != record.result_id
        or marker.get("manifest_sha256") != actual_manifest_hash
        or marker.get("managed_root_id") != managed_root_id(managed_root)
    ):
        return VerificationResult(False, "identity_mismatch")

    entries = manifest.get("files")
    if not isinstance(entries, list):
        return VerificationResult(False, "manifest_invalid")
    expected = {MANIFEST_NAME, MARKER_NAME}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            return VerificationResult(False, "manifest_invalid")
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            return VerificationResult(False, "unsafe_member")
        path = root / relative
        if path.is_symlink() or not path.is_file():
            return VerificationResult(False, "missing_member")
        size_matches = path.stat().st_size == entry.get("size_bytes")
        hash_matches = file_sha256(path) == entry.get("sha256")
        if not size_matches or not hash_matches:
            return VerificationResult(False, "modified")
        expected.add(relative.as_posix())
    keep = root / ".keep"
    if keep.is_file() and not keep.is_symlink():
        expected.add(".keep")
    actual: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            return VerificationResult(False, "symlink")
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    if actual != expected:
        return VerificationResult(False, "unexpected_members")
    return VerificationResult(True)


def _has_symlink_component(managed_root: Path, root: Path) -> bool:
    current = managed_root
    if current.is_symlink():
        return True
    for part in root.relative_to(managed_root).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


class AtomicPublisher:
    def __init__(self, registry: Registry) -> None:
        self.registry = registry

    def publish(
        self,
        staging: Path,
        target: Path,
        job_id: str,
        fault: Callable[[str], None] | None = None,
    ) -> Path:
        self._validate_paths(staging, target, job_id)
        backup = target.parent / f".weread-export-backup-{job_id}"
        transaction = PublishTransaction(
            job_id, target, staging, backup, "prepared", datetime.now(UTC)
        )
        self.registry.upsert_publish_transaction(transaction)
        if target.exists():
            if backup.exists():
                raise RuntimeError("发布备份目录已存在")
            target.rename(backup)
            transaction = PublishTransaction(
                job_id, target, staging, backup, "backup_created", datetime.now(UTC)
            )
            self.registry.upsert_publish_transaction(transaction)
            if fault:
                fault("after_backup")
        staging.rename(target)
        self.registry.upsert_publish_transaction(
            PublishTransaction(job_id, target, staging, backup, "new_published", datetime.now(UTC))
        )
        if fault:
            fault("after_publish")
        return target

    def finalize(self, job_id: str) -> None:
        transactions = {
            transaction.job_id: transaction
            for transaction in self.registry.list_publish_transactions()
        }
        transaction = transactions.get(job_id)
        if transaction is None:
            return
        if transaction.phase != "new_published":
            raise RuntimeError("发布事务尚未完成，不能 finalize")
        if transaction.backup.exists():
            _remove_known_tree(transaction.backup, ".weread-export-backup-")
        self.registry.delete_publish_transaction(job_id)

    def recover_transactions(self) -> None:
        with self._recovery_lock():
            self._recover_transactions_unlocked()

    def _recover_transactions_unlocked(self) -> None:
        for transaction in self.registry.list_publish_transactions():
            if self.registry.job_owned_by_live_process(transaction.job_id):
                continue
            if transaction.phase in {"prepared", "backup_created"}:
                if not transaction.target.exists() and transaction.backup.exists():
                    transaction.backup.rename(transaction.target)
                if transaction.staging.exists():
                    _remove_known_tree(transaction.staging, ".weread-export-staging-")
            elif transaction.phase == "new_published":
                verification = verify_export_tree(transaction.target, marker_policy="optional")
                if verification.ok:
                    if (transaction.target / MARKER_NAME).is_file():
                        self._register_recovered_target(transaction.target)
                    if transaction.backup.exists():
                        _remove_known_tree(transaction.backup, ".weread-export-backup-")
                else:
                    if transaction.target.exists():
                        quarantine = transaction.staging
                        if quarantine.exists():
                            quarantine = quarantine.with_name(f"{quarantine.name}-invalid")
                        if quarantine.exists():
                            quarantine = quarantine.with_name(
                                f"{quarantine.name}-{datetime.now(UTC).timestamp():.0f}"
                            )
                        transaction.target.rename(quarantine)
                    if not transaction.target.exists() and transaction.backup.exists():
                        transaction.backup.rename(transaction.target)
            self.registry.delete_publish_transaction(transaction.job_id)

    @contextmanager
    def _recovery_lock(self):
        path = self.registry.path.parent / "publish-recovery.lock"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open("a+b") as stream:
            path.chmod(0o600)
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _register_recovered_target(self, target: Path) -> None:
        marker = json.loads((target / MARKER_NAME).read_text(encoding="utf-8"))
        result_id = str(marker["result_id"])
        manifest_path = target / MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        book_id = str(manifest["book"]["book_id"])
        if self.registry.result_exists(result_id):
            self.registry.invalidate_other_results(book_id, result_id)
            if (target / ".keep").is_file():
                self.registry.set_keep(result_id, True)
            return
        markdown_entry = next(
            entry for entry in manifest["files"] if entry["media_type"] == "text/markdown"
        )
        statistics = manifest["statistics"]
        retained_value = manifest.get("retention", {}).get("retained_until")
        retained_until = datetime.fromisoformat(retained_value) if retained_value else None
        result = ExportResult(
            "completed",
            manifest["book"]["book_id"],
            manifest["book"]["title"],
            target,
            target / markdown_entry["path"],
            manifest_path,
            statistics["chapters"],
            statistics["characters"],
            statistics["image_files"],
            retained_until,
        )
        self.registry.register_export_result(
            result,
            manifest_sha256(manifest_path),
            created_at=datetime.fromisoformat(manifest["exported_at"]),
            result_id=result_id,
        )
        self.registry.invalidate_other_results(result.book_id, result_id)
        if (target / ".keep").is_file():
            self.registry.set_keep(result_id, True)

    @staticmethod
    def _validate_paths(staging: Path, target: Path, job_id: str) -> None:
        if staging.parent != target.parent:
            raise ValueError("staging 与目标必须位于同一父目录")
        if staging.name != f".weread-export-staging-{job_id}" or not staging.is_dir():
            raise ValueError("staging 身份不匹配")


def _remove_known_tree(path: Path, prefix: str) -> None:
    if path.is_symlink() or not path.name.startswith(prefix):
        raise RuntimeError("拒绝删除身份不明的发布目录")
    shutil.rmtree(path)


def verify_export_tree(
    root: Path,
    expected_book_id: str | None = None,
    marker_policy: str = "optional",
) -> VerificationResult:
    if marker_policy not in {"required", "forbidden", "optional"}:
        raise ValueError("marker_policy 必须是 required、forbidden 或 optional")
    if not root.is_dir() or root.is_symlink():
        return VerificationResult(False, "missing_or_symlink")
    try:
        manifest_path = root / MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        book_id = str(manifest["book"]["book_id"])
        if expected_book_id is not None and book_id != expected_book_id:
            return VerificationResult(False, "book_id_mismatch")
        marker_path = root / MARKER_NAME
        marker_exists = marker_path.is_file() and not marker_path.is_symlink()
        if marker_policy == "required" and not marker_exists:
            return VerificationResult(False, "marker_missing")
        if marker_policy == "forbidden" and marker_path.exists():
            return VerificationResult(False, "unexpected_marker")
        expected_files = {MANIFEST_NAME}
        if marker_exists:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            if (
                marker.get("book_id") != book_id
                or not marker.get("result_id")
                or marker.get("manifest_sha256") != manifest_sha256(manifest_path)
                or marker.get("managed_root_id") != managed_root_id(root.parent)
            ):
                return VerificationResult(False, "marker_invalid")
            expected_files.add(MARKER_NAME)
            keep = root / ".keep"
            if keep.is_file() and not keep.is_symlink():
                expected_files.add(".keep")
        expected_directories = {"images"}
        for entry in manifest["files"]:
            relative = Path(entry["path"])
            if relative.is_absolute() or ".." in relative.parts:
                return VerificationResult(False, "unsafe_manifest_path")
            path = root / relative
            if path.is_symlink() or not path.is_file():
                return VerificationResult(False, "file_missing_or_symlink")
            size_matches = path.stat().st_size == entry["size_bytes"]
            hash_matches = file_sha256(path) == entry["sha256"]
            if not size_matches or not hash_matches:
                return VerificationResult(False, "file_modified")
            expected_files.add(relative.as_posix())
            parent = relative.parent
            while parent != Path("."):
                expected_directories.add(parent.as_posix())
                parent = parent.parent
        actual_files: set[str] = set()
        actual_directories: set[str] = set()
        for path in root.rglob("*"):
            if path.is_symlink():
                return VerificationResult(False, "symlink")
            relative = path.relative_to(root).as_posix()
            if path.is_file():
                actual_files.add(relative)
            elif path.is_dir():
                actual_directories.add(relative)
        if actual_files != expected_files or actual_directories != expected_directories:
            return VerificationResult(False, "unexpected_members")
        return VerificationResult(True)
    except (KeyError, StopIteration, OSError, TypeError, ValueError, json.JSONDecodeError):
        return VerificationResult(False, "metadata_invalid")
