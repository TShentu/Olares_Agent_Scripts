# Skill: compare_chart（Olares 双源 Chart 对比）

面向 **OpenClaw / Agent** 的操作说明：在需要核对 **prod 与 test** 两套 Olares 应用目录是否一致时，使用本目录脚本；**不要**臆造路径或仓库状态，以用户环境中的 `config.yaml` 与本地克隆为准。

---

## 触发指令

**触发格式**（slash command）：

```text
/compare_chart [参数...]
```

- **`/compare_chart`**：唤起本技能；**其后所有 token** 与命令行 **`compare_chart_versions.py`** 一致，原样转发即可（例如 `/compare_chart --skip-sync`、 `/compare_chart -c other/config.yaml -A`）。默认读取**当前工作目录**下的 `config.yaml`，一般无需 `-c`。
- **`/compare_chart -h`**：列出**全部**可用参数与说明；实现上等价于在 `compare_chart` 目录执行：

  ```bash
  python3 compare_chart_versions.py -h
  ```

  亦可用 **`/compare_chart --help`**，与 `-h` 相同。

**实现映射（供宿主 / Agent 集成）**：将 `/compare_chart` 之后的内容作为 `compare_chart_versions.py` 的 `argv`（跳过第一个占位参数），在 **`compare_chart` 目录作为当前工作目录**下执行上述 Python 脚本即可。

**关于 `-h`**：帮助文案中的默认配置描述为「当前目录下的 `config.yaml`」，**不会**出现机器相关的绝对路径；完整列表以运行时 `python3 compare_chart_versions.py -h` 为准。

---

## 技能目标

- 对比两套**同名应用目录**下的 **`Chart.yaml` `version`**，以及 **`.suspend` / `.remove`** 状态与一侧缺失（`empty`）。
- 可选在对比前 **同步 Git**（默认行为），使本地与远端（及 fork 的 **upstream**）对齐。
- 通过 **黑名单** 过滤噪声应用；通过参数控制是否展示 suspend/remove 或全盘差异。

---

## 何时调用

| 场景 | 建议 |
|------|------|
| 用户要核对 prod/test 应用版本是否一致 | 触发 **`/compare_chart`** 或运行 `compare_chart_versions.py`（参数一致） |
| 用户只想看当前磁盘上的差异，不能改 Git | 必须加 **`--skip-sync`** |
| 用户要先拉代码再比对 | 默认命令（不带 `--skip-sync`） |
| 私有仓库 `fetch` 失败 | 确认环境中有 **`GITHUB_TOKEN`** / **`GH_TOKEN`** 或 **`GITHUB_TOKEN_FILE`** |
| 同步报错「工作区有未提交更改」 | 让用户先 commit/stash，或在其明确要求下使用 **`--allow-dirty`**（慎用） |
| 不确定配置是否正确 | 先运行 **`validate_config.py`** |

---

## 前置条件

1. **当前工作目录（重要）**：未指定 `-c` 时，脚本读取的是 **进程当前工作目录**下的 `./config.yaml`，不是脚本文件所在目录。推荐先 **`cd` 到 `compare_chart/`** 再执行；若在仓库根目录执行 `python3 compare_chart/compare_chart_versions.py`，默认会查找**根目录**的 `config.yaml`，除非使用 `-c compare_chart/config.yaml` 等显式路径。
2. **依赖**：在已 `cd compare_chart` 的前提下执行 `pip install -r requirements.txt`；或 `pip install -r compare_chart/requirements.txt`（自仓库根引用路径）。
3. **配置**：存在 **`config.yaml`**（由 `config.yaml.template` 复制；该文件通常 **gitignore**，勿假设仓库内一定存在）。
4. **本地克隆**：`config.yaml` 里 **`prod.local_path`** 与 **`test.local_path`** 必须指向已存在的应用根目录；脚本**不会**自动 `git clone`。

**其它脚本**：`sync_repos.py`、`validate_config.py` 与主脚本使用相同的 **`-c` 默认值**（当前目录下的 `config.yaml`）。查看帮助：`python3 sync_repos.py -h`、`python3 validate_config.py -h`。

