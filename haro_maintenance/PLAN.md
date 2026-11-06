# Haro 应用生命周期管理重构计划

更新日期：2026-11-02。本文合并本次讨论的全部已确认决策，作为后续实施依据，替代此前分散的方案描述。第一阶段独立维护模块已提交并部署到云端，具体版本、任务、验证和剩余边界见 [DEPLOYMENT.md](DEPLOYMENT.md)。单应用 promote/publish 与 job 已按后文增量落地；批量、仓库历史分发查询及完整旧配置迁移仍按阶段推进。

## 1. 目标与实施边界

Haro 是运行在 `hzfysttq.olares.com` 上的 OpenClaw Agent，以飞书机器人作为全公司入口。员工通过自然语言或统一指令查询应用、提出应用修改与环境发布请求；Haro 创建并跟踪 Draft PR，审批和合并继续由人工在 GitHub 完成。

最终职责包括：

1. 应用内容修改、环境晋级、发布、下架、恢复和移除。
2. 各环境 fork 的 upstream 同步、跨环境 backport、冲突处理和差异报表。
3. 根据指定仓库自身 Git 历史，查询指定 OS／CPU 架构下应发布的应用版本、列表和数量。

镜像转存、图片上传 CDN 后续移交其他 Agent。

**第一阶段只新增独立的仓库定时维护能力，暂不改动现有 Haro 指令、Skills 和业务脚本。** 新的 promote／publish 交互、完整查询能力和整体 OpenClaw 重构放到后续阶段。第一阶段保留现有镜像、CDN 等功能，待其他 Agent 接手后再移除。

## 2. 已确认的线上基础

通过 Olares Files 访问云端，不以本机仓库作为线上现状。

| 项目 | 已确认位置或值 |
|---|---|
| Haro 工作区 | Files：`drive/Data/clawdbot/config/workspace`；容器：`/home/node/.openclaw/workspace` |
| 业务脚本 | Files：`drive/Home/Code/Olares_Project/Olares_Agent_Scripts` |
| test upstream | `https://github.com/Above-Os/terminus-apps` |
| test fork | `https://github.com/TShentu/terminus-apps` |
| prod upstream | `https://github.com/beclab/apps` |
| prod fork | `https://github.com/TShentu/apps` |
| preview | upstream `beclab/apps-preview`；fork `TShentu/apps-preview`；分支 `main`；level 20 |

现有应用同步通过删除目标应用目录后 `copytree` 完成；fork 同步通过 `reset --hard upstream/main` 和普通 `push --force` 完成。`mod-app` 已有 PR guard，可复用其中适用的校验规则。

线上配置目前不止一种：

- `sync_chart/config.yaml` 和 `compare_chart/config.yaml` 使用顶层 `test`／`prod`，环境字段为 `github`、`upstream`、`local_path`。
- `sync_fork/config.yaml` 使用 `repos` 列表，包含应用仓库，也包含 static、Olares 等其他仓库。
- `repo_stats/config.yaml` 使用 `repos`、`os_versions`、`filters`。

OpenClaw 配置曾记录 `lastTouchedVersion=2026.10.1`，这不等于已验证运行版本。旧 cron 文件标记为 migrated，不能用它们推断活跃任务。部署前读取真实运行版本和活跃调度记录。

## 3. 统一配置与兼容

### 3.1 保留现有格式

统一配置继续采用 `config.yaml`，以现有 `sync_chart`／`compare_chart` 格式向后兼容扩展。保留环境在顶层的结构，不改成新的 `repositories` 层级。

- `github` 仍表示 fork URL。
- `upstream` 仍表示正式 PR 目标仓库 URL。
- `local_path` 保持现有路径含义，不在加载时偷偷重写旧路径。
- `git_branch` 保留为全局默认分支，缺省为 `main`；新增可选环境级 `git_branch` 覆盖。
- `blacklist` 保留原有比较用途与路径解析语义，不能自动作为 backport 排除规则。

示例仅展示结构，不包含真实秘密，preview 已配置真实仓库：

