"""稳定的错误原因和进程退出码。"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    INVALID_INPUT = 2
    ACTION_REQUIRED = 10
    AUTH_FAILED = 20
    ACCESS_DENIED = 21
    CAPTURE_FAILED = 30
    VALIDATION_FAILED = 31
    BOOTSTRAP_FAILED = 40
    INSUFFICIENT_DISK_SPACE = 50
    INTERRUPTED = 130


@dataclass(slots=True)
class ExportError(Exception):
    reason: str
    message: str
    exit_code: ExitCode
    details: Mapping[str, object] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message
