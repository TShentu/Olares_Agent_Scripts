# sync_chart 技术实现说明

本文说明 `sync_chart/` 的运行模型、模块划分、关键函数与落盘协议。面向维护脚本的人，不替代 `python3 sync_chart.py -h` 与 `skills.md`。

Agent 操作约定见 [skills.md](./skills.md)；面向用户的用法见仓库根 [README.md](../README.md) 的 `sync_chart/` 节。

---

## 1. 定位与硬约束

脚本把 **test** 本地应用目录整目录覆盖到 **prod**，在 prod fork 上推分支，并向 **prod.upstream** 开草稿 PR。

实现上必须满足三条物理约束：

1. **prod / test 各一份本地 git 工作区**，同一时刻只能有一个进程在改 prod。
2. **Agent exec 有超时**。整批串行 copy / commit / push / 开 PR 会超过超时；父进程必须尽快返回。
3. **不引入队列中间件**。任务状态用目录里的 JSON + pid 探活，不用 Redis、数据库或独立 daemon。

因此采用 **一次提交 = 一个 job**：提交时若已有 job 在 `queued` / `running` 且 pid 仍活着，新提交立刻失败。需要插队时，先 `--cancel` 再提交。

---

## 2. 目录与模块

```text
sync_chart/
├── sync_chart.py          # CLI 入口、单 chart 同步、job 主循环
├── job_util.py            # job 落盘、互斥、detach、--status / --cancel
├── git_sync.py            # PAT、git fetch/merge、fork 对齐
├── repo_config.py         # config.yaml 与 prod/test 路径
├── pat_url.py             # HTTPS URL 嵌入 PAT（oauth2:<token>@github.com/...）
├── run_sync.py            # Olares/Agent 环境薄包装（改 sys.path 后 runpy）
├── config.yaml.template
├── requirements.txt       # PyYAML、requests
├── skills.md
├── changelog.md
└── architecture.md        # 本文件
```

运行时产物（gitignore，勿提交）：

```text
sync_chart/output/
├── .sync.lock             # flock，保护 prod 工作区
└── jobs/
    ├── latest             # 最近一次 job_id
    └── <job_id>/
        ├── status.json    # 状态机 + 每 chart 结果
        └── sync.log       # detach 后 worker 的 stdout/stderr
```

依赖方向：

```text
run_sync.py → sync_chart.py
                  ├─ job_util.py
                  ├─ git_sync.py ─┬─ repo_config.py
                  │               └─ pat_url.py
                  ├─ repo_config.py
                  └─ pat_url.py
```

`sync_chart` **不**依赖 `compare_chart`。`git_sync.py` 也可单独执行，只做 fork 对齐、不开 PR。

---

## 3. 运行模型

### 3.1 一次提交一个 job

`main()` 在创建新 job 之前调用 `find_active_job()`。最近一次 job 若状态为 `queued`/`running` 且 `pid` 仍存活，打印当前 job_id / 进度并以退出码 **2** 拒绝。

`--job-dir` 是 worker 重入参数（`argparse.SUPPRESS`），此时跳过互斥检查，因为本进程就是那个活跃 job。

互斥有两层：

| 层 | 机制 | 作用 |
|----|------|------|
| 提交期 | `find_active_job()` | 第二份 CLI 立刻失败，带上如何 `--status` / `--cancel` |
| 执行期 | `RepoLock`（`fcntl.flock` 非阻塞） | 防止两份 worker 同时改 prod；正常路径不应撞上 |

### 3.2 前台与后台

| 场景 | 行为 |
|------|------|
| 终端 TTY + 单个 CHART | 默认前台，跑完打印 PR URL |
| 终端 TTY + `--batch` | 默认前台（可 `--detach`） |
| 非 TTY（Agent）+ `--batch` | 默认 detach，立刻返回 `job_id` |
| `--detach` / `--foreground` | 强制覆盖上述默认 |

Detach 使用 `subprocess.Popen(..., start_new_session=True)`，worker 进入新 session。Agent 超时杀掉父进程组时，**不会**带走 worker。子进程 argv 带 `--foreground --job-dir <path>`，标准输出接到 `sync.log`。

前台与后台走 **同一套** `run_charts_job()`，只是谁在等、日志写到哪。

### 3.3 插队

没有运行时入队、没有队头插入。插队 = 人工两步：

1. `python3 sync_chart.py --cancel`：置 `cancel_requested`，向 worker 发 `SIGTERM`。
2. `--status` 确认 `cancelled`（或 pid 已死）后，再提交新 job。

`--cancel` **不会**在 copy/push/开 PR 中途强杀。worker 在每个 chart **开始前和结束后**检查取消标志；当前这一个会做完。随后尽量 `git switch` 回配置分支。

---

## 4. 任务状态与 `status.json`

### 4.1 状态

```text
queued ──► running ──► done
                │
                ├────► failed      （有 chart 失败，其余已跑完）
                ├────► cancelled   （用户 --cancel 或带 cancel 标记的进程退出）
                └────► crashed     （pid 已死，但状态仍是 queued/running）
```

