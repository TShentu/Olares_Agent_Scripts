# Skill: sync_chart（test → prod Chart 同步与上游草稿 PR）

面向 **OpenClaw / Agent** 的操作说明：在需要把 **test 侧**某应用目录同步到 **prod 侧**并在 **prod 的 upstream** 上开**草稿 PR** 时，使用本目录脚本；**不要**臆造路径、分支或 PR 链接，以用户环境中的 `config.yaml`、本地克隆与 GitHub 实际返回为准。

---

## 触发指令

**触发格式**（slash command）：

```text
!sync_chart [参数...]
!push_app [参数...]
```

- **`!sync_chart`** / **`!push_app`**：二者等价，唤起同一技能；**其后所有 token** 与命令行 **`sync_chart.py`** 一致，原样转发即可（例如 `!push_app studio --title "fix: …"`、`!sync_chart --batch charts.txt --skip-sync`）。
- 默认 **`config.yaml`** 为 **`sync_chart` 脚本所在目录**下的 `config.yaml`（与 `compare_chart`「当前工作目录」不同）；推荐先 **`cd sync_chart`** 再执行，或显式 **`-c /path/to/config.yaml`**。
- **`!sync_chart -h`** 或 **`!push_app -h`**：列出全部参数；等价于在合适工作目录下执行：

  ```bash
  cd sync_chart
  python3 sync_chart.py -h
  ```

**实现映射（供宿主 / Agent 集成）**：将 `!sync_chart` 或 `!push_app` 之后的内容作为 `sync_chart.py` 的 `argv`（跳过第一个占位参数）。若依赖默认配置路径，应以 **`sync_chart` 目录为当前工作目录**执行脚本（与 `compare_chart` 的 cwd 语义不同）。

---

## 技能目标

- 在同步前（默认）对 **prod / test** 本地克隆执行 **`git_sync.sync_from_config`**：`fetch`、合并 **upstream** 与 **origin**（与 `compare_chart/sync_repos` 同类逻辑）。
- 将 **test** `local_path` 下指定 **chart 目录**整目录复制到 **prod** `local_path`。
- 在 **prod** 仓库创建分支、提交、推送到 **prod fork**，并对 **`prod.upstream`** 创建 **GitHub 草稿 PR**。
- **HTTPS + PAT**：`fetch` / `push` 使用 **`oauth2:<PAT>@github.com/...`** 嵌入 URL（跨平台，不依赖 macOS 钥匙串）；未 `--skip-sync` 时先 **`verify_github_token`**（REST）。

---

## 何时调用

| 场景 | 建议 |
|------|------|
| 用户要把 test 上某应用版本同步到 prod 并向上游提 PR | **`!sync_chart <CHART>`** 或 **`!push_app <CHART>`**（二者等价） |
| 用户只需本地复制与提交、**不能**或**不应**先 fetch 远端 | 加 **`--skip-sync`**（**不**执行 `verify_github_token` 与 `sync_from_config`；**仍须** PAT 才能 `push` / 建 PR） |
| 用户要用文件里的 PAT（CI / 本地文件） | **`--token-file <path>`**（**仅读文件**，不合并环境变量里的 token） |
| 用户用环境变量注入 PAT | 默认（省略 `--token-file`），配合 **`GITHUB_TOKEN` / `GH_TOKEN` / `GITHUB_TOKEN_FILE`** 或 **`--token-env NAME`** |
| `git fetch` / 认证失败 | 确认 PAT 对仓库有权限、组织 **SSO** 已授权 token；远程需为 **`https://github.com/...`**（SSH 需用户自行改 remote） |
| prod 工作区有未提交修改 | 先让用户处理，或在其明确要求下使用 **`--allow-dirty`** |

---

## 前置条件

1. **配置文件路径**：未指定 `-c` 时，默认 **`--config`** 为 **脚本所在目录**下的 `config.yaml`（`repo_config.DEFAULT_CONFIG_PATH`），与当前 shell 的 `cwd` 无关。相对路径的 **`--batch`** 等仍相对 **`cwd`** 解析。**稳妥做法**：先 **`cd sync_chart`** 再执行，便于批量文件与相对路径一致。
2. **依赖**：`pip install -r requirements.txt`（在 `sync_chart` 下或 `pip install -r sync_chart/requirements.txt`）。
3. **配置**：`config.yaml` 含 **prod / test** 的 `github`、`local_path`、`prod.upstream`；分支 **`git_branch`**（常见 `main`）。
4. **本地克隆**：`local_path` 必须指向已存在的 **prod / test** 应用根；脚本**不会**自动 `git clone`。
5. **独立目录**：`sync_chart` 自带 **`repo_config.py`**、**`git_sync.py`**、**`pat_url.py`**，**不**依赖 `compare_chart` 包路径；可将整个 `sync_chart` 目录单独拷贝使用。

---

## 推荐执行顺序（给 Agent 的步骤）

1. **准备配置**（在仓库中）

   ```bash
   cp sync_chart/config.yaml.template sync_chart/config.yaml
   # 编辑 prod/test 的 github、local_path、upstream
   ```

2. **安装依赖并进入目录**

   ```bash
   cd sync_chart
   pip install -r requirements.txt
   ```

3. **同步单个应用并开草稿 PR**（默认先 fork 同步再复制）

   ```bash
   python3 sync_chart.py <CHART> --title "可选说明"
   ```

