"""SQLite WAL 状态、任务和托管结果登记。"""

import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .errors import ExitCode, ExportError
from .models import ExportResult, JobRecord, JobStatus, PublishTransaction, ResultRecord

ACTIVE_STATUSES = (
    JobStatus.CREATED,
    JobStatus.BOOTSTRAPPING,
    JobStatus.WAITING_FOR_AUTH,
    JobStatus.CAPTURING,
    JobStatus.DOWNLOADING_IMAGES,
    JobStatus.VALIDATING,
    JobStatus.PUBLISHING,
)

ALLOWED_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.CREATED: {JobStatus.BOOTSTRAPPING, JobStatus.INTERRUPTED},
    JobStatus.BOOTSTRAPPING: {
        JobStatus.WAITING_FOR_AUTH,
        JobStatus.CAPTURING,
        JobStatus.FAILED,
        JobStatus.INTERRUPTED,
    },
    JobStatus.WAITING_FOR_AUTH: {
        JobStatus.CAPTURING,
        JobStatus.FAILED,
        JobStatus.INTERRUPTED,
    },
    JobStatus.CAPTURING: {
        JobStatus.DOWNLOADING_IMAGES,
        JobStatus.FAILED,
        JobStatus.INTERRUPTED,
    },
    JobStatus.DOWNLOADING_IMAGES: {
        JobStatus.VALIDATING,
        JobStatus.FAILED,
        JobStatus.INTERRUPTED,
    },
    JobStatus.VALIDATING: {
        JobStatus.PUBLISHING,
        JobStatus.VALIDATION_FAILED,
        JobStatus.FAILED,
        JobStatus.INTERRUPTED,
    },
    JobStatus.PUBLISHING: {
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.INTERRUPTED,
    },
}