```yaml
git_branch: main
blacklist: ""

test:
  level: 10
  github: "https://github.com/TShentu/terminus-apps"
  upstream: "https://github.com/Above-Os/terminus-apps"
  local_path: "/home/userdata/home/Code/Olares_Project/terminus-apps"

preview:
  level: 20
  github: "https://github.com/TShentu/apps-preview"
  upstream: "https://github.com/beclab/apps-preview"
  local_path: "/home/userdata/home/Code/Olares_Project/apps-preview"

prod:
  level: 30
  github: "https://github.com/TShentu/apps"
  upstream: "https://github.com/beclab/apps"
  local_path: "/home/userdata/home/Code/Olares_Project/apps"

auth:
  github_token: "<部署时填写>"

maintenance:
  enabled: false
  timezone: Asia/Shanghai
  schedule: "0 3 * * *"
  fork_sync:
    mode: mirror_upstream
    preserve_divergent_head: true
  backport:
    strategy: source_snapshot
    scope: changed_apps
    delivery: draft_pr
    exclude_apps: []

runtime:
  release: "<固定工具及 Skills 版本>"
  maintenance_root: "<独立维护数据目录>"

notifications:
  channel: feishu
  target: "<维护通知群>"
```

两级配置完全删除 preview 段。preview 段存在但字段不完整属于错误，不静默降级。缺少 maintenance 时默认不启用新任务。

### 3.2 level 定义环境顺序

数值越大表示发布等级越高，数值无需连续。环境主分支构成发布链；fork 的功能分支不参与等级排序。

- 所有环境都未配置 level 的旧配置，按 `test=10`、`preview=20`、`prod=30` 推导，只包含实际配置的环境。
- 一旦任一环境显式填写 level，所有环境都必须填写唯一整数；不允许显式值和隐式值混用。
- 自定义环境名称必须显式提供 level，沿用相同仓库字段；配置加载器将保留的系统配置段与环境段区分开。
- 支持两级、三级和更多级。需要晋级的维护部署至少有两个有效环境。
- 最高等级是 publish 目标和日常 backport 来源，不按名称硬编码 prod。

### 3.3 配置加载与旧功能隔离

新增一个统一加载器供维护工具和后续 Skills 使用，统一校验、默认值和脱敏输出。Skills 通过工具获取脱敏配置，不直接读取含 Token 的原文件。

第一阶段不改旧脚本的默认路径和配置文件。新维护模块显式通过 `--config` 读取兼容主配置，并使用独立 clone／worktree，不切换旧功能正在使用的 checkout。

后续统一旧功能时，将旧配置中的统计 filters、OS 列表、比较黑名单和非应用 fork 同步列表导入主配置的专属配置段，再生成旧脚本需要的兼容文件。环境地址只在顶层维护一份；生成文件不是另一套人工配置源。不能将两种含义不同的 `repos` 列表直接拼接到同一顶层键。

保留旧命令参数及凭据读取行为。新加载器的凭据优先级为：显式 Token 文件／显式环境变量选择 > 配置内 Token > 默认 GitHub 环境变量。显式指定但缺失或无效时直接报告，不悄悄切换身份。旧脚本不会因为新增 `auth` 段而自动获得读取它的能力，需后续通过适配入口接入。

### 3.4 单文件备份与重建

主配置保存重建所需的仓库映射、凭据、固定工具／Skills 版本、OpenClaw 兼容版本、Agent 配置、调度、通知和模型服务配置。按需扩展，不把环境信息硬编码在 Skill 中。

- 可在主配置保存 Token，以满足单文件迁移；权限设为 `0600`，不提交 Git，备份加密，日志和展示全部脱敏。
- bootstrap 根据主配置安装固定版本、准备目录和仓库、生成 OpenClaw 配置和旧格式兼容文件、幂等注册调度。
- 新环境可显式指定根目录映射，恢复时更新 local_path 并输出变化清单；兼容旧配置不意味着新环境必须具有旧的绝对路径。
- 外部黑名单等规则须在单文件备份中内嵌保存，恢复时生成兼容文件；只保存外部文件路径不满足单文件恢复要求。
- 任务与已处理来源状态保存到可恢复的远端维护状态分支及 PR 元数据，配置记录其位置；本地数据库只是可恢复缓存。状态分支不参与环境主分支的发布历史。
- 单文件保证配置和能力重建，不保证恢复聊天历史、未提交文件或已失效凭据。第一阶段先覆盖新增维护模块，完整 Haro 重建随旧功能迁移完成。

