"""输入解析和用户目录约束。"""

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .errors import ExitCode, ExportError
from .models import BookRef

BOOK_ID_RE = re.compile(r"^[A-Za-z0-9]{6,128}$")
READER_PREFIX = "/web/reader/"


@dataclass(frozen=True, slots=True)
class AppPaths:
    home: Path
    app_support: Path
    cache: Path
    logs: Path
    jobs: Path
    locks: Path
    auth: Path
    auth_state: Path
    state_db: Path
    managed_output: Path

    @classmethod
    def from_home(cls, home: Path) -> "AppPaths":
        clean_home = home.expanduser().resolve()
        support = clean_home / "Library/Application Support/weread-book-export"
        cache = clean_home / "Library/Caches/weread-book-export"
        auth = support / "auth"
        return cls(
            home=clean_home,
            app_support=support,
            cache=cache,
            logs=cache / "logs",
            jobs=cache / "jobs",
            locks=support / "locks",
            auth=auth,
            auth_state=auth / "storage-state.json",
            state_db=support / "state.sqlite3",
            managed_output=clean_home / "Downloads/WeRead Exports",
        )

    def create_private_dirs(self) -> None:
        for path in (self.app_support, self.cache, self.logs, self.jobs, self.locks, self.auth):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)


def parse_book_ref(value: str) -> BookRef:
    candidate = value.strip()
    if "://" in candidate:
        parsed = urlsplit(candidate)
        if parsed.scheme != "https" or parsed.hostname != "weread.qq.com":
            raise _invalid_input()
        if not parsed.path.startswith(READER_PREFIX):
            raise _invalid_input()
        book_id = parsed.path.removeprefix(READER_PREFIX).strip("/")
        if "/" in book_id:
            raise _invalid_input()
    else:
        book_id = candidate
    if not BOOK_ID_RE.fullmatch(book_id):
        raise _invalid_input()
    return BookRef(book_id=book_id, canonical_url=f"https://weread.qq.com/web/reader/{book_id}")


def safe_book_dir_name(title: str, book_id: str) -> str:
    if not BOOK_ID_RE.fullmatch(book_id):
        raise _invalid_input()
    normalized = unicodedata.normalize("NFC", title)
    leading_dot = normalized.startswith(".")
    reserved = '/:\\"<>|?*'
    cleaned = "".join(
        "_" if char in reserved or unicodedata.category(char) == "Cc" else char
        for char in normalized
    )
    cleaned = re.sub(r"_+", "_", cleaned).strip(" ._")
    if not cleaned:
        cleaned = "未命名"
    if leading_dot:
        cleaned = "_" + cleaned
    suffix = f"-{book_id}"
    return cleaned[: 120 - len(suffix)].rstrip(" .") + suffix


def _invalid_input() -> ExportError:
    return ExportError(
        reason="invalid_input",
        message="请输入有效的微信读书阅读器 URL 或 Book ID",
        exit_code=ExitCode.INVALID_INPUT,
    )