4. **批量**（txt 每行一个目录名，`#` 注释）

   Agent **不要**在一次 exec 里等待整批结束（会超时）。非 TTY 下 `--batch` 会立刻后台启动并打印 `job_id`。随后用短命令轮询：

   ```bash
   python3 sync_chart.py --batch charts.txt
   python3 sync_chart.py --status            # 最近一次任务
   python3 sync_chart.py --status JOB_ID     # 指定任务
   ```

   终端里手动跑、需要盯着日志时加 **`--foreground`**。后台启动成功 **不等于** 同步完成。

   **同一时刻只允许一个 job。** 若已有任务在跑，新的 `!sync_chart` / `!push_app` / `--batch` 会立刻失败并打印当前 `job_id`。Agent 应把该提示转给用户，**不要重试提交**。若用户要插队：先 `--cancel`，确认 `--status` 已停止，再提交新任务。

5. **仅用本地克隆当前内容**（跳过 fetch/merge upstream）

   ```bash
   python3 sync_chart.py <CHART> --skip-sync
   ```

---

## 参数速查（`sync_chart.py`）

| 参数 | 作用 |
|------|------|
| `-c` / `--config` | 配置文件路径；**默认**为 **脚本所在目录**下的 `config.yaml` |
| `CHART` | 单个应用目录名（与 `--batch` 二选一） |
| `--batch` / `-batch` | 批量列表文件路径 |
| `--title` | 追加在 PR/提交标题的 `[TYPE][name][ver]` 之后 |
| `--branch` | 覆盖配置中的 `git_branch` |
| `--allow-dirty` | 允许 prod 工作区有未提交更改仍继续 |
| `--token-file` | 从该文件读取 PAT；**给出则不再读环境变量**。文件内 `KEY=value`（`GITHUB_TOKEN`/`GH_TOKEN` 等） |
| `--token-env` | 优先从该环境变量名读取 PAT（未使用 `--token-file` 时） |
| `--skip-sync` | 跳过 prod/test 的 fetch/合并，直接用本地目录做复制与提交 |
| `--status [JOB_ID]` | 查询任务进度；省略 JOB_ID 则显示最近一次 |
| `--cancel [JOB_ID]` | 中断当前 job（当前 chart 结束后停止）；省略则中断最近一次 |
| `--detach` | 强制后台运行 |
| `--foreground` | 强制前台运行（终端默认；Agent 非 TTY 下 `--batch` 默认后台） |

**规则**：默认从环境变量读 PAT。给出 **`--token-file`** 时只读该文件，**不**读取环境中的 `GITHUB_TOKEN`。不必再传 `--token-source`。

**一次提交一个 job**：已有 `queued`/`running` 任务时，新提交直接失败（退出码 2）。不要并发提交。插队请 `--cancel` 后再提。

**批量超时**：`--batch` 在 Agent exec（非 TTY）下会 detach 到独立进程组，宿主超时杀父进程时不会带走同步任务。Agent 应立刻把 `job_id` / `--status` 命令回给用户，再用 `--status` 轮询；禁止再次提交同一批 `--batch` 来“重试等待”。

---

## 凭证与安全（Agent 必须遵守）

- **PAT 来源**：未给 `--token-file` 时为环境变量（`--token-env` 指定变量 → **`GITHUB_TOKEN` / `GH_TOKEN`** → **`GITHUB_TOKEN_FILE`**）。给出 **`--token-file`** 时只解析该文件中的 **`GITHUB_TOKEN` / `GH_TOKEN`**（及可选用户名/邮箱键），**不**读环境。
- **禁止**在对话、代码或提交中写入真实 **GitHub PAT**；**禁止**建议用户将含密钥的 `github.txt` 或 `config.yaml` **提交**到 Git；二者通常在 **`.gitignore`** 中。
- **REST 校验**：未 `--skip-sync` 时，若需要同步且已解析到 PAT，会先调用 **`GET /user`**；推送与建 PR 使用与 GitHub 文档一致的 **`Authorization: token`**。

---

## 输出如何解读

- 每个 chart 会打印同步进度；成功时给出 **PR 的 `html_url`**（若有）。
- **`--batch` 后台模式**：父进程几乎立刻返回 `job_id`、`status`/`log` 路径和 `--status` 查询命令；进度与 PR 列表以 `--status` 为准（`state=done|failed|crashed|cancelled|running`）。
- **已有 job 在跑**：新提交失败，输出当前 job 的 id/进度，并提示可 `--cancel` 后插队。
- 若 **test 侧不存在**该应用目录，会报错并计入失败列表（批量模式，不中断其余 chart）。
- **「与当前 prod 无差异」**时跳过 PR（无新提交）。

---

## 配置字段（回答用户时可用）

- **`git_branch`**：同步与合并分支，常见 `main`。
- **`prod` / `test`**：`github`（fork URL）、`local_path`（本地应用根）、`upstream`（prod 侧上游，用于 PR base 与 fork 同步）。
- **`blacklist`**：`sync_chart` 主流程**不读取**黑名单；黑名单属于 `compare_chart` 对比场景。

---

## 与仓库文档的关系

- 全局人类可读说明见仓库根目录 **`README.md`**（`sync_chart/` 章节）；与 `compare_chart` 同版本演进时，亦可查阅 **`compare_chart/changelog.md`**。
- 本 **`skills.md`** 侧重 **Agent 决策、触发指令与安全约束**；参数与默认值以 **`python3 sync_chart.py -h`** 及源码为准。