## 4. 后续统一指令与交互

员工使用 `!app` 或自然语言，两者进入相同参数和权限校验，不因显式命令而绕过检查。

| 能力 | 指令 |
|---|---|
| 帮助 | `!app help` |
| 应用查询 | `!app show dify --env prod --os 1.12.7 --arch arm64` |
| 列表与数量 | `!app list …`、`!app count …` |
| 环境差异 | `!app diff dify`、`!app diff --all` |
| 内容修改 | `!app update dify --env test <修改要求>` |
| 晋级 | `!app promote dify --from test` |
| 发布 | `!app publish dify --from test` |
| 状态修改 | `!app suspend/resume/remove dify --env prod --reason "原因"` |
| 显式回流 | `!app backport dify --from prod --to preview,test` |
| 任务与异常 | `!app job <任务编号>`、`!app conflicts --env test` |

### 4.1 promote 和 publish

- promote：目标为比来源等级高的最近一级，不需要 `--to`。
- publish：目标固定为最高等级，来源必须比目标低。
- from 缺省时，若仅有一个合法来源环境则自动选择，否则询问；不根据最后一次其他人的操作推断。
- 来源已经是最高等级时报告没有可提交目标，不创建空操作。
- 两级 test／prod 配置中，promote 和 publish 默认均为 test → prod，与现有 push_app 一样创建 Draft PR。
- 无 preview 却显式指定 `--from preview` 时报告未配置。

| 配置 | 操作 | 结果 |
|---|---|---|
| 两级 | `promote app` 或 `publish app` | test → prod |
| 三级 | `promote app --from test` | test → preview |
| 三级 | `promote app --from preview` | preview → prod |
| 三级 | `publish app --from preview` | preview → prod |
| 三级 | `publish app --from test` | test → prod；合并后向 preview 创建 backport PR |

跳级 publish 在确认生产 PR 已合并后，使用目标实际合并内容向所有被跳过的中间等级创建 backport PR。PR 被关闭但未合并不触发。记录来源及合并事件，与每日回流共用去重机制。自动触发针对已记录事件对应的快照；若存在更新事件，合并为更新的维护任务，避免反复覆盖。

### 4.2 其他默认行为

- 全公司成员可查询和提出 PR，发起人取自可信飞书身份；审批、owners 和合并权限由 GitHub 仓库规则控制。
- 内容修改默认 test；自定义链没有 test 时使用最低等级，并在回执明确展示。生产紧急修复可显式指定最高等级，合并后进入回流。
- 查询未指定环境时展示多环境概览；缺少 OS／架构时展示矩阵，不给含混的单一总数。
- 下架通常对应 suspend；移除 remove 展示具体范围并要求确认。恢复已移除应用需要选定内容重新提交，不能等同于删除 suspend 标记。
- 批量任务有总任务编号，每个应用和目标环境单独产生结果与 PR。失败项独立处理。
- 回执包含操作、源／目标环境、版本变化、任务编号、PR 链接和状态。明确区分已创建 PR、已合并、仓库应发布；不将合并等同线上生效。
- 第一阶段原 `!push_app` 等完全不变。后续提供旧指令兼容适配，并对目标歧义进行确认。

## 5. 第一阶段每日仓库维护

### 5.1 调度流程

默认每天北京时间 03:00，采用独立维护任务：

1. 读取、校验配置，取得全局任务锁，恢复未完成任务。
2. 获取所有已配置 upstream／fork 的明确 SHA，固定本轮输入。
3. 将各 fork 主分支同步到对应 upstream 主分支。
4. 检测最高等级仓库自检查点以来新合并的应用变更。
5. 对每个受影响应用，分别向所有较低等级仓库创建或刷新 backport Draft PR。
6. 检查现有维护 PR 的冲突、目标过期、合并、关闭和检查结果。
7. 持久化来源事件、PR 关联、扫描检查点和通知状态。
8. 向维护群发送本轮汇总，包括成功、无变化、已自动处理的冲突、失败及待人工审核 PR。

