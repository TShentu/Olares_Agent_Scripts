# Skill: repo_stats（Olares 应用仓库可用性统计）

面向 **Agent**：需要统计 app 仓库在各 OS 版本、各架构下的有效应用数，或查看单应用可用性明细时使用。以 `config.yaml` 与 `output/` 实际产物为准，勿臆造数字。

---

## 触发指令

```text
!preprocess_repo [参数...]
!repo_stats [参数...]
```

- **不传参（推荐）**：`!preprocess_repo`、`!repo_stats` — 默认处理 / 统计 `config.yaml` 里 **`repos` 列表中的全部仓库**。
- 将 `!` 之后的内容原样作为对应脚本的命令行参数。
- 在 **`repo_stats` 目录**下执行（`cwd` 影响默认 `config.yaml` 路径）。
- `-h` / `--help` 查看完整参数。

**典型顺序**：先 `!preprocess_repo`，再 `!repo_stats`。

---

## 前置准备（一次性）

```bash
cd repo_stats
cp config.yaml.template config.yaml   # 编辑 repos / os_versions / filters
pip install -r requirements.txt
```

`config.yaml`、`output/` 已 gitignore，勿提交。

---

## 调用示例

```bash
# 默认：处理 / 统计 config 中全部 repo（无额外参数）
python3 preprocess_repo.py
python3 repo_stats.py

# 仅处理 / 统计单个 repo
python3 preprocess_repo.py --repo apps-origin
python3 repo_stats.py --repo apps-origin

# 指定配置文件 / 输出目录
python3 preprocess_repo.py -c /path/to/config.yaml -o /path/to/output
python3 repo_stats.py -i /path/to/input -o /path/to/output
```

---

## 参数

**默认行为**：除 `-c`、`-o`、`-i` 等有各自默认值外，**省略 `--repo` 即表示 config 里全部 `repos`**。

### `preprocess_repo.py`

| 参数 | 说明 |
|------|------|
| *(无参数)* | 处理 **全部** `repos` |
| `-c` / `--config` | 配置文件；默认 **cwd** 下 `config.yaml` |
| `-o` / `--output` | 输出根目录；默认 `repo_stats/output` |
| `--repo NAME` | 只处理该 repo；可重复，用于缩小范围 |

### `repo_stats.py`

| 参数 | 说明 |
|------|------|
| *(无参数)* | 统计 **全部** `repos` |
| `-c` / `--config` | 配置文件；默认 **cwd** 下 `config.yaml` |
| `-i` / `--input` | 结构化数据根目录；默认 `repo_stats/output` |
| `-o` / `--output` | 统计结果目录；默认与 `-i` 相同 |
| `--repo NAME` | 只统计该 repo；可重复，用于缩小范围 |

---

## 预期返回

### 终端（`preprocess_repo.py`）

成功时打印进度与输出路径，末尾为「预处理完成。」示例：

```text
OS 版本: 1.12.3, 1.12.4, 1.12.5, 1.12.6
输出目录: .../repo_stats/output
默认处理全部 repo: apps-origin, test-repo
处理仓库: apps-origin (/path/to/clone)
  ...
处理仓库: test-repo (/path/to/clone)
  ...
预处理完成。
```

失败时以非零退出码结束，stderr 为中文错误说明（如配置缺失、路径非 git 仓库）。

### 终端（`repo_stats.py`）

成功时打印文件路径，并**在 stdout 输出完整 Markdown 统计表**。示例：

```text
默认统计全部 repo: apps-origin, test-repo
统计仓库: apps-origin
  ...
统计仓库: test-repo
  ...
```

### 输出文件

路径：`output/{repo_name}/`

| 文件 | 内容 |
|------|------|
| `all_apps.yaml` | 全 repo 汇总；头部含 `repo`、`remote_url`、`git_branch`、`commit_sha`、`committed_at`；`apps` 为应用列表 |
| `stats_table.md` | 有效应用数 Markdown 表（含 commit 元数据） |
| `stats_table.csv` | 同上；前几行为 `commit_sha`、`committed_at`、`remote_url` 等元数据 |
| `appdata/{appname}.yaml` | 单应用明细 |

**`all_apps.yaml` 头部示例：**

```yaml
repo: apps-origin
remote_url: https://github.com/beclab/apps
git_branch: main
commit_sha: 64543bf106fbb8e91a592aeef3462b586e1f0ddb
committed_at: '2026-07-03T17:25:13+08:00'
apps:
  - appname: jdownloader2
    status: normal
    os_versions:
      - os_version: 1.12.6
        visibility: true
        chart_version: 1.0.6
        supportarch:
          - amd64
          - arm64
```

**`appdata/{appname}.yaml` 结构**与上例单个 `apps[]` 条目相同。

**`stats_table.md` 表意**：各 OS 版本列下，amd64 / arm64 行分别为该架构**有效应用数量**（具体计数规则见表头「统计条件」一行）。

---

## 配置（供填写 `-c` 指向的文件）

模板：`config.yaml.template`。`repos[].name` 与 `--repo` 参数一致；`filters[].name` 须对应某条 `repos[].name`。

```yaml
os_versions:
  - "1.12.3"
  - "1.12.6"

repos:
  - name: apps-origin
    remote_url: "https://github.com/beclab/apps"
    local_path: "/absolute/path/to/clone"
    git_branch: main

filters:                    # 可选
  - name: apps-origin
    blacklist:
      - appName: myapp
        versionConstraint: ">=1.12.6-0"
```

人类可读说明见仓库根目录 **`README.md`**。