---

## 推荐执行顺序（给 Agent 的步骤）

1. **（可选）校验配置**（在 `compare_chart` 下且已放置 `config.yaml`）

   ```bash
   cd compare_chart
   python3 validate_config.py
   ```

2. **完整流程：默认先同步再比较**

   ```bash
   python3 compare_chart_versions.py
   ```

3. **仅本地比较（不执行任何 git fetch/merge）**

   ```bash
   python3 compare_chart_versions.py --skip-sync
   ```

4. **只做同步、不输出对比表**

   ```bash
   python3 sync_repos.py
   ```

   配置文件不在当前目录或名称不是 `config.yaml` 时，使用 `-c` 指定相对或绝对路径。

---

## 参数速查（`compare_chart_versions.py`）

| 参数 | 作用 |
|------|------|
| `-c` / `--config` | 指定配置文件；**默认**为**当前工作目录**下的 `config.yaml` |
| `--skip-sync` | **跳过 Git**，只读本地目录对比 |
| `-A` / `--all` | 展示全部差异 + **忽略黑名单** + 不因 prod suspend/remove 默认隐藏而过滤 |
| `--show-suspend` | 默认模式下仍显示 prod 为 **suspend** 的行 |
| `--show-remove` | 默认模式下仍显示 prod 为 **remove** 的行 |
| `-b FILE` | 覆盖黑名单文件；`""` 表示不加载黑名单 |
| `--git-branch` | 同步时覆盖配置中的分支名 |
| `--allow-dirty` | 同步时允许脏工作区（**仅在不使用 `--skip-sync` 时有效**） |
| `--token-env NAME` | 仅从该环境变量读取 GitHub token |

**规则**：`--allow-dirty` 与 **`--skip-sync`** 不要混用意义——只做本地对比时用 `--skip-sync`，不需要 `--allow-dirty`。

---

## 凭证与安全（Agent 必须遵守）

- **Access token 来源**：Agent **仅从自身运行环境**读取凭证，优先级与脚本一致：`GITHUB_TOKEN`、`GH_TOKEN`，或 **`GITHUB_TOKEN_FILE`**（指向文件）。宿主应在启动 Agent 或任务环境中注入上述变量，**不要求、也不应要求用户通过 `/compare_chart` 或聊天消息传递 token**。
- **`--token-env NAME`**：仅用于指定「从**哪一个环境变量名**读取 token」，变量值仍须已在 Agent 环境中设置；**不是**让用户在指令里贴上 secret。
- **禁止**在对话、代码或提交中写入真实 **GitHub token**。
- **禁止**建议用户把 token 写入仓库内 `github.txt` 或 `config.yaml` 并提交；`config.yaml` 含本地路径，亦不应提交。

---

## 输出如何解读

- 脚本打印 **prod** / **test** 两列：版本号、`suspend`、`remove`、`empty`、`unknown` 等。
- 最后一行汇总 **存在差异的应用数量** 与 **总应用数**（两棵目录**并集**）。
- **黑名单**中的应用默认不出现在差异列表中（除非 `-A` 或覆盖黑名单为空）。

---

## 配置字段（回答用户时可用）

- **`git_branch`**：同步分支，常见为 `main`。
- **`blacklist`**：黑名单文件路径（相对路径相对 `config.yaml` 所在目录）。
- **`prod` / `test`**：`github`（展示用）、`local_path`（必填）、`upstream`（fork 时建议填写上游 HTTPS）。

---

## 变更记录

目录内 **`changelog.md`** 记录脚本与配置变更；回答版本相关问题时可提示用户查看该文件。

---

## 与仓库文档的关系

- 人类可读的全局说明见仓库根目录 **`README.md`**（`compare_chart/` 章节）。
- 本 **`skills.md`** 侧重 **Agent 决策、触发指令与安全约束**，与 README 互补；参数与默认值以 **`python3 compare_chart_versions.py -h`** 及代码为准。
