"""认证、抓取、图片、验证和原子发布的单本导出事务。"""

import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .auth import AsyncFileLock, AuthStore, ensure_auth
from .browser import BrowserFactory
from .chapters import render_book_markdown
from .cleanup import CleanupService
from .errors import ExitCode, ExportError
from .images import download_images
from .jobs import BookLock
from .models import BookCapture, ExportRequest, ExportResult, JobStatus
from .output import (
    AtomicPublisher,
    build_manifest,
    manifest_sha256,
    verify_export_tree,
    verify_managed_result,
    write_json_atomic,
    write_managed_marker,
)
from .paths import AppPaths, safe_book_dir_name
from .reader import WeReadReader
from .registry import ALLOWED_TRANSITIONS, Registry
from .validation import validate_export


@dataclass(slots=True)
class ExportService:
    paths: AppPaths
    browser_factory: BrowserFactory
    auth_store: AuthStore
    auth_lock: AsyncFileLock
    reader: WeReadReader
    registry: Registry | None = None
    cleanup: CleanupService | None = None

    @classmethod
    def default(cls, paths: AppPaths) -> "ExportService":
        registry = Registry(paths.state_db)
        registry.initialize()
        return cls(
            paths=paths,
            browser_factory=BrowserFactory(paths.jobs),
            auth_store=AuthStore(paths.auth_state),
            auth_lock=AsyncFileLock(paths.locks / "auth.lock"),
            reader=WeReadReader(),
            registry=registry,
            cleanup=CleanupService(registry, paths),
        )

    async def capture_book(self, request: ExportRequest, progress=None) -> BookCapture:
        self.paths.create_private_dirs()
        state = await ensure_auth(
            self.browser_factory,
            self.auth_store,
            self.auth_lock,
            progress=progress,
        )
        job_dir = self.paths.jobs / f"{request.book_ref.book_id}-{uuid.uuid4().hex}"
        job_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
        try:
            return await self._capture_with_browser_recovery(
                request,
                state,
                job_dir,
                progress,
            )
        finally:
            if job_dir.exists():
                shutil.rmtree(job_dir)

    async def _capture_with_browser_recovery(
        self,
        request: ExportRequest,
        state,
        job_dir: Path,
        progress=None,
    ) -> BookCapture:
        last_error: Exception | None = None
        for attempt in range(1, 3):
            try:
                async with self.browser_factory.open(
                    job_dir / f"browser-{attempt}",
                    headless=True,
                    storage_state=state,
                ) as context:
                    return await self.reader.capture(
                        request.book_ref,
                        context,
                        checkpoint=None,
                        progress=progress,
                    )
            except ExportError:
                raise
            except Exception as error:
                last_error = error
        raise ExportError(
            reason="capture_failed",
            message="浏览器恢复两次后仍无法完成抓取",
            exit_code=ExitCode.CAPTURE_FAILED,
            details={"error_type": type(last_error).__name__ if last_error else "unknown"},
        )

    async def _download_with_browser_recovery(
        self,
        state,
        occurrences,
        destination: Path,
        job_dir: Path,
        progress=None,
    ):
        last_error: Exception | None = None
        for attempt in range(1, 3):
            try:
                async with self.browser_factory.open(
                    job_dir / f"image-browser-{attempt}",
                    headless=True,
                    storage_state=state,
                ) as context:
                    return await download_images(
                        context.request,
                        occurrences,
                        destination,
                        progress=progress,
                    )
            except ExportError:
                raise
            except Exception as error:
                last_error = error
        raise ExportError(
            reason="image_download_failed",
            message="浏览器恢复两次后仍无法完成图片下载",
            exit_code=ExitCode.CAPTURE_FAILED,
            details={"error_type": type(last_error).__name__ if last_error else "unknown"},
        )

    async def export_book(self, request: ExportRequest, progress=None) -> ExportResult:
        self.paths.create_private_dirs()
        registry = self._registry()
        cleanup = self._cleanup(registry)
        publisher = AtomicPublisher(registry)
        publisher.recover_transactions()
        cleanup.run()
        if request.output_dir is None:
            cleanup.check_disk_reserve()
            cleanup.check_managed_limit(request.book_ref.book_id)

        async with BookLock(self.paths.locks, request.book_ref.book_id):
            reused = self._reusable_result(request, registry)
            if reused is not None:
                return reused

            job = registry.create_job(
                request.book_ref.book_id,
                managed=request.output_dir is None,
            )
            registry.transition_job(job.job_id, JobStatus.BOOTSTRAPPING)
            job_dir = self.paths.jobs / job.job_id
            job_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
            staging: Path | None = None
            succeeded = False
            try:
                registry.transition_job(job.job_id, JobStatus.WAITING_FOR_AUTH)
                state = await ensure_auth(
                    self.browser_factory,
                    self.auth_store,
                    self.auth_lock,
                    progress=progress,
                )
                registry.transition_job(job.job_id, JobStatus.CAPTURING)
                capture = await self._capture_with_browser_recovery(
                    request,
                    state,
                    job_dir,
                    progress,
                )
                target, managed = self._target_for(request, capture)
                previous_record = self._check_target_collision(
                    target, capture.book_id, request.force, registry, managed
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                staging = target.parent / f".weread-export-staging-{job.job_id}"
                staging.mkdir(mode=0o700)
                (staging / "images").mkdir(mode=0o700)
                registry.transition_job(job.job_id, JobStatus.DOWNLOADING_IMAGES)
                image_files = await self._download_with_browser_recovery(
                    state,
                    capture.image_occurrences,
                    staging / "images",
                    job_dir,
                    progress,
                )

                rendered = render_book_markdown(capture, image_files)
                markdown_name = self._markdown_name(capture.title, capture.book_id)
                markdown_path = staging / markdown_name
                markdown_path.write_text(rendered.markdown, encoding="utf-8")
                registry.transition_job(job.job_id, JobStatus.VALIDATING)
                validation = validate_export(capture, rendered, staging)
                validation.require_success()
                now = datetime.now(UTC)
                retained_until = now + timedelta(days=7) if managed else None
                manifest = build_manifest(
                    capture,
                    rendered,
                    validation,
                    staging,
                    markdown_path,
                    now,
                    retained_until,
                )
                manifest_path = staging / "manifest.json"
                write_json_atomic(manifest_path, manifest)
                result_id = uuid.uuid4().hex
                if managed:
                    write_managed_marker(
                        staging,
                        capture.book_id,
                        result_id,
                        self.paths.managed_output,
                    )
                    if previous_record is not None and (
                        previous_record.kept or (previous_record.path / ".keep").is_file()
                    ):
                        (staging / ".keep").touch(mode=0o600)
                result = ExportResult(
                    "completed",
                    capture.book_id,
                    capture.title,
                    target,
                    target / markdown_name,
                    target / "manifest.json",
                    len(capture.chapters),
                    len(rendered.canonical_text),
                    len(rendered.images),
                    retained_until,
                    validation,
                )
                registry.transition_job(job.job_id, JobStatus.PUBLISHING)
                publisher.publish(staging, target, job.job_id)
                staging = None
                if managed:
                    registry.register_export_result(
                        result,
                        manifest_sha256(result.manifest_path),
                        created_at=now,
                        result_id=result_id,
                    )
                    registry.invalidate_other_results(capture.book_id, result_id)
                    if (result.output_dir / ".keep").is_file():
                        registry.set_keep(result_id, True)
                publisher.finalize(job.job_id)
                registry.transition_job(job.job_id, JobStatus.COMPLETED)
                succeeded = True
                cleanup.run()
                return result
            except Exception as error:
                self._transition_failure(registry, job.job_id, error)
                if staging is not None and staging.exists():
                    diagnostic = job_dir / "failed-output"
                    if diagnostic.exists():
                        shutil.rmtree(diagnostic)
                    shutil.move(str(staging), str(diagnostic))
                raise
            finally:
                if succeeded and job_dir.exists():
                    shutil.rmtree(job_dir)

    def _registry(self) -> Registry:
        if self.registry is None:
            self.registry = Registry(self.paths.state_db)
            self.registry.initialize()
        return self.registry

    def _cleanup(self, registry: Registry) -> CleanupService:
        if self.cleanup is None:
            self.cleanup = CleanupService(registry, self.paths)
        return self.cleanup

    def _reusable_result(self, request: ExportRequest, registry: Registry) -> ExportResult | None:
        if request.force or request.output_dir is not None:
            return None
        record = registry.latest_result_record(request.book_ref.book_id)
        if record is None:
            return None
        verification = verify_managed_result(record)
        if verification.ok:
            existing = registry.latest_export_result(request.book_ref.book_id)
            if existing is not None:
                from dataclasses import replace

                return replace(existing, status="reused")
        if verification.reason == "missing":
            registry.invalidate_result(record.result_id)
            return None
        raise ExportError(
            "existing_result_modified",
            "已有托管结果被修改或身份不一致；请改用自定义输出目录，或经确认后使用 --force",
            ExitCode.ACTION_REQUIRED,
            {"result_id": record.result_id, "reason": verification.reason},
        )

    def _target_for(self, request: ExportRequest, capture: BookCapture) -> tuple[Path, bool]:
        if request.output_dir is None:
            return (
                self.paths.managed_output / safe_book_dir_name(capture.title, capture.book_id),
                True,
            )
        parent = request.output_dir.expanduser().absolute()
        if parent in {Path("/"), self.paths.home}:
            raise ExportError(
                "unsafe_output_path",
                "自定义输出父目录不能是根目录或用户主目录",
                ExitCode.INVALID_INPUT,
            )
        return parent / safe_book_dir_name(capture.title, capture.book_id), False

    @staticmethod
    def _markdown_name(title: str, book_id: str) -> str:
        directory_name = safe_book_dir_name(title, book_id)
        return f"{directory_name.removesuffix(f'-{book_id}')}.md"

    @staticmethod
    def _check_target_collision(
        target: Path,
        book_id: str,
        force: bool,
        registry: Registry,
        managed: bool,
    ):
        record = None
        if not target.exists():
            return None
        if not force:
            raise ExportError(
                "output_exists",
                "目标目录已存在；请更换目录，或经确认后使用 --force",
                ExitCode.ACTION_REQUIRED,
                {"output_dir": str(target)},
            )
        if managed:
            record = registry.latest_result_record(book_id)
            verification = (
                verify_managed_result(record)
                if record is not None and record.path == target
                else None
            )
        else:
            verification = verify_export_tree(
                target,
                expected_book_id=book_id,
                marker_policy="forbidden",
            )
        if verification is None or not verification.ok:
            raise ExportError(
                "unsafe_existing_output",
                "即使使用 --force，也只允许替换同一 Book ID 且未被修改的既有导出",
                ExitCode.ACTION_REQUIRED,
                {
                    "output_dir": str(target),
                    "reason": verification.reason if verification is not None else "unregistered",
                },
            )
        return record if managed else None

    @staticmethod
    def _transition_failure(registry: Registry, job_id: str, error: Exception) -> None:
        current = registry.get_job(job_id)
        desired = (
            JobStatus.VALIDATION_FAILED
            if isinstance(error, ExportError) and error.exit_code is ExitCode.VALIDATION_FAILED
            else JobStatus.FAILED
        )
        if desired in ALLOWED_TRANSITIONS.get(current.status, set()):
            registry.transition_job(job_id, desired, error_reason=type(error).__name__)
