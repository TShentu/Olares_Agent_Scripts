# repo_stats changelog

## 2026-07-03

- 两步流程：`preprocess_repo.py`（结构化可用性）+ `repo_stats.py`（OS×架构统计表）。
- 配置：`repos[]`（`name`、`remote_url`、`local_path`、`git_branch`）、`os_versions`、`filters[]`（`blacklist`：`appName` + `versionConstraint`）。
- 输出：`output/{repo}/all_apps.yaml`、`stats_table.md` / `.csv`、`appdata/*.yaml`。
- 统计标注 `commit_sha` / `committed_at`；有效应用排除 `suspend` / `remove`。