`cmd_status` / `find_active_job` 会先 `mark_dead_if_needed()`：若声明还在跑但 pid 不在，按是否 `cancel_requested` 写成 `cancelled` 或 `crashed`。这两种都 **不再占坑**，允许新提交。

### 4.2 字段

`create_job()` 写入的核心字段：

| 字段 | 含义 |
|------|------|
| `job_id` | 时间戳 + 短随机，也是目录名 |
| `pid` | 提交进程或 worker pid（detach 后改写成子进程 pid） |
| `state` | 上一节的状态 |
| `started_at` / `finished_at` / `updated_at` | ISO 时间 |
| `total` / `index` / `current_chart` | 进度 |
| `charts` | 本次提交的目录名列表（快照，提交后不再改） |
| `results[]` | `{name, outcome, pr_url, error}`，`outcome` 为 `ok` / `skip` / `fail` |
| `message` | 给人看的一句话 |
| `cancel_requested` | `--cancel` 或信号置位 |

写盘用临时文件 `replace`，避免读到半截 JSON。

`output/jobs/latest` 只记最近一次 `job_id`。`--status` / `--cancel` 省略 id 时读它。历史 job 目录保留，不自动清理。

---

## 5. 主流程

单次或批量提交（非 `--status` / `--cancel`）：

```text
main()
  ├─ 解析 CHART 或 --batch 列表、config、PAT
  ├─ 若无 --job-dir：find_active_job() → 忙则 exit 2
  ├─ create_job(charts)  →  status.json + latest
  ├─ 若 detach：
  │     spawn_detached_worker()  →  打印 job_id 后返回
  └─ 否则 / worker 重入：
        run_charts_job()
          ├─ SIGTERM/SIGINT → 只设停止标志
          ├─ RepoLock.acquire()
          ├─ 默认 verify_github_token + sync_from_config（prod 与 test）
          └─ for chart in charts:
                若取消 → _finish_cancelled（切回分支，state=cancelled，exit 130）
                sync_one_chart()
                记录 results
                若取消 → 同上
        全部结束：state=done 或 failed；释放锁
```

`sync_one_chart()` 单 chart 路径：

```text
切到配置分支
复制 test/<chart> → prod/<chart>
无 diff → skip（不开 PR）
否则：建 sync-<chart>-<时间> 分支 → add/commit → push fork → 草稿 PR → 切回原分支
```

单个 chart 的 git/PR 失败只记入 `results`，**不** `sys.exit` 整批。fork 对齐失败（缺 token、fetch/merge 失败）仍视为 job 级失败。

---

## 6. 关键函数

### 6.1 `sync_chart.py`

| 函数 | 职责 |
|------|------|
| `main` | 参数、互斥、创建 job、detach 或进入 `run_charts_job` |
| `run_charts_job` | job 主循环：锁、fork 同步、逐 chart、取消检查、收尾状态 |
| `sync_one_chart` | 复制、提交、push、开草稿 PR；返回 `(ok, pr_url, error)` |
| `create_pull_request` | `POST /repos/{upstream}/pulls`，`draft=true`，超时 30s |
| `push_branch_fork` | 用嵌入 PAT 的 HTTPS URL `git push` 到 prod fork |
| `get_pr_type` | 按 `.remove` / `.suspend` / upstream 是否已有目录决定 NEW/UPDATE/REMOVE/SUSPEND |
| `ensure_commit_identity` | repo config → `GIT_AUTHOR_*` → 凭证文件用户名邮箱 |
| `_cancel_requested` | 进程内停止标志 **或** 重读 `status.json` 的 `cancel_requested` |
| `_finish_cancelled` | 写 `cancelled`、尝试切回分支、退出码 130 |
| `load_chart_list_file` | `--batch`：每行一个目录名，`#` 注释 |

### 6.2 `job_util.py`

| 函数 / 类 | 职责 |
|-----------|------|
| `create_job` | 分配 job_id、写初始 `status.json`、更新 `latest` |
| `find_active_job` | latest + 探活；仅 `queued`/`running` 且 pid 活着算占用 |
| `print_busy_error` | 拒绝提交时的 stderr（job_id、进度、`--cancel` 提示） |
| `cmd_status` | 打印进度与 PR 列表；先做 pid 死亡收敛 |
| `cmd_cancel` | 置取消位、`SIGTERM`、最多等约 20s、必要时把死进程标成 `cancelled` |
| `RepoLock` | `output/.sync.lock` 上非阻塞独占 flock |
| `spawn_detached_worker` | 新 session 拉起 `python -u sync_chart.py … --foreground --job-dir` |
| `mark_dead_if_needed` | 僵尸 queued/running → `crashed` 或 `cancelled` |
| `configure_stdio` | stdout/stderr 行缓冲，避免 Agent 管道里看不到进度 |

### 6.3 `git_sync.py`