较低等级之间的正常版本差异不是自动回流触发器。不能每天扫描所有差异后把测试中的领先版本全部覆盖。首次没有检查点时先建立基线并报告存量差异，不自动覆盖所有历史差异；需要处理的存量应用可显式发起 backport。

每个来源事件按应用、目标和来源标识去重。扫描已发现不等于 PR 已合并；待处理任务独立持久化，不因推进扫描检查点而丢失。源历史发生改写且检查点不再可达时停止自动推断，报告并重建基线。

### 5.2 fork upstream sync

fork 主分支是 upstream 的镜像，不承担未提交的开发工作：

- 相同：无操作；仅落后：更新到 upstream。
- 存在 fork 独立提交：先将旧 head 保存到可恢复的归档分支，确认归档成功，再同步主分支。
- 更新使用带明确预期旧 SHA 的 `--force-with-lease`。并发变化导致 lease 失败时重新读取，不改为无条件 force。
- 不覆盖功能分支，不操作 upstream 主分支，不影响未列入维护配置的仓库。

部署时盘点现有 sync_fork 调度，仅移交重叠的应用仓库同步。现有 static、Olares 等同步继续保留，避免为了启用新任务而停掉整项旧任务。

### 5.3 跨环境 backport

最高等级仓库的新应用内容、suspend 和 remove 均生成相应回流 PR。文本冲突按第 6 节的源快照策略自动处理；不自动合并 upstream PR。

- 同应用、同目标只维持一个机器人管理的待审维护 PR；来源更新后刷新它，不批量开重复 PR。
- 同应用已有人工 PR 时报告关联和冲突，不重写人工分支，遵守目标仓库是否允许并行 PR 的规则。
- 维护 PR 被人工拒绝关闭时，对同一来源事件保持已拒绝状态，不每天重开；新来源变化或显式重新请求可以产生新任务。
- 技术错误最多重试三次，记录失败并通知；网络写入结果不明时先查询远端，避免重复 PR。
- 权限、owners、版本规则或发布检查失败保留为业务异常，不通过自动改版本、弱化 guard 或切换身份绕过。

## 6. 源快照覆盖与准确 PR diff

### 6.1 固定的覆盖语义

在本次同步的单个应用范围内，完整采用选定源 commit 的 Git tree；范围外保留目标内容。采用 Git 对象生成提交，不再从可能变化的旧工作目录做文件 copy。

1. 固定源 commit S 和目标 upstream 最新 commit T。
2. 在独立工作区从 T 建立维护分支。
3. 将应用目录恢复为 S 中的内容；可使用 `git restore --source=<S> --staged --worktree -- <app>`，并按仓库协议处理整目录移除。
4. 生成单个提交 C，其父提交为 T。
5. 验证 C 的应用 tree 等于 S 的应用 tree，且应用范围外与 T 相同。
6. 提交与 PR 记录源仓库、S、应用 tree hash、目标 T、来源 PR、策略版本及本次新增／修改／删除摘要。
7. 校验目标仓库规则后创建 Draft PR。

双改文件使用源文件；目标独有文件删除；源新增文件新增；文件类型、权限、符号链接和二进制内容以源 tree 为准。`.github`、根 README 和其他应用不参与覆盖。生成分支后不执行来自应用仓库的任意脚本。

这是确定性的自动冲突处理，不依赖模型猜测如何合并。它允许把目标中的独立开发改动替换掉，甚至提出版本回退；这些变化必须明确展示在 PR 中。目标仓库不允许的版本回退仍会被校验拦住，不能承诺所有覆盖 PR 都一定可合并。

不采用整条环境分支直接 merge，也不以 `merge --squash -X theirs` 代替完整覆盖。`-X theirs` 只优先解决冲突部分，目标无冲突的独有内容仍可能留下；squash 不保留源分支作为父提交。追溯依赖固定来源 SHA、tree 校验和提交元数据，不宣称源历史已经合并进目标。

### 6.2 目标变化后重新生成

GitHub PR 使用三点 diff，比较 merge base 到 head。创建时 C 的父提交为 T，可以保证 `T...C` 与 `T..C` 等价。

