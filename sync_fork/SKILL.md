# sync_fork

将 **4 个 fork repo** 的 `main` 分支同步到 **upstream/main**，丢弃任何 diverged commits，确保 fork 始终和上游最新 main 完全一致。

## 触发指令

```
sync fork
同步 fork
!sync_fork
!syncfork
```

## 配置

Repo 列表通过 `config.yaml` 配置（从 `config.yaml.template` 复制，gitignored）：

```bash
cp sync_fork/config.yaml.template sync_fork/config.yaml
```

编辑 `config.yaml` 中的 `repos` 列表即可增删 repo。

## 执行的 repo

| repo | 本地路径 |
|------|----------|
| static | `/home/userdata/home/Code/Olares_Project/static` |
| Olares | `/home/userdata/home/Code/Olares_Project/Olares` |
| apps | `/home/userdata/home/Code/Olares_Project/apps` |
| terminus-apps | `/home/userdata/home/Code/Olares_Project/terminus-apps` |

## 核心命令（脚本内部逻辑）

```bash
# 每个 repo 执行：
git fetch upstream
git checkout main
git reset --hard upstream/main   # 直接丢弃 diverged commits
git push --force origin main
```

## 执行方式

**必须使用 python3 + 绝对路径**，禁止 `cd && python3` 链式调用。

```bash
# Dry run（只检查状态，不执行 push）
python3 /home/userdata/home/Code/Olares_Project/Olares_Agent_Scripts/sync_fork/sync_fork.py --dry-run

# 正式执行
python3 /home/userdata/home/Code/Olares_Project/Olares_Agent_Scripts/sync_fork/sync_fork.py
```

## 输出

脚本会输出每个 repo 的同步状态：
- `[OK] Already in sync` — 无需操作
- `[OK] Synced successfully: <commit>` — 同步成功
- `[ERROR] ...` — 失败（附错误信息）

## 注意事项

- **diverged commits 直接丢弃**：`reset --hard` 不做 merge，直接以 upstream/main 为准
- **PAT 需要 `repo` scope**：用于 force push
- **repo 列表可配置**：通过 `config.yaml` 管理，不写死在脚本中
- **定时任务**：由 OpenClaw cron 自动化（北京时间每天 03:00），调用本脚本执行