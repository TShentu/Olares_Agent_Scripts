# compare_chart 变更记录

本文件记录 `compare_chart` 目录下脚本与配置的变更历史；新版本追加在顶部。

---

## [未发布]

<!-- 下一次发布前在此累积条目 -->

### 认证与 Git（`sync_repos` / `compare_chart_versions`）

- 新增 **`pat_url.py`**：从 `https://github.com/owner/repo` 生成带 PAT 的 HTTPS URL；`fetch` 默认使用 **`https://oauth2:<PAT>@github.com/...`** + refspec（跨平台、不依赖 macOS 钥匙串或 GCM 交互）。
- 新增 **`verify_github_token`**（`requests`）：在同步前对 PAT 执行 **`GET https://api.github.com/user`**（`Authorization: token`）；`compare_chart_versions` 与 **`sync_repos.py` 独立入口**在解析到 token 时均会先校验再 `sync_from_config`。
- **`requirements.txt`**：增加 **`requests>=2.28.0`**。

### 根目录文档

- **`README.md`**：补充 compare_chart / sync_chart 的 PAT 来源、`--token-env` 与 `--token-source file`、oauth2 嵌入策略及「file 模式不读环境 token」等说明。

### sync_chart（同仓库，独立目录）

- **`sync_chart`** 与 **`compare_chart`** 解耦：自有 **`repo_config.py`**、**`git_sync.py`**、**`pat_url.py`**；默认 **`config.yaml`** 为脚本所在目录。
- **`sync_chart.py`**：`--token-source env|file`、`--token-file`；`fetch`/`push` 与上述 oauth2 策略一致；未 `--skip-sync` 时先 **`verify_github_token`**。

### 其它（历史条目）

- **黑名单**：仓库内移除已提交的 `blacklist.txt`，改为 `blacklist.txt.template`（仅说明格式）；本地 `blacklist.txt` 由用户复制生成并已加入根 `.gitignore`。
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
