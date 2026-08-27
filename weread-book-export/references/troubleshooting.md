# 故障排查

先执行：

```bash
"$SKILL_DIR/bin/weread-book-export" doctor --json
```

不要把 Cookie、storage-state.json、完整正文或含正文的失败目录贴到聊天中。只提供错误原因、计数、版本、非正文哈希和必要的脱敏日志。

## bootstrap_failed / chrome_not_found

- 本工具只支持 macOS 13 及以上的 arm64、x86_64，并假设已经安装 Google Chrome stable。
- Chrome 默认路径是 `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` 或用户 Applications 目录。
- 不要运行 `playwright install chromium`。程序只使用系统 Chrome，不需要 Chrome for Testing。
- uv、Python、venv 和 Python 包都在 `~/Library/Caches/weread-book-export/`；不使用 sudo、Homebrew 或系统 Python。
- uv 下载会先校验 SHA-256。校验失败时停止，不应跳过。

## auth_failed / auth_scan_required

- `auth_scan_required` 是等待状态。保持命令运行，让用户在独立窗口完成扫码。
- 十分钟未完成、窗口被关闭或网络失败会返回 `auth_failed`。
- 可执行 `auth reset --json` 后再执行 `auth login --json`。这只删除本工具的隔离认证快照，不影响日常 Chrome。
- 多个任务同时发现过期时只会出现一个登录窗口。

## access_denied

网页出现“去 App 阅读”“无权阅读”或“试读结束”时，程序不会把试读内容冒充整书。确认账号确实有权在微信读书网页版阅读该书；若服务端只允许 App 阅读，本 Skill 无法绕过。

## capture_failed

- 阅读器连续三次翻页无变化，但当前章节不是目录末章时会失败，避免提前截断。
- 浏览器异常最多恢复两次。失败资料保留 24 小时后自动清理。
- 可重试一次；重复失败时收集 Book ID、失败阶段、已抓页数和选择器状态，不要收集正文。
- 微信读书页面结构变化时，应先更新集中选择器和本地阅读器夹具，再重跑完整测试。

## validation_failed

任一条件失败都不会发布成功：到达末尾、非空章节、字符守恒、页面指纹唯一、章节顺序、图片 occurrence 数量、图片解码、文件哈希、Markdown 图片链接。

不要通过删除检查或改成“警告”来绕过。保留脱敏失败证据，定位最早失真的页面、章节或图片步骤，然后重新导出。

## managed_output_limit

默认托管结果最多 30 本。向用户展示程序返回的候选；只有用户明确选择后，才执行 `delete <result_id> --yes --json`。用户拒绝删除时停止新导出，不做自动淘汰。

## 清理拒绝

以下情况是保护行为：正文或图片被修改、manifest/marker 不一致、出现额外文件、任一路径为符号链接、目录被移出托管根目录。程序会保留内容并返回原因。用户若要删除，应自行核对和处理；Agent 不得绕过身份检查递归删除。
