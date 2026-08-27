---
name: weread-book-export
description: 当用户提供微信读书（WeRead）阅读器 URL 或 Book ID，并要求下载、导出或保存其有权阅读的整本书时使用；调用本机隔离 Chrome 导出经校验的 Markdown 和本地图片，并返回结果目录。
---

# 微信读书整书导出

将用户提供的微信读书阅读器 URL 或 Book ID 交给本 Skill 自带的命令行程序。业务逻辑、依赖安装、浏览器隔离、并发、恢复、完整性校验和清理由程序负责；Agent 只负责传参、持续等待、请求必要确认并解释结构化结果。

## 执行流程

1. 确认用户要导出自己有权阅读的书，并从消息中提取 `https://weread.qq.com/web/reader/<BookID>` 或 Book ID。没有输入时只询问这一项。
2. 以本文件所在目录为 `SKILL_DIR`，执行：

   ```bash
   "$SKILL_DIR/bin/weread-book-export" export '<URL或BookID>' --json
   ```

3. 保持命令运行并读取 stderr 的 JSONL 进度。收到 `auth_scan_required` 时，告诉用户在新开的独立 Chrome 登录窗口扫码；不要终止命令，也不要操作用户日常 Chrome 窗口。
4. 最终 stdout 只有一个 JSON 对象。只有 `status` 为 `completed` 或 `reused` 且 `validation.valid` 为 `true` 时，才向用户报告成功，并返回其中的绝对 `output_dir`、章节数、字符数和图片数。
5. `status=action_required` 时展示原因和候选项并询问用户。没有用户明确同意，不得执行 `delete ... --yes`、`purge-all --yes` 或带 `--force` 的覆盖导出。
6. `validation_failed`、`capture_failed`、`access_denied`、`auth_failed`、`bootstrap_failed` 或 `internal_error` 都不得描述为成功。保留错误原因，按排障资料处理。

## 自治规则

- 直接调用入口脚本；它会检测 macOS、系统 Chrome、CPU 架构和私有运行时，缺少时自动安装固定版本的 uv、Python 和 Python 包。不得另行运行 `pip install`、`brew install` 或 `playwright install`。
- 不读取或复用用户日常 Chrome profile。首次或认证失效时只打开一个隔离登录窗口；认证成功后复用独立快照，后续导出通常不再扫码。
- 不同 Book ID 可启动多个独立命令并发导出，不设全局上限。同一 Book ID 的并发请求由文件锁合并，只抓取一次并共享结果。
- 默认结果受托管：保留七天，最多三十本。达到上限时只展示程序返回的候选，不替用户决定删除。
- 用户要求长期保留时，先从 `list --json` 取得 `result_id`，再执行 `keep <result_id> --json`。恢复默认保留期使用 `unkeep`。
- 用户指定自定义输出父目录时追加 `--output '<父目录>'`；程序会在其中创建 `<书名>-<BookID>` 子目录。自定义结果不受自动清理管理，也不会被 Skill 自动删除。
- 正常任务的日志、浏览器临时目录和 staging 在成功后立即清理；失败或中断资料最多保留二十四小时。不要自行复制正文或图片到其他缓存目录。
- 程序返回 `reused` 时直接使用既有结果目录，不要为了“刷新”擅自加 `--force`。

## 需要用户确认的操作

当托管结果达到三十本时，先原样展示候选书名、Book ID、日期和大小。用户明确选择后才能执行：

```bash
"$SKILL_DIR/bin/weread-book-export" delete '<result_id>' --yes --json
```

删除前程序仍会再次核验路径、托管标记、manifest、成员和哈希；身份不一致或用户修改过的结果会拒绝删除。不要绕过这一安全门槛。

## 参考资料

- 需要解析命令、JSON 字段、退出码或并发行为时，读取 [references/cli-contract.md](references/cli-contract.md)。
- 只有发生安装、登录、访问、抓取、校验或清理错误时，读取 [references/troubleshooting.md](references/troubleshooting.md)。
