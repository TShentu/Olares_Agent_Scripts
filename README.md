# Olares Agent Scripts

面向 Olares 相关自动化与 Agent 工作流的脚本集合；按子目录划分工具。下文**每个工具一节**：用途、用法、注意事项。

---

## 环境

- Python 3.9+（建议 3.10+）
- 进入对应子目录后安装依赖：`pip install -r <子目录>/requirements.txt`

---

## `compare_chart/` — 双源 Chart 对比与仓库预同步

### 用途

在 **prod** 与 **test** 两套本地应用根目录之间，按应用名对齐，比较 `Chart.yaml` 的 **version** 以及 **`.suspend` / `.remove`** 等状态，用于快速发现两套源是否一致。默认在对比**之前**会对两侧 Git 仓库执行 fetch，并在 fork 场景下合并 **upstream** 与 **origin**（`sync_repos.py` 使用 **PAT 嵌入 HTTPS URL** `oauth2:<PAT>@github.com/...` 做 `fetch`，跨平台、不依赖系统钥匙串）。若从环境解析到 PAT，会先用 **GitHub REST** 校验 token 再执行 git 同步。

### 用法

1. 复制配置并填写路径与仓库 URL；若使用黑名单，从模板生成本地文件（已 gitignore）：

   ```bash
   cp compare_chart/config.yaml.template compare_chart/config.yaml
   cp compare_chart/blacklist.txt.template compare_chart/blacklist.txt
   ```

2. 建议在 **`compare_chart` 目录下**执行脚本，使默认读取当前目录的 `config.yaml`；否则用 `-c` 指定配置文件路径。

   ```bash
   cd compare_chart
   pip install -r requirements.txt
   python3 validate_config.py              # 可选：检查路径与黑名单文件
   python3 compare_chart_versions.py       # 默认先同步再比较
   python3 compare_chart_versions.py --skip-sync   # 只读本地，不做 git
   python3 sync_repos.py                   # 仅同步 prod/test，不比较
   ```

3. 常用参数（完整列表见 `python3 compare_chart_versions.py -h`）：`--skip-sync`、`-A/--all`、`--git-branch`、`--allow-dirty`（仅与同步配合）、`--token-env`（与 `GITHUB_TOKEN` / `GH_TOKEN` / `GITHUB_TOKEN_FILE` 配合）、`-b/--blacklist`、`-c/--config`。

### 注意事项

- **工作目录**：未指定 `-c` 时，读取的是**进程当前目录**下的 `config.yaml`，不是脚本所在目录；在仓库根运行 `python compare_chart/compare_chart_versions.py` 时要么 `cd compare_chart`，要么 `-c compare_chart/config.yaml`。
- **凭证**：私有仓库 `fetch` 需要 **`GITHUB_TOKEN` / `GH_TOKEN`** 或 **`GITHUB_TOKEN_FILE`**（文件内为 `KEY=value`：`GITHUB_TOKEN` / `GH_TOKEN` 及可选用户名邮箱键，与 `sync_chart` 一致）。有 PAT 时会先调用 **`GET /user`** 校验再 `fetch`。勿把 token 写入仓库。
- **忽略文件**：`compare_chart/config.yaml`、`compare_chart/blacklist.txt`、本地 `github.txt` 等含路径或密钥的文件请勿提交（见根目录 `.gitignore`）；仓库内仅保留 `blacklist.txt.template`。

### 常用参数（`compare_chart_versions.py`）

