# sync_chart 变更记录

本文件记录 `sync_chart` 目录下脚本与配置的变更历史；新版本追加在顶部。

与 **PAT / Git 预同步 / oauth2 HTTPS** 等跨目录改动，亦可能出现在仓库根 **`README.md`** 与 **`compare_chart/changelog.md`** 的对应条目中。

---

## [未发布]

<!-- 下一次发布或打 tag 前在此累积条目 -->

### 功能

- **批量同步不再阻塞 Agent**：非 TTY 下 `--batch` 自动 **detach** 到新 session，立刻返回 `job_id`；用 **`--status [JOB_ID]`** 查询进度与 PR 列表。`--detach` / `--foreground` 可强制。进度写入 `output/jobs/<job_id>/`（status.json + sync.log），并对 prod 工作区加锁，避免两批任务同时改同一仓库。
- 单个 chart 的 git/PR 失败改为记入失败列表并继续其余项，不再 `sys.exit` 中断整批；PR 间隔从 5s 降为 1s；GitHub 建 PR 超时 30s；stdout 行缓冲。
- **一次提交一个 job**：已有任务在 `queued`/`running` 时，新提交立即失败并告知需等待；新增 **`--cancel`** 中断当前 job（当前 chart 结束后停止），以便插队提交。
- **`--token-file` 即文件模式**：不必再传 `--token-source`。默认读环境变量；给出 `--token-file` 则只读文件。旧的 `--token-source` 仍接受但不出现在 `-h` 中。
- Agent 触发别名 **`!push_app`**，与 **`!sync_chart`** 参数相同。

### 文档

- 新增 **architecture.md**：模块划分、一次提交一个 job 的运行模型、`status.json` 状态机、关键函数与调用链；文内单独一章记录本次核心改动。
- 新增 **skills.md**：面向 Agent 的 **`!sync_chart`** / **`!push_app`** 触发方式、与 `sync_chart.py` 参数映射、默认 **`config.yaml`**（脚本所在目录，与 `compare_chart` 的「当前工作目录」语义不同）、**`--skip-sync`** / **`--token-file`**、凭证与安全约束、配置字段说明等。
- 新增 **changelog.md**（本文件）：独立记录本目录变更历史。
