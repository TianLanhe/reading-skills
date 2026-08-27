"""同书共享锁与不同书并行执行。"""

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from .auth import AsyncFileLock
from .models import ExportResult, JobStatus
from .registry import Registry


class ExportRunner(Protocol):
    async def run(self, book_id: str) -> ExportResult: ...


class BookLock(AsyncFileLock):
    def __init__(self, locks_dir: Path, book_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", book_id):
            raise ValueError("不安全的 Book ID")
        super().__init__(locks_dir / f"book-{book_id}.lock")


@dataclass(slots=True)
class JobManager:
    registry: Registry
    locks_dir: Path
    runner: ExportRunner

    @classmethod
    def for_test(cls, root: Path, runner: ExportRunner) -> "JobManager":
        registry = Registry(root / "state.sqlite3")
        registry.initialize()
        return cls(registry, root / "locks", runner)

    async def export(self, book_id: str, force: bool = False) -> ExportResult:
        async with BookLock(self.locks_dir, book_id):
            existing = self.registry.latest_export_result(book_id)
            if existing is not None and not force:
                if self._valid_result(existing):
                    return replace(existing, status="reused")
                self.registry.invalidate_latest(book_id)

            job = self.registry.create_job(book_id)
            self.registry.transition_job(job.job_id, JobStatus.BOOTSTRAPPING)
            self.registry.transition_job(job.job_id, JobStatus.CAPTURING)
            try:
                result = await self.runner.run(book_id)
                self.registry.transition_job(job.job_id, JobStatus.DOWNLOADING_IMAGES)
                self.registry.transition_job(job.job_id, JobStatus.VALIDATING)
                self.registry.transition_job(job.job_id, JobStatus.PUBLISHING)
                self.registry.register_export_result(result)
                self.registry.transition_job(job.job_id, JobStatus.COMPLETED)
                return result
            except Exception as error:
                current = self.registry.get_job(job.job_id)
                if JobStatus.FAILED in self._allowed_failure_from(current.status):
                    self.registry.transition_job(
                        job.job_id,
                        JobStatus.FAILED,
                        error_reason=type(error).__name__,
                    )
                raise

    @staticmethod
    def _valid_result(result: ExportResult) -> bool:
        return (
            result.output_dir.is_dir()
            and result.markdown_path.is_file()
            and result.manifest_path.is_file()
        )

    @staticmethod
    def _allowed_failure_from(status: JobStatus) -> set[JobStatus]:
        from .registry import ALLOWED_TRANSITIONS

        return ALLOWED_TRANSITIONS.get(status, set())