目标推进为 T2 后，从 T2 重新生成相同源快照的提交 C2，而不是直接 merge 目标分支进旧维护分支。重新校验树、修改范围和 diff，更新 PR 说明，使目标新增内容在覆盖结果中准确展示。

- 只更新机器人所有且 head 仍匹配预期的分支，使用明确 SHA 的 lease。
- 检测到人工在维护分支加提交时停止重写并提示，不删除人工工作。
- 自动更新后旧审核结论应失效，审核对应新的 diff。
- 当目标已经与源一致时，不生成空提交；关联 PR 标记为已被其他变更满足。

每日任务保证每次检查时重新验证，不能保证全天任何时刻的 PR 页面都基于最新 main。要保证合并时准确，启用目标更新触发的刷新，以及必须通过的来源、范围、目标新鲜度检查，并配合严格的分支最新状态保护。该检查属于维护功能的部署配套；条件未具备时在报告标明快照时间，不宣称持续最新。

### 6.3 各仓库历史完全独立

每个仓库依据自身 Git 历史独立计算发布结果。晋级／backport 只在目标形成本次内容变更的提交，记录来源，但不搬运源仓库完整历史。

**不要求跨仓库补齐旧 OS 的有效版本，不要求各环境的历史选版结果一致，也不把这种差异判定为同步失败。** 同步验收针对当前选定应用快照、目标规则和准确 diff。

## 7. 后续查询与差异报表

查询输入为环境、仓库 commit、OS 版本和 CPU 架构。支持 amd64／arm64，并归一化 x86_64／aarch64 别名。

复用或抽取实际 Market／GitOps 发布规则，现有 repo_stats 作为参考实现进行对照，不能直接假定它与真实发布实现完全一致。历史 Manifest、版本约束、上下架控制文件及过滤规则都在当前数据源内求值。

输出包含应用标识、应发布与否、选中版本、判断原因、数据源 commit、规则版本和数据更新时间。单应用、列表、统计、报表共用同一解析器。解析失败显示未知或结果不完整；刷新失败显示旧快照时间，不返回伪造的零数量。

报表展示所有配置环境的版本、同版本内容、上下架状态和 OS／架构结果差异，并关联维护 PR 与异常；对历史结果差异提供说明，不强制修复成一致。

后续完整报告默认每天 09:30，周一增加未处理 PR／异常汇总。第一阶段先提供每日维护运行报告，不以前置完整查询引擎阻塞维护模块交付。

## 8. OpenClaw 与 Skills 目标架构

对员工保持一个 Haro 飞书入口，内部拆成两个固定职责：

| Agent | 职责 |
|---|---|
| haro | 飞书交互、查询、按明确请求创建变更任务 |
| haro-maintenance | 固定调度、fork 同步、backport、PR 跟踪和维护报告 |

第一阶段仅新增维护 Agent 或独立调度入口，不重写当前 main Agent 的配置。实际字段按部署版本 schema 校验，不照搬旧版示例。

工具层负责确定性 Git／GitHub 操作、配置解析、身份校验和任务持久化；Skill 只收集参数、调用工具、解释结果。后续业务 Skills 拆为：

- haro-query：查询、列表、数量和环境差异。
- haro-lifecycle：内容修改、promote、publish 和上下架。
- haro-reconcile：维护、backport、覆盖策略与异常处理；第一阶段优先提供。
- haro-report：运行汇总和差异报告。

共同接口采用结构化 JSON，统一任务编号、状态、错误和来源证据。旧脚本能复用的校验抽入公共工具，不能让不同 Skills 分别实现 Git 写入规则。

使用独立任务工作区；同应用、同目标串行，其他任务有限并行。持久化任务、来源映射、检查点及通知去重状态，重启后恢复。权限和任务事实不放在聊天记忆中。

完整重构阶段精简 AGENTS.md 为职责、路由和边界；SOUL.md 保留 Haro 语气；通用个人助手任务不进入公司发布工作流。飞书私聊按用户隔离，群操作绑定发起人和会话。

员工任务结果回原会话，定时维护进入固定维护群，异常按应用负责人映射提醒；不使用“最后一次对话”决定通知目标。活跃任务后续每 10 分钟检查，无变化不反复通知。固定调度调用工具，不动态在 HEARTBEAT.md／聊天记忆中拼接业务逻辑。

