"""系统 Chrome 定位和隔离的临时浏览器上下文。"""

import os
import shutil
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import BrowserContext, async_playwright

from .errors import ExitCode, ExportError

CHROME_RELATIVE = Path("Google Chrome.app/Contents/MacOS/Google Chrome")
LOGIN_TRIGGER_SELECTOR = "button.navBar_link_Login"
LOGIN_QRCODE_SELECTOR = ".login_dialog_qrcode_img_main, [class*='login_dialog_qrcode']"


@dataclass(frozen=True, slots=True)
class ChromeInstallation:
    executable: Path
    channel: str = "chrome"


def locate_chrome(search_roots: tuple[Path, ...] | None = None) -> ChromeInstallation:
    roots = search_roots or (Path("/Applications"), Path.home() / "Applications")
    for root in roots:
        candidates = (
            (root, root / CHROME_RELATIVE)
            if root.name == "Google Chrome"
            else (root / CHROME_RELATIVE,)
        )
        for candidate in candidates:
            if candidate.is_file():
                return ChromeInstallation(candidate.resolve())
    raise ExportError(
        reason="chrome_not_found",
        message="未找到 Google Chrome，请先安装 Chrome stable",
        exit_code=ExitCode.BOOTSTRAP_FAILED,
    )


class BrowserFactory:
    channel = "chrome"
    uses_persistent_context = False

    def __init__(self, temporary_root: Path | None = None) -> None:
        self.temporary_root = temporary_root

    @asynccontextmanager
    async def open(
        self,
        job_dir: Path,
        headless: bool,
        storage_state: Mapping[str, object] | None,
    ) -> AsyncIterator[BrowserContext]:
        installation = locate_chrome()
        chrome_tmp = job_dir / "chrome"
        chrome_tmp.mkdir(parents=True, exist_ok=True, mode=0o700)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                executable_path=str(installation.executable),
                headless=headless,
                env={**os.environ, "TMPDIR": str(chrome_tmp)},
                args=["--disable-blink-features=AutomationControlled", "--no-first-run"],
            )
            context = await browser.new_context(
                storage_state=dict(storage_state) if storage_state else None,
                viewport={"width": 1200, "height": 900},
            )
            try:
                yield context
            finally:
                await context.close()
                await browser.close()

    async def is_authenticated(self, state: Mapping[str, object]) -> bool:
        if not state.get("cookies") and not state.get("origins"):
            return False
        with self._temporary_directory("auth-check") as check_dir:
            async with self.open(check_dir, headless=True, storage_state=state) as context:
                page = await context.new_page()
                try:
                    await page.goto(
                        "https://weread.qq.com/web/shelf",
                        wait_until="domcontentloaded",
                        timeout=30_000,
                    )
                    await page.wait_for_timeout(1_000)
                    return "login" not in page.url.lower() and not await _login_prompt_visible(page)
                finally:
                    await page.close()

    async def capture_auth(self, progress=None) -> Mapping[str, object]:
        with self._temporary_directory("auth-window") as auth_dir:
            async with self.open(auth_dir, headless=False, storage_state=None) as context:
                page = await context.new_page()
                await page.goto(
                    "https://weread.qq.com/web/shelf",
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                await _open_login_prompt(page)
                if progress is not None:
                    emitted = progress("auth_scan_required", {"timeout_seconds": 600})
                    if emitted is not None:
                        await emitted
                for _ in range(600):
                    if page.is_closed():
                        break
                    if "login" not in page.url.lower() and not await _login_prompt_visible(page):
                        state = await context.storage_state(indexed_db=True)
                        await page.close()
                        return state
                    await page.wait_for_timeout(1_000)
        raise ExportError(
            reason="auth_failed",
            message="扫码登录超时或登录窗口已关闭",
            exit_code=ExitCode.AUTH_FAILED,
        )

    @contextmanager
    def _temporary_directory(self, purpose: str):
        root = self.temporary_root or _default_temporary_root()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = root / f".{purpose}-{uuid.uuid4().hex}"
        temporary.mkdir(mode=0o700)
        try:
            yield temporary
        finally:
            if temporary.exists():
                if temporary.is_symlink():
                    temporary.unlink()
                else:
                    shutil.rmtree(temporary)


def _default_temporary_root() -> Path:
    home = Path(os.environ.get("WEREAD_EXPORT_HOME", Path.home()))
    return home / "Library/Caches/weread-book-export/jobs"


async def _login_prompt_visible(page) -> bool:
    selectors = (
        LOGIN_TRIGGER_SELECTOR,
        ".login_dialog_qrcode_img_main",
        "[class*='login_dialog_qrcode']",
        "[class*='login_qrcode']",
        "[class*='loginPanel']",
        "text=扫码登录",
    )
    for selector in selectors:
        try:
            if await page.locator(selector).first.is_visible(timeout=300):
                return True
        except Exception:
            continue
    return False


async def _open_login_prompt(page) -> bool:
    trigger = page.locator(LOGIN_TRIGGER_SELECTOR).first
    try:
        if not await trigger.is_visible(timeout=500):
            return False
        await trigger.click(timeout=2_000)
        await page.locator(LOGIN_QRCODE_SELECTOR).first.wait_for(state="visible", timeout=3_000)
        return True
    except Exception:
        return False
