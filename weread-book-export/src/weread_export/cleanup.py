"""托管结果保留期、容量上限和身份校验后的安全清理。"""

import fcntl
import shutil
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .errors import ExitCode, ExportError
from .output import verify_managed_result
from .paths import AppPaths
from .registry import Registry


@dataclass(slots=True)
class CleanupReport:
    deleted: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    refused: list[dict[str, str]] = field(default_factory=list)
    runtime_deleted: list[str] = field(default_factory=list)


class CleanupService:
    def __init__(self, registry: Registry, paths: AppPaths) -> None:
        self.registry = registry
        self.paths = paths
        self.disk_usage = shutil.disk_usage

    def check_disk_reserve(self, reserve_bytes: int = 1024**3) -> None:
        self.paths.managed_output.mkdir(parents=True, exist_ok=True)
        free = self.disk_usage(self.paths.managed_output).free
        if free < reserve_bytes:
            raise ExportError(
                "insufficient_disk_space",
                "可用磁盘空间不足 1 GiB，导出已停止",
                ExitCode.INSUFFICIENT_DISK_SPACE,
                {"free_bytes": free, "required_bytes": reserve_bytes},
            )

    def check_managed_limit(self, new_book_id: str, maximum: int = 30) -> None:
        records = self.registry.list_results()
        if any(record.book_id == new_book_id for record in records):
            return
        if len(records) < maximum:
            return
        candidates = [
            {
                "result_id": record.result_id,
                "book_id": record.book_id,
                "title": record.title,
                "created_at": record.created_at.isoformat(),
                "size_bytes": record.size_bytes,
                "kept": record.kept,
                "modified": record.modified,
            }
            for record in records
        ]
        raise ExportError(
            "managed_output_limit",
            "托管导出结果已达到 30 本，请选择是否删除",
            ExitCode.ACTION_REQUIRED,
            {"maximum": maximum, "candidates": candidates},
        )

    def run(self, now: datetime | None = None, dry_run: bool = False) -> CleanupReport:
        with self._cleanup_lock():
            current = now or datetime.now(UTC)
            report = CleanupReport()
            self._cleanup_runtime(current, dry_run, report)
            for record in self.registry.list_results():
                if (
                    record.kept
                    or (record.path / ".keep").exists()
                    or record.retained_until > current
                ):
                    report.kept.append(record.book_id)
                    continue
                verification = verify_managed_result(record)
                if not verification.ok:
                    if verification.reason in {
                        "modified",
                        "manifest_modified",
                        "unexpected_members",
                    }:
                        report.modified.append(record.book_id)
                        if not dry_run:
                            self.registry.mark_modified(record.result_id)
                    else:
                        report.refused.append(
                            {"book_id": record.book_id, "reason": verification.reason}
                        )
                    continue
                report.deleted.append(record.book_id)
                if not dry_run:
                    self._delete_verified_root(record.path)
                    self.registry.invalidate_result(record.result_id)
            return report

    @contextmanager
    def _cleanup_lock(self):
        path = self.paths.locks / "cleanup.lock"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open("a+b") as stream:
            path.chmod(0o600)
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def keep(self, result_id: str) -> None:
        with self._cleanup_lock():
            record = self._record(result_id)
            verification = verify_managed_result(record)
            if not verification.ok:
                raise ValueError(f"结果不可保留：{verification.reason}")
            (record.path / ".keep").touch(mode=0o600, exist_ok=True)
            self.registry.set_keep(result_id, True)

    def unkeep(self, result_id: str) -> None:
        with self._cleanup_lock():
            record = self._record(result_id)
            keep = record.path / ".keep"
            if keep.is_symlink():
                raise ValueError("拒绝删除符号链接 .keep")
            keep.unlink(missing_ok=True)
            self.registry.set_keep(result_id, False)

    def delete_confirmed(self, result_ids: list[str]) -> CleanupReport:
        with self._cleanup_lock():
            report = CleanupReport()
            for result_id in result_ids:
                record = self._record(result_id)
                verification = verify_managed_result(record)
                if not verification.ok:
                    report.refused.append(
                        {"book_id": record.book_id, "reason": verification.reason}
                    )
                    continue
                self._delete_verified_root(record.path)
                self.registry.invalidate_result(record.result_id)
                report.deleted.append(record.book_id)
            return report

    def _record(self, result_id: str):
        for record in self.registry.list_results():
            if record.result_id == result_id:
                return record
        raise KeyError(result_id)

    def _cleanup_runtime(self, current: datetime, dry_run: bool, report: CleanupReport) -> None:
        cutoff = current - timedelta(hours=24)
        for runtime_root in (self.paths.jobs, self.paths.logs):
            if not runtime_root.exists() or runtime_root.is_symlink():
                continue
            for child in runtime_root.iterdir():
                if self.registry.job_owned_by_live_process(child.name):
                    continue
                modified = datetime.fromtimestamp(child.lstat().st_mtime, UTC)
                if modified > cutoff:
                    continue
                report.runtime_deleted.append(str(child))
                if dry_run:
                    continue
                if child.is_symlink() or child.is_file():
                    child.unlink()
                else:
                    shutil.rmtree(child)
        self._cleanup_publish_artifacts(current, dry_run, report)

    def _cleanup_publish_artifacts(
        self, current: datetime, dry_run: bool, report: CleanupReport
    ) -> None:
        root = self.paths.managed_output
        if not root.exists() or root.is_symlink():
            return
        cutoff = current - timedelta(hours=24)
        prefixes = (".weread-export-staging-", ".weread-export-backup-")
        for child in root.iterdir():
            if not child.name.startswith(prefixes):
                continue
            job_id = child.name
            for prefix in prefixes:
                if job_id.startswith(prefix):
                    job_id = job_id.removeprefix(prefix).split("-invalid", 1)[0]
                    break
            if self.registry.job_owned_by_live_process(job_id):
                continue
            if datetime.fromtimestamp(child.lstat().st_mtime, UTC) > cutoff:
                continue
            report.runtime_deleted.append(str(child))
            if dry_run:
                continue
            if child.is_symlink() or child.is_file():
                child.unlink()
            else:
                shutil.rmtree(child)

    def _delete_verified_root(self, root: Path) -> None:
        if root.is_symlink() or root.parent != self.paths.managed_output:
            raise RuntimeError("托管目录身份在删除前发生变化")
        shutil.rmtree(root)