## 9. 实施顺序与验收

### 第一阶段

1. 配置加载器与脱敏检查：读取原配置，增加 level 和维护配置，校验两级／三级模式；提供模板和恢复入口。
2. 独立 Git 维护工具：新工作区、lease、fork 分歧归档、源快照提交、来源记录、幂等 PR 和恢复状态。
3. 只读试运行：输出计划同步范围和候选 diff，验证环境、身份、版本规则和应用识别。
4. 接入维护调度和通知：先确认重叠旧任务，再启用每日执行；仅接管相同应用 fork 的重复调度。
5. 配套 PR 新鲜度检查与刷新；验证目标推进和并发操作不会导致错误覆盖或误导性合并。

### 后续阶段

1. 接入统一生命周期指令和自然语言，保留旧入口兼容。
2. 接入基于各自 Git 历史的查询、统计与差异报表。
3. 迁移旧配置至统一源，完善完整机器人单文件重建。
4. 完成 OpenClaw 职责与 Skills 重构，再移交镜像和 CDN 功能。

### 必须通过的验收场景

- 原 test/prod 配置无 level 可加载；无 preview 时 promote／publish 均为 test → prod；部分填写 level、重复 level 和不完整 preview 被拒绝。
- 三级和四级链按等级选择相邻／最高目标；跳级合并后只补被跳过的中间环境，定时回流覆盖全部低等级环境。
- 旧脚本和指令不变，新维护模块不占用旧 checkout；旧配置黑名单语义不被改为同步排除。
- fork 落后、领先、分歧、并发推送均正确处理；归档失败不覆盖 main；不碰非维护仓库及人工功能分支。
- 双方改同一文件、目标独有文件、源新增文件、二进制、权限变更和删除均得到准确源快照；范围外内容不变。
- PR 初次生成和目标推进后重新生成均满足范围和 tree 校验，三点／两点比较一致；发现人工提交不强推覆盖。
- 新增、下架、移除和恢复遵守目标规则；语法冲突可自动消除，版本／owners／检查失败不被绕过。
- 首次基线不覆盖全部存量差异；重复调度不重复 PR；人工关闭后不重开同一事件；网络超时、任务重启和检查点恢复不丢任务。
- 发布 PR 未合并不触发补回流；合并后采用实际合并内容，并与定时任务去重。
- 查询在同一仓库快照下与实际发布规则一致；列表、数量和单应用结果一致；不同仓库历史选版差异允许存在。
- 单文件恢复可重建配置、固定版本、仓库及调度；外部规则已内嵌，秘密不出现在日志、PR 或 Skill 上下文。

部署必填项包括真实 preview 配置（若启用三级）、有效凭据、维护通知目标、工具固定版本、持久化状态位置和实际运行版本。部署时验证 GitHub 分支保护与身份权限；查询阶段定位实际发布规则实现。缺失项明确报告，不创建虚构仓库或跳过规则。

## 10. Git 方案依据

