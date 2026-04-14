# compare_chart 变更记录

本文件记录 `compare_chart` 目录下脚本与配置的变更历史；新版本追加在顶部。

---

## [未发布]

<!-- 下一次发布前在此累积条目 -->

- 默认配置文件改为**进程当前工作目录**下的 `config.yaml`（不再默认指向脚本目录）；`-h` 帮助文案改为相对路径说明，不再打印绝对路径。
- 更新 **`README.md`**、**`skills.md`**：补充「当前工作目录」与默认 `config.yaml` 的关系、从仓库根调用时的 `-c` 写法，以及 `-h` / 辅助脚本说明。

---

## 2026-04-14

### 新增

- `compare_chart_versions.py`：对比 prod / test 两套应用根目录下同名应用的 `Chart.yaml` 版本，并识别 `.suspend`、`.remove` 与一侧缺失（empty）。
- `config.yaml` / `config.yaml.template`：从配置读取 `github`、`local_path`、黑名单路径；支持 `git_branch`；支持 fork 场景下的 `upstream`。
- `blacklist.txt`：按应用目录名屏蔽差异展示（可通过配置指定其它文件）。
- `sync_repos.py`：在比较前同步本地仓库（`main`、fetch、upstream + origin 合并）；支持 `GITHUB_TOKEN` / `GH_TOKEN` / `GITHUB_TOKEN_FILE`。
- `validate_config.py`：校验配置路径与黑名单文件是否存在。
- `requirements.txt`：声明 PyYAML 依赖。

### 行为说明

- 默认在比较**之前**执行 Git 同步；使用 **`--skip-sync`** 可跳过远端操作，仅按本地文件比较。
- **`--allow-dirty`**：同步时允许工作区存在未提交修改（默认会中止以防误操作）。
- 比较参数：**`-A` / `--all`**（忽略黑名单并展示全部差异类型）、**`--show-suspend`**、**`--show-remove`**、**`-b` 黑名单覆盖**。

### 其它

- 根目录 `.gitignore` 忽略 `compare_chart/config.yaml`、`compare_chart/github.txt`，避免泄露路径与凭证。
- 项目级 `README.md` 中包含 `compare_chart` 的使用说明。