class Registry:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    book_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error_reason TEXT,
                    owner_pid INTEGER,
                    managed INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS jobs_book_id_idx ON jobs(book_id, created_at);
                CREATE TABLE IF NOT EXISTS results (
                    result_id TEXT PRIMARY KEY,
                    book_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    output_dir TEXT NOT NULL,
                    markdown_path TEXT NOT NULL,
                    manifest_path TEXT NOT NULL,
                    chapters INTEGER NOT NULL,
                    characters INTEGER NOT NULL,
                    images INTEGER NOT NULL,
                    manifest_sha256 TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    retained_until TEXT,
                    valid INTEGER NOT NULL DEFAULT 1,
                    kept INTEGER NOT NULL DEFAULT 0,
                    modified INTEGER NOT NULL DEFAULT 0,
                    size_bytes INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS results_book_id_idx ON results(book_id, created_at);
                CREATE TABLE IF NOT EXISTS publish_transactions (
                    job_id TEXT PRIMARY KEY,
                    target TEXT NOT NULL,
                    staging TEXT NOT NULL,
                    backup TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(1, ?)",
                (datetime.now(UTC).isoformat(),),
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "owner_pid" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN owner_pid INTEGER")
            if "managed" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN managed INTEGER NOT NULL DEFAULT 0")
            placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
            active_rows = connection.execute(
                f"SELECT job_id, owner_pid FROM jobs WHERE status IN ({placeholders})",
                tuple(status.value for status in ACTIVE_STATUSES),
            ).fetchall()
            for row in active_rows:
                if _process_is_alive(row["owner_pid"]):
                    continue
                connection.execute(
                    "UPDATE jobs SET status = ?, updated_at = ? WHERE job_id = ?",
                    (
                        JobStatus.INTERRUPTED.value,
                        datetime.now(UTC).isoformat(),
                        row["job_id"],
                    ),
                )

    def scalar(self, query: str, parameters: tuple[object, ...] = ()):
        with self.connect() as connection:
            row = connection.execute(query, parameters).fetchone()
            return row[0] if row else None

    def create_job(
        self,
        book_id: str,
        now: datetime | None = None,
        managed: bool = False,
        maximum_managed_books: int = 30,
    ) -> JobRecord:
        timestamp = now or datetime.now(UTC)
        job = JobRecord(uuid.uuid4().hex, book_id, JobStatus.CREATED, timestamp, timestamp)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if managed:
                existing_ids = {
                    row["book_id"]
                    for row in connection.execute(
                        "SELECT DISTINCT book_id FROM results WHERE valid = 1"
                    ).fetchall()
                }
                placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
                existing_ids.update(
                    row["book_id"]
                    for row in connection.execute(
                        f"""SELECT DISTINCT book_id FROM jobs
                            WHERE managed = 1 AND status IN ({placeholders})""",
                        tuple(status.value for status in ACTIVE_STATUSES),
                    ).fetchall()
                )
                if book_id not in existing_ids and len(existing_ids) >= maximum_managed_books:
                    rows = connection.execute(
                        """SELECT result_id, book_id, title, created_at, size_bytes,
                                  kept, modified
                           FROM results WHERE valid = 1 ORDER BY created_at"""
                    ).fetchall()
                    raise ExportError(
                        "managed_output_limit",
                        "托管导出结果及进行中任务已达到 30 本，请选择是否删除",
                        ExitCode.ACTION_REQUIRED,
                        {
                            "maximum": maximum_managed_books,
                            "candidates": [dict(row) for row in rows],
                            "active_book_ids": sorted(
                                existing_ids - {row["book_id"] for row in rows}
                            ),
                        },
                    )
            connection.execute(
                """INSERT INTO jobs(
                       job_id, book_id, status, created_at, updated_at, owner_pid, managed
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    job.job_id,
                    job.book_id,
                    job.status.value,
                    job.created_at.isoformat(),
                    job.updated_at.isoformat(),
                    os.getpid(),
                    int(managed),
                ),
            )
        return job

    def get_job(self, job_id: str) -> JobRecord:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _job_from_row(row)

    def transition_job(
        self,
        job_id: str,
        status: JobStatus,
        error_reason: str | None = None,
        now: datetime | None = None,
    ) -> JobRecord:
        current = self.get_job(job_id)
        if status not in ALLOWED_TRANSITIONS.get(current.status, set()):
            raise ValueError(f"非法任务状态转移：{current.status.value} -> {status.value}")
        timestamp = now or datetime.now(UTC)
        with self.connect() as connection:
            connection.execute(
                "UPDATE jobs SET status = ?, updated_at = ?, error_reason = ? WHERE job_id = ?",
                (status.value, timestamp.isoformat(), error_reason, job_id),
            )
        return self.get_job(job_id)

    def register_export_result(
        self,
        result: ExportResult,
        manifest_sha256: str = "",
        created_at: datetime | None = None,
        result_id: str | None = None,
    ) -> str:
        result_id = result_id or uuid.uuid4().hex
        timestamp = created_at or datetime.now(UTC)
        size_bytes = sum(
            path.stat().st_size
            for path in result.output_dir.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO results(
                    result_id, book_id, title, output_dir, markdown_path, manifest_path,
                    chapters, characters, images, manifest_sha256, created_at,
                    retained_until, valid, kept, modified, size_bytes
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,0,0,?)
                """,
                (
                    result_id,
                    result.book_id,
                    result.title,
                    str(result.output_dir),
                    str(result.markdown_path),
                    str(result.manifest_path),
                    result.chapters,
                    result.characters,
                    result.images,
                    manifest_sha256,
                    timestamp.isoformat(),
                    result.retained_until.isoformat() if result.retained_until else None,
                    size_bytes,
                ),
            )
        return result_id

    def latest_result_record(self, book_id: str) -> ResultRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT * FROM results
                   WHERE book_id = ? AND valid = 1
                   ORDER BY created_at DESC LIMIT 1""",
                (book_id,),
            ).fetchone()
        return _result_record_from_row(row) if row is not None else None

    def result_exists(self, result_id: str) -> bool:
        return bool(
            self.scalar("SELECT EXISTS(SELECT 1 FROM results WHERE result_id = ?)", (result_id,))
        )

    def job_owned_by_live_process(self, job_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status, owner_pid FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return bool(
            row is not None
            and JobStatus(row["status"]) in ACTIVE_STATUSES
            and _process_is_alive(row["owner_pid"])
        )

    def latest_export_result(self, book_id: str) -> ExportResult | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT * FROM results
                   WHERE book_id = ? AND valid = 1 AND modified = 0
                   ORDER BY created_at DESC LIMIT 1""",
                (book_id,),
            ).fetchone()
        if row is None:
            return None
        retained = datetime.fromisoformat(row["retained_until"]) if row["retained_until"] else None
        return ExportResult(
            "completed",
            row["book_id"],
            row["title"],
            Path(row["output_dir"]),
            Path(row["markdown_path"]),
            Path(row["manifest_path"]),
            row["chapters"],
            row["characters"],
            row["images"],
            retained,
        )

    def invalidate_latest(self, book_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE results SET valid = 0 WHERE result_id = (
                     SELECT result_id FROM results WHERE book_id = ? AND valid = 1
                     ORDER BY created_at DESC LIMIT 1
                   )""",
                (book_id,),
            )

    def list_results(self) -> tuple[ResultRecord, ...]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM results WHERE valid = 1 ORDER BY created_at ASC"
            ).fetchall()
        return tuple(_result_record_from_row(row) for row in rows)

    def mark_modified(self, result_id: str) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE results SET modified = 1 WHERE result_id = ?", (result_id,))

    def set_keep(self, result_id: str, kept: bool) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE results SET kept = ? WHERE result_id = ?", (int(kept), result_id)
            )

    def invalidate_result(self, result_id: str) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE results SET valid = 0 WHERE result_id = ?", (result_id,))

    def invalidate_other_results(self, book_id: str, current_result_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE results SET valid = 0
                   WHERE book_id = ? AND result_id != ? AND valid = 1""",
                (book_id, current_result_id),
            )

    def upsert_publish_transaction(self, transaction: PublishTransaction) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO publish_transactions(job_id, target, staging, backup, phase, updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(job_id) DO UPDATE SET
                    target=excluded.target, staging=excluded.staging, backup=excluded.backup,
                    phase=excluded.phase, updated_at=excluded.updated_at
                """,
                (
                    transaction.job_id,
                    str(transaction.target),
                    str(transaction.staging),
                    str(transaction.backup),
                    transaction.phase,
                    transaction.updated_at.isoformat(),
                ),
            )

    def list_publish_transactions(self) -> tuple[PublishTransaction, ...]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM publish_transactions ORDER BY updated_at"
            ).fetchall()
        return tuple(
            PublishTransaction(
                row["job_id"],
                Path(row["target"]),
                Path(row["staging"]),
                Path(row["backup"]),
                row["phase"],
                datetime.fromisoformat(row["updated_at"]),
            )
            for row in rows
        )

    def delete_publish_transaction(self, job_id: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM publish_transactions WHERE job_id = ?", (job_id,))


def _job_from_row(row: sqlite3.Row) -> JobRecord:
    return JobRecord(
        row["job_id"],
        row["book_id"],
        JobStatus(row["status"]),
        datetime.fromisoformat(row["created_at"]),
        datetime.fromisoformat(row["updated_at"]),
        row["error_reason"],
    )


def _result_record_from_row(row: sqlite3.Row) -> ResultRecord:
    output_dir = Path(row["output_dir"])
    retained = (
        datetime.fromisoformat(row["retained_until"])
        if row["retained_until"]
        else datetime.max.replace(tzinfo=UTC)
    )
    return ResultRecord(
        result_id=row["result_id"],
        book_id=row["book_id"],
        title=row["title"],
        path=output_dir,
        managed_root=output_dir.parent,
        manifest_sha256=row["manifest_sha256"],
        created_at=datetime.fromisoformat(row["created_at"]),
        retained_until=retained,
        size_bytes=row["size_bytes"],
        kept=bool(row["kept"]),
        modified=bool(row["modified"]),
    )


def _process_is_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