| 函数 | 职责 |
|------|------|
| `sync_from_config` | 对 prod、test 各做 `sync_one_repo` |
| `sync_one_repo` | fetch origin、fetch upstream（若有）、checkout、merge upstream、merge origin |
| `run_git` | 非交互 git；`fetch` 时把 remote URL 换成 PAT 嵌入形式 |
| `resolve_github_token` | `--token-env` → `GITHUB_TOKEN`/`GH_TOKEN` → `GITHUB_TOKEN_FILE` |
| `parse_github_credentials_file` | `KEY=value`；`GITHUB_*` 优先于 `GH_*` |
| `verify_github_token` | `GET /user`，PAT 无效则退出 |

### 6.4 其它

- `repo_config.resolve_roots`：读 `prod`/`test` 的 `local_path` 与 `github`。
- `pat_url.github_authenticated_https_url`：默认 `https://oauth2:<PAT>@github.com/owner/repo.git`。
- `run_sync.py`：给固定路径的 Olares 运行时加 `site-packages`，再 `runpy` 进 `sync_chart.py`。逻辑以 `sync_chart.py` 为准。

---

## 7. 命令一览

```bash
python3 sync_chart.py <CHART>                 # 单个
python3 sync_chart.py --batch charts.txt      # 批量（非 TTY 默认后台）
python3 sync_chart.py --status                # 最近一次 job
python3 sync_chart.py --status JOB_ID
python3 sync_chart.py --cancel                # 中断最近一次活跃 job
python3 sync_chart.py --cancel JOB_ID
python3 sync_chart.py --batch charts.txt --foreground
python3 sync_chart.py <CHART> --detach
```

常用选项：`-c`、`--title`、`--branch`、`--allow-dirty`、`--skip-sync`、`--token-file`、`--token-env`。默认从环境变量读 PAT；给出 `--token-file` 则只读文件。

退出码约定：

| 码 | 含义 |
|----|------|
| 0 | 同步全部成功，或 `--status` 为 `done` |
| 1 | 业务失败 / `--status` 为 failed·crashed·cancelled / 无可查询任务 |
| 2 | 已有活跃 job，拒绝新提交 |
| 130 | worker 因用户中断而结束 |

---

## 8. 本次核心改动

相对「同步的 CLI 前台一把梭、失败就整批退出」，这次只加了 **job 外壳与互斥**，没有做成可运行中入队的全局队列。

### 8.1 为什么是 job，不是队列

prod 只有一份工作区，真正能并行的调度没有意义。讨论过「单队列 + 插队 + 三次重试 + failed 门闩」，状态机会明显变复杂（current 能否 force、run 边界被不断拉长、后来入队用哪份 token）。

落地选择：

- **一次 CLI 提交 = 一个 job 快照**（chart 列表提交时冻结）。
- **第二份提交失败**，而不是挂到队尾。
- **插队靠 `--cancel` + 重新提交**，不在 worker 里改别人的列表。

### 8.2 Agent 超时

`--batch` 在非 TTY 下 **detach**：父进程只负责建 job、拉起 worker、打印 `job_id`。Worker 在新 session 里跑 `run_charts_job`。

配套：

- `--status` 读 `status.json`（短命令，适合轮询）。
- 进度按 chart 刷新，`results` 里带 PR URL。
- stdout 行缓冲；建 PR HTTP 超时 30s；PR 间隔 1s。

**不要**用再提交一次 `--batch` 当「等待」。已有 job 时再提交会直接失败。

### 8.3 独占与中断

| 改动 | 行为 |
|------|------|
| `find_active_job` | 提交前探活 latest job |
| 退出码 2 | 忙时拒绝，stderr 给出 `--status` / `--cancel` |
| `--cancel` | `cancel_requested` + SIGTERM；当前 chart 跑完再停 |
| pid 死亡收敛 | 无主的 queued/running → `crashed` 或 `cancelled`，释放占坑 |
| `RepoLock` | 执行期第二道锁，防双 worker 改 prod |

取消是 **协作式** 的：正在进行的 git/GitHub 调用不会被半路 SIGKILL。中断后尝试切回配置分支；若工作区仍脏，下次可能需要 `--allow-dirty` 或手工处理。

### 8.4 单 chart 失败不再打死整批

`sync_one_chart` 出错返回 `(False, None, err)`，记入 `results` 后继续下一个。job 结束时若有失败项，`state=failed`。fork 级 `sync_from_config` 失败仍会让整个 job 失败。

### 8.5 明确不做的事

- 运行中往同一 job 追加 / 插队 chart。
- 失败自动回队尾、三次重试、`failed` 状态下拒收直到 `--retry`/`--discard`。
- 多 worker、消息队列、跨主机调度。
- 把 PAT 写入 `status.json`（worker 从环境或 `--token-file` 再解析）。

### 8.6 改动涉及的文件

- `sync_chart.py`：`run_charts_job`、取消检查、`main` 互斥与 `--cancel`/`--status`/`--detach`。
- `job_util.py`：新建；job 目录、锁、detach、status/cancel。
- `skills.md` / `changelog.md` / 根 `README.md`：Agent 与用户说明。
- `../.gitignore`：`sync_chart/output/`。
