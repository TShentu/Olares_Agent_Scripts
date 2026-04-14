# Olares Agent Scripts

面向 Olares 相关自动化与 Agent 工作流的脚本集合。仓库按**子目录**组织不同用途的工具；当前包含 **`compare_chart`**（对比两套应用目录中的 Chart 版本与状态）。后续可在本 README 中追加新的脚本说明。

---

## 环境要求

- Python 3.9+（建议 3.10+）
- 各子目录若提供 `requirements.txt`，请在该目录下安装依赖，例如：

```bash
pip install -r compare_chart/requirements.txt
```

---

## `compare_chart/` — Chart 版本对比与仓库同步

### 作用与目的

在两条 Olares 应用源（例如 **prod** 与 **test** 对应的本地克隆）之间，按**应用目录名**对齐，比较：

- 各应用目录下 **`Chart.yaml` 中的 `version`**
- 特殊状态文件：**`.suspend`**、**`.remove`**（与仅版本号并列展示）
- 仅一侧存在目录时表现为 **empty** 等差异

用于快速发现两套源之间「同名应用」的版本或上架状态是否一致。可选在比较**之前**自动对本地 Git 仓库执行 **fetch / 合并**，使对比基于与远端对齐后的代码（fork 场景下还可先与 **upstream** 同步）。

### 目录内文件

| 文件 | 说明 |
|------|------|
| `config.yaml.template` | 配置模板；复制为 `config.yaml` 后填写（`config.yaml` 已 gitignore，避免泄露路径与仓库信息） |
| `compare_chart_versions.py` | 主入口：默认先同步再比较；支持黑名单与多种过滤参数 |
| `sync_repos.py` | 仅执行同步（不比较）；逻辑与主脚本中的同步阶段一致 |
| `validate_config.py` | 校验 `config.yaml` 中路径、黑名单文件等是否可用 |
| `blacklist.txt` | 默认黑名单（应用目录名，一行一个）；也可在配置中指定其它路径 |
| `requirements.txt` | Python 依赖（当前为 PyYAML） |
| `skills.md` | 供 OpenClaw / Agent 使用的操作说明与决策要点 |
| `changelog.md` | 本目录变更记录 |

### 配置说明（`config.yaml`）

从模板复制：

```bash
cp compare_chart/config.yaml.template compare_chart/config.yaml
```

主要字段：

- **`git_branch`**：同步时检出的分支，默认 `main`。
- **`blacklist`**：黑名单文件路径；相对路径相对于 **`config.yaml` 所在目录**；空字符串 `""` 表示不使用黑名单。
- **`prod` / `test`**（必填）：
  - **`github`**：对应该侧的 GitHub 仓库地址（用于日志展示与核对，脚本不会自动 `clone`）。
  - **`local_path`**：本地**应用根目录**（其下每个子目录为一个应用，内含 `Chart.yaml` 或 `.suspend` / `.remove`）。
  - **`upstream`**（可选）：上游父仓库 HTTPS 地址；若为 fork，同步时会 `fetch upstream` 并合并 `upstream/<git_branch>`，再合并 `origin/<git_branch>`。若本地已配置 `upstream` remote，即使未填此项也会尝试使用已有 upstream。

### 默认配置文件与当前工作目录

- 三个脚本 **`compare_chart_versions.py`**、**`sync_repos.py`**、**`validate_config.py`** 在未传入 **`-c` / `--config`** 时，均默认读取 **进程当前工作目录**下的 **`config.yaml`**（相对路径 `./config.yaml`），**不是**脚本文件所在目录。
- 因此推荐：先 **`cd compare_chart`**，将 `config.yaml` 放在该目录下再运行，即可省略 `-c`。
- 若在**仓库根目录**执行 `python3 compare_chart/compare_chart_versions.py`，默认会在**根目录**查找 `config.yaml`；此时应 **`cd compare_chart`**，或使用 **`-c compare_chart/config.yaml`**（或你的配置文件相对/绝对路径）。
- **`python3 compare_chart_versions.py -h`**（及 **`--help`**）列出全部参数；帮助中的默认说明为「当前目录下的 `config.yaml`」，**不**包含机器相关的绝对路径。

### 操作说明

在 `compare_chart` 目录下执行（或从其它目录调用时按上一节使用 `-c`）：

```bash
cd compare_chart
pip install -r requirements.txt
```

**1. 校验配置（可选）**

在 `compare_chart` 目录下且存在 `./config.yaml` 时无需 `-c`：

```bash
python3 validate_config.py
```

**2. 比较 Chart（默认会先同步再比较）**

```bash
python3 compare_chart_versions.py
```

**3. 只读本地、不做任何 Git 操作**

若希望完全基于当前磁盘上的文件比较（不 `fetch`、不 `merge`）：

```bash
python3 compare_chart_versions.py --skip-sync
```

**4. 仅同步、不比较**

```bash
python3 sync_repos.py
```

其它路径的配置文件使用 **`-c` / `--config`** 指定，例如 `-c ../my-config.yaml`。

**查看帮助**：`python3 compare_chart_versions.py -h`；`sync_repos.py`、`validate_config.py` 同样支持 **`-h`**，且 **`--config` 默认行为与主脚本一致**。

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
| `--token-env` | 同步时仅从指定**环境变量名**读取 GitHub token（仍可使用 `GITHUB_TOKEN_FILE`） |

说明：**`--allow-dirty` 仅在未使用 `--skip-sync` 时有效**；若只做本地对比，请使用 `--skip-sync`，无需 `--allow-dirty`。

### GitHub 访问令牌（同步私有仓库时）

同步阶段需要对 `git fetch` 授权时，由**运行环境或调用方**注入凭证，**不要**把 token 写入仓库或提交到 Git。

推荐方式（任选）：

- 环境变量 **`GITHUB_TOKEN`** 或 **`GH_TOKEN`**
- 环境变量 **`GITHUB_TOKEN_FILE`**：指向文件路径；文件内可为单行 token，或 `KEY=value` 形式（脚本会解析等号右侧）

私有仓库在未设置 token 时，`fetch` 可能失败。

### 安全与仓库忽略项

- **`compare_chart/config.yaml`**：包含本地路径与仓库信息，已列入 `.gitignore`，请勿提交。
- **`compare_chart/github.txt`**：若用于本地测试 token，同样应忽略；切勿将真实 token 提交远端。

---

## 后续脚本

在本仓库中新增其它脚本目录时，建议：

1. 在该目录下提供 `README` 片段或在本文件增加二级标题说明用途、依赖与用法。
2. 敏感配置使用模板 + gitignore 的实际配置文件模式。

---

## 许可证

若仓库根目录未单独声明许可证，以仓库所有者配置为准。