| 参数 | 含义 |
|------|------|
| `-c` / `--config` | 配置文件路径；**默认**为**当前工作目录**下的 `config.yaml`（一般先在 `compare_chart` 下 `cd` 再运行，可不写 `-c`） |
| `--skip-sync` | 跳过远端 Git 操作，直接按本地目录比较 |
| `-A` / `--all` | 列出全部差异，并**忽略黑名单**；且不再按 prod 侧 suspend/remove 规则隐藏行 |
| `--show-suspend` | 在默认模式下仍显示 prod 为 **suspend** 的差异行 |
| `--show-remove` | 在默认模式下仍显示 prod 为 **remove** 的差异行 |
| `-b` / `--blacklist` | 覆盖配置中的黑名单文件；`""` 表示不加载黑名单（与 `--all` 时忽略黑名单不同，见下行） |
| `--git-branch` | 同步时覆盖配置中的 `git_branch` |
| `--allow-dirty` | 同步时若工作区有未提交修改仍继续（默认会中止；慎用） |
| `--token-env` | 同步时优先从指定**环境变量名**读取 PAT；否则仍使用 `GITHUB_TOKEN` / `GH_TOKEN` / `GITHUB_TOKEN_FILE`；有 PAT 时先 API 校验 |

说明：**`--allow-dirty` 仅在未使用 `--skip-sync` 时有效**；若只做本地对比，请使用 `--skip-sync`，无需 `--allow-dirty`。

---

## `sync_chart/` — 从 test 同步 Chart 到 prod 并向上游提 PR

### 用途

将 **test** 配置中 `local_path` 下的某个应用目录，**整目录覆盖复制**到 **prod** 的 `local_path`；在 **prod** 的本地克隆上新建分支、提交、推送到 **prod 的 fork**，并对 **`prod.upstream`** 创建**草稿 PR**（标题/正文格式对齐 GithubSync 中 `sync_folders` 一类约定）。默认执行前会调用 **`git_sync.sync_from_config`**（`fetch`/`push` 使用 **`oauth2:<PAT>` 嵌入 HTTPS URL**，与 `compare_chart/sync_repos` 同类策略）。未使用 `--skip-sync` 时会先 **`verify_github_token`**（REST）再同步。

### 用法

1. 复制配置（可与 `compare_chart` 共用同一份 `config.yaml`；默认读取 **脚本所在目录** 下的 `config.yaml`）：

   ```bash
   cp sync_chart/config.yaml.template sync_chart/config.yaml
   ```

2. 在 **`sync_chart` 目录下**执行（或 `-c` 指向配置文件）：

   ```bash
   cd sync_chart
   pip install -r requirements.txt
   python3 sync_chart.py <CHART>                    # 同步单个应用目录名
   python3 sync_chart.py --batch charts.txt       # 批量：txt 每行一个目录名，# 为注释
   python3 sync_chart.py myapp --title "说明"       # 标题在 [TYPE][name][ver] 后追加说明
   ```

3. 其它参数：`--branch`、`--allow-dirty`、`--token-source env|file`、`--token-file`、`--token-env`、`--skip-sync`（跳过 fork 与 upstream 的 fetch/合并，仅用本地克隆当前内容；默认会先同步并校验 PAT）。帮助：`python3 sync_chart.py -h`。

### 注意事项

- **独立运行**：`sync_chart` 自带 `repo_config.py` 与 `git_sync.py`，不依赖 `compare_chart`；可将 `sync_chart` 目录单独拷贝使用（仍需 `pip install -r requirements.txt`）。
- **prod 工作区**：默认要求 **prod 克隆无未提交更改**；否则需先处理或使用 `--allow-dirty`。
- **提交身份**：在 prod 仓库配置 `user.name` / `user.email`，或设置环境变量 **`GIT_AUTHOR_NAME`**、**`GIT_AUTHOR_EMAIL`**。
- **Token**：推送与创建 PR 需要 PAT。可通过环境（`GITHUB_TOKEN` / `GH_TOKEN` / `GITHUB_TOKEN_FILE`）或 **`--token-source file --token-file`** 读取凭证文件（键名同上）。`--token-source file` 时**仅使用文件**，不读取环境中的 token。REST 与 `sync_chart` 一致使用 **`Authorization: token`**。**勿将 `config.yaml`、`github.txt` 提交到远端**。
- **批量列表**：`--batch` 的参数是**文件路径**（txt），不是命令行罗列多个 chart。

---

## 许可证

若仓库根目录未单独声明许可证，以仓库所有者配置为准。
