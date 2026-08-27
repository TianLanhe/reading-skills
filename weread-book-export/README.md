# weread-book-export

面向本地 AI Agent 的微信读书整书导出 Skill。用户提供微信读书阅读器 URL 或 Book ID 后，程序使用独立的系统 Chrome context 抓取正文和图片，经过完整性校验后输出 Markdown，并返回结果目录。

当前版本：`0.1.0`，发布通道：`stable`。除自动测试和本地阅读器夹具外，已使用正文为主、图文混排和图片密集三类真实授权书籍完成强校验验收；微信读书网页结构变化后仍应重新验收。

## 系统要求

- macOS 13 或更高版本，arm64 或 x86_64。
- 已安装 Google Chrome stable。
- 能访问微信读书和 GitHub Release/PyPI 下载源。

不要求预装 Python、uv、Playwright、Chromium、Homebrew 或 Codex Desktop。首次执行会在工具私有缓存中安装固定版本的 uv 0.12.1、Python 3.13.14、Playwright 1.62.0 和 Pillow 12.3.0。设置了 `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`，不会下载 Chromium，也不会修改系统 Python 或 PATH。

## 安装

解压分发包后执行：

```bash
./scripts/install.sh
```

安装器会先在临时版本目录完成 `doctor` 自检，再原子切换到新版本。默认始终注册 `~/.agents/skills/weread-book-export`；如果已存在 Claude Code、Cursor、Qwen、Kimi 或 Trae 的 skills 目录，也会注册对应符号链接。

其他 Agent 可指定自己的 skills 目录：

```bash
./scripts/install.sh --target /path/to/agent/skills
```

DeepSeek、GLM、MiniMax 等没有稳定通用 Skill 目录的环境，可使用 `--target`，或直接让 Agent 调用安装目录中的 `bin/weread-book-export`。核心功能只依赖标准 shell 命令和 JSON，不依赖 Codex 专有 API。

## 使用

```bash
bin/weread-book-export doctor --json
bin/weread-book-export export 'https://weread.qq.com/web/reader/<BookID>' --json
bin/weread-book-export export '<BookID>' --output '/自定义/父目录' --json
bin/weread-book-export list --json
```

首次导出会打开一个独立 Chrome 登录窗口供扫码。认证快照只属于本工具，不读取日常 Chrome profile；后续任务复用快照，通常无需再次扫码。认证过期时，多任务共享一个认证锁，因此只会出现一个扫码窗口。

不同 Book ID 可以同时运行，不设人为并发上限；同一 Book ID 的任务共享文件锁和有效结果，等待者返回 `reused`。

## 输出与正确性

默认目录：

```text
~/Downloads/WeRead Exports/<书名>-<BookID>/
├── <书名>.md
├── images/
├── manifest.json
└── .weread-export-managed
```

程序不会按相邻文本内容去重，也不会按全局图片 URL 去重；相同句子和同一图片在不同位置重复出现时都会保留。双页按 canvas 和视觉坐标排序，章节标题由稳定页面状态和目录共同辅助判断。Markdown 图片始终使用结果目录内的 `images/...` 相对路径。

发布前必须通过到达全书末尾、非空章节、正文字符守恒、页面指纹唯一、章节顺序、图片 occurrence 数量、图片解码、文件哈希和链接检查。任一失败返回 `validation_failed`，不会把部分结果描述为成功。

## 清理策略

- 成功任务：日志、Chrome 临时目录、staging 和 backup 立即删除。
- 失败或中断：脱敏恢复资料最多保留 24 小时。
- 默认托管结果：保留 7 天，最多 30 本；达到 30 本只返回候选并询问，不自动淘汰。
- `.keep` 或 `keep <result_id>` 可长期保留。
- 自定义 `--output` 是父目录；程序在其中创建书籍子目录，且永不加入自动清理。
- 自动删除前必须同时匹配 SQLite 记录、托管 marker、manifest、路径、成员和 SHA-256。用户修改或出现 symlink 时拒绝删除。

## 卸载

标准卸载保留认证、SQLite 状态库和托管导出：

```bash
./scripts/uninstall.sh --yes
```

彻底卸载会先调用 CLI 对每个托管结果执行同样的安全身份校验。任何结果被修改或身份不一致都会停止：

```bash
./scripts/uninstall.sh --purge-all --yes
```

## Agent 兼容性

| Agent 类型 | 使用方式 |
|---|---|
| 支持 Agent Skills 标准 | 安装 `SKILL.md` 后按触发描述自动调用 |
| Claude Code、Cursor、Qwen、Kimi、Trae | 安装器检测已有 skills 目录并注册 |
| DeepSeek、GLM、MiniMax 或自定义 Agent | `--target` 注册，或直接调用 CLI |
| 只支持 shell 的 Agent | 读取 stdout 最终 JSON 和 stderr JSONL 进度 |

详见 [CLI 契约](references/cli-contract.md)和[故障排查](references/troubleshooting.md)。

## 合规边界

只导出用户有权阅读的内容，供个人使用。微信读书网页结构、账号权限或服务条款可能变化；本工具不会绕过账号授权、付费权限或“仅 App 阅读”限制。用户应自行确认使用和分发导出内容的合法性。

上游来源和授权前提见 [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES)。

## 开发验证

```bash
uv sync --all-groups --python 3.13.14
uv run pytest -m 'not live' -q
uv run ruff check .
bash -n bin/weread-book-export scripts/*.sh
```

自动测试不会连接真实微信读书账号。真实验收必须由用户扫码，并提供其有权阅读的纯文字、图文混排和图片密集书籍。
