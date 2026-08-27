# CLI 契约

所有 Agent 应通过 `bin/weread-book-export` 调用程序。入口会建立私有运行时，不要求 Agent 自己安装 Python 包。

## 命令

```text
weread-book-export export <URL或BookID> [--output <父目录>] [--force] --json
weread-book-export list --json
weread-book-export status <job_id> --json
weread-book-export keep <result_id> --json
weread-book-export unkeep <result_id> --json
weread-book-export delete <result_id>... --yes --json
weread-book-export cleanup [--dry-run] --json
weread-book-export auth status|login|reset --json
weread-book-export doctor --json
```

`purge-all --yes --json` 只供卸载脚本内部使用，不应在普通导出流程中调用。

## 输出通道

- `--json` 模式的 stdout：恰好一个最终 JSON 对象。
- stderr：零到多行 JSONL 进度事件；不得包含 Cookie、完整正文或认证快照。
- `auth_scan_required` 表示命令正在等待用户扫码，不是失败。

成功示例：

```json
{
  "status": "completed",
  "book_id": "d31323b0813abaf26g0137c2",
  "title": "书名",
  "output_dir": "/Users/example/Downloads/WeRead Exports/书名-d31323b0813abaf26g0137c2",
  "markdown_path": "/Users/example/Downloads/WeRead Exports/书名-d31323b0813abaf26g0137c2/书名.md",
  "manifest_path": "/Users/example/Downloads/WeRead Exports/书名-d31323b0813abaf26g0137c2/manifest.json",
  "chapters": 20,
  "characters": 123456,
  "images": 12,
  "retained_until": "2026-09-02T00:00:00+00:00",
  "validation": {"valid": true}
}
```

`status=reused` 与 `completed` 同样是成功，但表示没有再次抓取。Agent 仍须检查 `validation.valid`；旧记录的精简验证字段至少应为 `true`。

## 退出码

| 退出码 | 含义 | Agent 行为 |
|---:|---|---|
| 0 | 成功或查询完成 | 按最终 JSON 报告 |
| 2 | 输入或命令无效 | 请求正确 URL、Book ID 或参数 |
| 10 | 需要用户确认 | 展示候选和影响，等待明确决定 |
| 20 | 登录失败或超时 | 建议重新执行 `auth login` |
| 21 | 当前账号无完整网页版访问权 | 说明限制，不宣称已导出 |
| 30 | 抓取或未分类运行错误 | 保留错误原因，按排障流程处理 |
| 31 | 完整性校验失败 | 不发布成功结果，不降级为部分成功 |
| 40 | 运行时、平台或 Chrome 不满足 | 执行 `doctor --json` 并报告 |
| 50 | 可用空间不足 1 GiB | 请用户释放空间或换自定义目录 |
| 130 | 用户中断 | 报告中断；恢复资料最多保留 24 小时 |

## 并发与复用

- 不同 Book ID：不同 Chrome context、不同任务目录和不同文件锁，可并行运行。
- 同一 Book ID：等待同一书锁；先完成者发布，等待者重新校验结果后返回 `reused`。
- 认证过期：所有任务争用一个认证锁，只有锁持有者打开扫码窗口，其他任务等待新快照。
- SQLite 使用 WAL；等待文件锁时不持有 SQLite 事务。