- [Git restore](https://git-scm.com/docs/git-restore)：从选定 tree 恢复内容，源中缺失的已跟踪路径可以删除以匹配源。
- [Git merge](https://git-scm.com/docs/git-merge)：squash 与 theirs 冲突选项的语义，不等于完整源快照替换。
- [GitHub 分支比较](https://docs.github.com/en/pull-requests/reference/branches)：PR 使用三点比较，应处理目标推进导致的基线变化。

临时 Git 实验已验证：`merge --squash -X theirs` 保留目标独有文件；源快照替换可得到相同应用 tree 并保留其他目录；目标推进后两种 diff 可以不同，从新目标重新生成后恢复一致。上述实验不代表已在真实仓库执行或部署维护功能。


## 单应用发布增量（2026-11-02）

本次落地单应用 promote/publish、自动标题、异步 job 和 job/jobs 查询；批量后续实现。
每个应用跨环境操作独立记录，发起前展示同应用 active jobs，确认后才入队；Gitbot
继续负责仓库端提交约束。发布 PR 合并后由十分钟心跳创建中间环境 backport，
回流失败独立展示，不回滚或混淆已完成发布。每日同时复查失败任务涉及的仓库
内容，不以扫描检查点推进作为一致性成功。所有操作针对 GitHub；历史分发查询
也是仓库历史解析，不调用 Olares Market 服务。


## 2026-11-03 同步范围增量

`!app sync` 默认 `--all`，保持所有配置环境 fork 同步和生产更新回流。
`--preview` 仅 prod → preview 增量回流及该方向已有任务，不操作 test、fork main 或其他发布 jobs。
两参数互斥；未配置 preview 明确报错。检查点按回流目标保存，旧共享检查点先复制给各目标再分别推进，避免漏掉 test 的更新。
两种模式默认预检查，`--apply` 才写入；不隐式进行历史差异全量回灌。


## 2026-11-03 Submitter 范围修订（替代此前 sync 参数语义）

仅保留 `sync`（Olares → preview/test）、`sync --preview`（Olares → preview）和 `sync --preview --all`（全部 Submitter → preview）。单独 --all 拒绝。
读取 spec.submitter，缺失不推断；回流复用单应用引擎。test 开发版本领先则报告 target_ahead，不覆盖。
手动 sync 不同步 fork main 或推进发布 jobs；每日维护保留 fork sync，应用自动扫描默认仅 Olares。
旧自动任务在 refresh 也过滤；显式单应用 backport 与发布关联任务保留授权范围。按目标×应用筛选独立记录增量检查点。

### Sync enhancement policy (2026-11-03)

Sync/backport PRs are batch maintenance and must carry `enhancement`. Skip local Gitbot title-format, owner-identity and file-count gates for these tasks. Keep per-app snapshot generation, version safeguards, source/target and diff verification. Promote/publish guards are unchanged. This supersedes earlier blanket owner-check requirements for backports. Existing managed open backport PRs receive the label during apply refresh; dry-run does not write labels.

### Sync execution cost (2026-11-04)

Interactive sync uses --summary, one background exec and native completion notification. No polling loops, automatic diagnostics, memory writes or whole-batch retry. Full reports remain on disk; compact chat reports cap app rows at 30. App routing reads only runtime.json, not a second reconcile skill. The installer updates both skill copies and repairs the add-cdn table row. Uncertain checkpoint writes abort the run and preserve the lease for reconciliation; they must not continue mutating other apps. Main-agent context cost remains; a deterministic non-LLM command dispatcher is a separate future phase.

### Explicit authorization before diagnosis (2026-11-04)

Supersedes Agent-driven recovery guidance: ordinary errors are reported and execution stops. Diagnostics, alternate commands and retries require explicit user authorization. Progress feedback remains allowed. Normal script workflows and configured schedules keep their scope. `clean_workspace_policy.py` archives conflicting workspace rules, recent diagnostic narratives and automatic triage triggers; backups are private and outside the daily memory path. Routine commands load only their route and runtime locator.

## 2026-11-05 聊天默认执行

!app promote/publish/sync/backport/refresh 默认执行，由 Skill 补 --apply；显式 --dry-run 仅预检查，转换时移除该参数且不加 --apply。旧 --apply 兼容，两参数同时给出报冲突。只读命令不变。脚本 CLI、定时任务、并发确认和错误停止规则不变。

## 2026-11-06 refresh 周期

本实例仅将 refresh 改为固定每 5 小时；每日完整 run 保留 03:00。统一配置使用 maintenance.refresh_every，bootstrap 优先转换为 --every；旧 refresh_schedule 保持兼容。后台首次提交仍立即启动，合并检查及失败队列恢复最多等待一个 refresh 周期。

2026-11-06 后续决定：本实例 refresh 恢复每 10 分钟（refresh_schedule: "*/10 * * * *"），移除 refresh_every。保留固定间隔配置能力。检查频率和通知频率分开评估；尚未修改通知策略。

2026-11-06 通知降频：refresh 仍每 10 分钟。notifications.refresh_summary_seconds=18000，普通变化每 5 小时汇总最新状态，只列变化项；新 blocked/rejected/needs_engineer 或执行错误及时报告，同一持续错误去重，恢复后再次发生可再通知。每日总结、用户即时回复和原生基础设施失败告警保留。状态按生成输出记录，发送回执由 OpenClaw 管理。
