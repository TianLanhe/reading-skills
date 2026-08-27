"""面向本地 Agent 的稳定 JSON CLI。"""

import argparse
import asyncio
import json
import os
import platform
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from . import __release_channel__, __version__
from .auth import AsyncFileLock, AuthStore, ensure_auth
from .browser import BrowserFactory, locate_chrome
from .cleanup import CleanupReport, CleanupService
from .errors import ExitCode, ExportError
from .exporter import ExportService
from .models import ExportRequest
from .output import AtomicPublisher
from .paths import AppPaths, parse_book_ref
from .reader import WeReadReader
from .registry import Registry


class EventSink:
    def __init__(self, json_mode: bool) -> None:
        self.json_mode = json_mode

    def emit(self, event: str, details: Mapping[str, object]) -> None:
        payload = {"event": event, **dict(details)}
        if self.json_mode:
            print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), file=sys.stderr)
        else:
            print(f"[{event}] {json.dumps(dict(details), ensure_ascii=False)}", file=sys.stderr)

    def final(self, payload: Mapping[str, object]) -> None:
        if self.json_mode:
            print(json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")))
        else:
            print(json.dumps(dict(payload), ensure_ascii=False, indent=2))


class Application:
    def __init__(
        self,
        paths: AppPaths,
        registry: Registry,
        cleanup: CleanupService,
        export_service: ExportService,
    ) -> None:
        self.paths = paths
        self.registry = registry
        self.cleanup = cleanup
        self.export_service = export_service

    @classmethod
    def default(cls, read_only: bool = False) -> "Application":
        paths = AppPaths.from_home(Path(os.environ.get("WEREAD_EXPORT_HOME", Path.home())))
        registry = Registry(paths.state_db)
        if not read_only:
            paths.create_private_dirs()
            registry.initialize()
            AtomicPublisher(registry).recover_transactions()
        cleanup = CleanupService(registry, paths)
        browser_factory = BrowserFactory(paths.jobs)
        export_service = ExportService(
            paths,
            browser_factory,
            AuthStore(paths.auth_state),
            AsyncFileLock(paths.locks / "auth.lock"),
            WeReadReader(),
            registry,
            cleanup,
        )
        return cls(paths, registry, cleanup, export_service)

    async def dispatch(self, args: argparse.Namespace, sink: EventSink) -> Mapping[str, object]:
        if args.command == "doctor":
            return await self._dispatch(args, sink)
        if args.command != "cleanup":
            self.cleanup.run()
        try:
            return await self._dispatch(args, sink)
        finally:
            if args.command != "cleanup":
                self.cleanup.run()

    async def _dispatch(self, args: argparse.Namespace, sink: EventSink) -> Mapping[str, object]:
        if args.command == "export":
            request = ExportRequest(
                parse_book_ref(args.book),
                Path(args.output).expanduser() if args.output else None,
                args.force,
            )
            result = await self.export_service.export_book(request, progress=sink.emit)
            return result.to_json()
        if args.command == "list":
            return {
                "status": "ok",
                "results": [_result_json(record) for record in self.registry.list_results()],
            }
        if args.command == "status":
            job = self.registry.get_job(args.job_id)
            return {
                "status": "ok",
                "job": {
                    "job_id": job.job_id,
                    "book_id": job.book_id,
                    "state": job.status.value,
                    "created_at": job.created_at.isoformat(),
                    "updated_at": job.updated_at.isoformat(),
                    "error_reason": job.error_reason,
                },
            }
        if args.command == "keep":
            self.cleanup.keep(args.result_id)
            return {"status": "ok", "result_id": args.result_id, "kept": True}
        if args.command == "unkeep":
            self.cleanup.unkeep(args.result_id)
            return {"status": "ok", "result_id": args.result_id, "kept": False}
        if args.command == "delete":
            if not args.yes:
                raise ExportError(
                    "delete_confirmation_required",
                    "删除托管结果需要用户明确同意并传入 --yes",
                    ExitCode.ACTION_REQUIRED,
                    {"result_ids": args.result_ids},
                )
            return {
                "status": "ok",
                "cleanup": _cleanup_json(self.cleanup.delete_confirmed(args.result_ids)),
            }
        if args.command == "cleanup":
            return {
                "status": "ok",
                "dry_run": args.dry_run,
                "cleanup": _cleanup_json(self.cleanup.run(dry_run=args.dry_run)),
            }
        if args.command == "purge-all":
            if not args.yes:
                raise ExportError(
                    "delete_confirmation_required",
                    "彻底清理需要传入 --yes",
                    ExitCode.ACTION_REQUIRED,
                )
            records = self.registry.list_results()
            report = self.cleanup.delete_confirmed([record.result_id for record in records])
            if report.refused:
                raise ExportError(
                    "purge_refused",
                    "部分托管结果身份校验失败，已停止彻底卸载",
                    ExitCode.ACTION_REQUIRED,
                    {"refused": report.refused},
                )
            AuthStore(self.paths.auth_state).reset()
            return {"status": "ok", "cleanup": _cleanup_json(report), "auth_removed": True}
        if args.command == "auth":
            return await self._auth(args, sink)
        if args.command == "doctor":
            installation = locate_chrome()
            return {
                "status": "ok",
                "app_version": __version__,
                "release_channel": __release_channel__,
                "macos": platform.mac_ver()[0],
                "python": platform.python_version(),
                "browser": {
                    "channel": "chrome",
                    "executable": str(installation.executable),
                    "downloaded_chromium": False,
                },
                "paths": {
                    "state": str(self.paths.app_support),
                    "managed_output": str(self.paths.managed_output),
                },
            }
        raise ExportError("invalid_command", "未知命令", ExitCode.INVALID_INPUT)

    async def _auth(self, args: argparse.Namespace, sink: EventSink) -> Mapping[str, object]:
        store = AuthStore(self.paths.auth_state)
        if args.auth_command == "status":
            return {"status": "ok", "auth": store.status()}
        if args.auth_command == "reset":
            store.reset()
            return {"status": "ok", "auth": {"exists": False}}
        state = await ensure_auth(
            self.export_service.browser_factory,
            store,
            AsyncFileLock(self.paths.locks / "auth.lock"),
            progress=sink.emit,
        )
        return {
            "status": "ok",
            "auth": {"exists": True, "cookies": len(state.get("cookies", []))},
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="weread-book-export")
    parser.add_argument("--json", action="store_true", help="输出 Agent 可解析的 JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export = subparsers.add_parser("export", help="导出一本微信读书")
    export.add_argument("book", help="微信读书阅读器 URL 或 Book ID")
    export.add_argument("--output", help="自定义输出父目录；书籍子目录不受自动清理管理")
    export.add_argument("--force", action="store_true", help="经用户确认后覆盖现有目标")
    _json_flag(export)

    status = subparsers.add_parser("status", help="查询任务状态")
    status.add_argument("job_id")
    _json_flag(status)
    list_parser = subparsers.add_parser("list", help="列出托管结果")
    _json_flag(list_parser)
    keep = subparsers.add_parser("keep", help="永久保留托管结果")
    keep.add_argument("result_id")
    _json_flag(keep)
    unkeep = subparsers.add_parser("unkeep", help="恢复默认保留期")
    unkeep.add_argument("result_id")
    _json_flag(unkeep)
    delete = subparsers.add_parser("delete", help="删除已确认的托管结果")
    delete.add_argument("result_ids", nargs="+")
    delete.add_argument("--yes", action="store_true")
    _json_flag(delete)
    cleanup = subparsers.add_parser("cleanup", help="执行安全清理")
    cleanup.add_argument("--dry-run", action="store_true")
    _json_flag(cleanup)
    purge = subparsers.add_parser("purge-all", help=argparse.SUPPRESS)
    purge.add_argument("--yes", action="store_true")
    _json_flag(purge)

    auth = subparsers.add_parser("auth", help="管理隔离登录状态")
    auth_subparsers = auth.add_subparsers(dest="auth_command", required=True)
    for name in ("status", "login", "reset"):
        auth_action = auth_subparsers.add_parser(name)
        _json_flag(auth_action)

    doctor = subparsers.add_parser("doctor", help="检查 Chrome 和私有运行时")
    _json_flag(doctor)
    return parser


def _json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS
    )


def main(
    argv: Sequence[str] | None = None,
    application: Application | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    sink = EventSink(json_mode=args.json)
    try:
        app = application or Application.default(read_only=args.command == "doctor")
        result = asyncio.run(app.dispatch(args, sink))
        sink.final(result)
        return int(ExitCode.OK)
    except ExportError as error:
        sink.final(
            {
                "status": "action_required"
                if error.exit_code is ExitCode.ACTION_REQUIRED
                else "failed",
                "reason": error.reason,
                "message": error.message,
                **dict(error.details),
            }
        )
        return int(error.exit_code)
    except KeyboardInterrupt:
        sink.final({"status": "failed", "reason": "interrupted", "message": "任务已中断"})
        return int(ExitCode.INTERRUPTED)
    except Exception as error:
        sink.final(
            {
                "status": "failed",
                "reason": "internal_error",
                "message": "程序发生未分类错误",
                "error_type": type(error).__name__,
            }
        )
        return int(ExitCode.CAPTURE_FAILED)


def _result_json(record) -> dict[str, object]:
    return {
        "result_id": record.result_id,
        "book_id": record.book_id,
        "title": record.title,
        "output_dir": str(record.path),
        "created_at": record.created_at.isoformat(),
        "retained_until": record.retained_until.isoformat(),
        "size_bytes": record.size_bytes,
        "kept": record.kept,
        "modified": record.modified,
    }


def _cleanup_json(report: CleanupReport) -> dict[str, object]:
    return {
        "deleted": report.deleted,
        "modified": report.modified,
        "kept": report.kept,
        "refused": report.refused,
        "runtime_deleted": report.runtime_deleted,
    }
