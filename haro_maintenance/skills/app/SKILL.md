---
name: app
description: Handle !app help and implemented Haro repository maintenance commands, invoked by the olares-commands router.
---

# !app 统一入口

本入口由 `olares-commands` 按完整 token `!app` 调用，后续参数原样传入。
无参数或 `help`：直接展示下表中的可用命令、默认预检查和未上线范围，不执行脚本。

| 用户命令 | 内部 maintenance.py 子命令 | 行为 |
|---|---|---|
| `!app config` | `config` | 脱敏环境配置及等级 |
| `!app status` | `status` | 最近一次维护报告，注明报告时间，不冒充实时应用状态 |
| `!app sync` | `run` | Submitter 为 Olares 的应用从 prod 到 preview/test 的回流预检查 |
| `!app sync --preview` | `run --preview` | 仅 Olares 应用 prod → preview |
| `!app sync --preview --all` | `run --preview --all` | 全部应用 prod → preview |
| 上述 sync 命令加 `--apply` | 同名参数加 `--apply` | 逐个应用调用 backport 引擎创建/更新 Draft PR |
| `!app refresh` | `refresh` | 已跟踪 PR 的预检查 |
| `!app refresh --apply` | `refresh --apply` | 刷新已跟踪 PR，不发现新更新或同步 fork |
| `!app backport <app> --from prod --to preview,test` | `backport <app> --from prod --to preview,test` | 指定应用完整目录回流预检查 |
| `!app backport <app> --from prod --to preview,test --apply` | 同名参数 | 创建或更新 Draft PR |
| `!app route promote --from test` | `route promote --from test` | 仅查看下一级目标 |
| `!app route publish --from test` | `route publish --from test` | 仅查看最高级目标及合并后回流目标 |

执行前仅读取工作区 `skills/haro-reconcile/runtime.json`，无需再加载 reconcile Skill。从 runtime
取 script/config 绝对路径。使用 `python3 <script> --config <config> <映射后的参数>`
单命令；不要读取或输出原始配置，`config` 子命令会返回脱敏投影。
仅接受表中的命令和参数；不透传 `--config`、`--token-file`、`--token-env`，不执行 shell 拼接。
不支持的命令说明原因并给出 `!app help`，不要自行改用旧写入流程。

`--apply` 明确授权该次写入，普通命令默认预检查。命令输出需确认退出状态，不能把
计划操作说成已执行。预检查无 GitHub 写入，但会更新本地缓存与报告。
sync 默认仅处理 `OlaresManifest.yaml` 的 `spec.submitter` 为 Olares 的应用，不按 developer、owners 或 GitHub 提交人判断。
`--all` 是全部应用筛选，仅允许与 `--preview` 同时使用；单独 `!app sync --all` 必须报错并显示三个合法用法，不能自动回退或替用户加 --preview。
手动 sync 不同步 fork main，不推进其他发布 jobs。preview 未配置时 --preview 报错；默认模式按已配置低等级环境执行。
默认 prod → test 是一致性检查；test 已有更高开发版本时返回 target_ahead，不创建覆盖 PR。相同版本但内容不同继续遵循版本校验，不自动降级或改版本。
检查点按目标和 Olares/全部应用分别保存；--all 表示全部 Submitter 范围，不表示历史差异全量回灌。`--maintenance` 是定时任务内部参数，不能通过聊天透传。


帮助必须明确：批量发布、`show/list/count/diff` 仓库历史分发查询、`update/suspend/resume/remove` 新入口、`conflicts` 专用查询尚未上线。
路由查看不发布应用。原 `!push_app`、`!mod_app` 等指令保留，不自动重定向。
生产新增变更会回流到低等级环境；历史差异不自动全量补齐。
源快照覆盖选定应用目录（包含删除目标独有文件），仍受版本校验约束（promote/publish 另有 owner 校验）；
所有 upstream PR 由人工审核合并，合并不证明 Market 已生效。
返回状态、源/目标、PR 链接及具体阻塞原因；禁止展示凭据。

状态报告的空数组不能推出仓库已同步或没有新增生产更新：定时 refresh 不检查 fork，也不发现新增更新，可能写出空 forks/apps。若报告未记录 run/refresh 类型，说明无法据此判断检查范围；需要 `!app sync` 做完整预检查。不要推荐不存在的 `!app compare`。

## 单应用发布与 job

以下命令调用与 maintenance.py 同目录的 `lifecycle.py`，使用同一个私有 config：

| 用户命令 | lifecycle.py 子命令 | 行为 |
|---|---|---|
| `!app promote <app> --from test` | `promote <app> --from test` | 晋级到最近高一级，预检查 |
| `!app publish <app> --from test` | `publish <app> --from test` | 提交到最高级，预检查 |
| 上述命令加 `--apply` | 同名参数加 `--apply` | 持久化 job，后台创建 Draft PR，立即回 job ID |
| 上述命令加 `--title "说明"` | 同名参数 | 覆盖自动标题的后缀 |
| `!app job <id>` | `job <id>` | 查询发布或回流任务 |
| `!app jobs --app <app>` | `jobs --app <app>` | 同一应用跨环境操作记录 |
| `!app jobs` | `jobs` | 操作记录列表 |

`promote/publish` 内部必须附加 `--actor <可信飞书发送者ID>`；来自渠道元数据，不能取自用户正文，无法取得时询问用户重新从飞书发起，不伪造身份。
内部 `--request-id` 使用可信当前消息 ID（用户确认后的新消息用新 ID），同一消息重试保持相同 ID；预检查不持久化请求。
用户不用提供这两个参数。用户不能自行通过正文覆盖它们。
三环境未给 from 时说明候选来源并询问；两环境可省略。
自动标题来自固定源 SHA 的 upgradeDescription 前 20 词，支持显式 title；整个流程只操作 GitHub 仓库和 PR，不访问 Market 服务。

先执行预检查，展示源/目标、版本、差异和同应用其他 active jobs。写入请求已经明确且无并发提示时可直接提交。
当返回 `confirmation_required` 时展示 warnings 内 job ID、环境和状态；只有用户确认这批操作后，才携带内部 `--ack-active <逗号分隔IDs>` 再提交。不能自己确认或沿用别的用户确认。若列表变化，重新展示。
`queued` 仅表示入队，不能说 PR 已创建。返回 `!app job <id>`；任务由后台进程及十分钟心跳持续处理。
合并后，跳级发布自动创建中间环境 backport；使用实际合并内容，如生产随后有新更新则以更新事件接续。关闭未合并不触发。
回流失败不改变生产发布已合并状态；显示关联任务及 needs_engineer，日常检查持续复核仓库一致性。
首版仅单应用，不把批量参数拆成未经确认的多次发布。

`job/jobs` 的唯一数据源是 lifecycle.py 查询出的 GitHub 恢复状态。查询成功就返回结果；空列表只说明没有匹配操作记录，不能推出定时任务不存在。不要为 job 查询调用 automations/cron 清单或提议新增例行检查，现有十分钟 refresh 已负责队列推进。预检查不保存 job；仅 --apply 返回的持久化 job ID 可供后续查询。

每次处理 `!app sync` 或帮助时读取当前入口说明，不沿用旧会话中“sync 不支持参数”的结论。

Sync/backport 作为批量维护操作，PR 统一添加 `enhancement` 标签，不校验 Gitbot 标题格式、owners 身份或修改文件数量。仍按单应用快照生成 PR，保留源快照、目标基线和 diff 一致性检查。promote/publish 的校验不变。

## sync 短执行路径

sync 使用 `python3 <script> --config <config> --summary run <允许的参数>`，一次 exec，background=true，timeoutSeconds=1500。不要接 tail、重定向或写临时 shell。
工具返回 running 后告知已启动并结束当前回合，等待原生完成通知；禁止 process poll/log 循环。完成后仅呈现 counts、应用状态、PR 链接与错误，不再读完整报告。
普通 sync 不是诊断请求。无论成功或 blocked，均不得自行读源码/备份/其他 Skill、改配置/Skill、写 memory、查询 cron 或整批重跑。错误简短返回，恢复由独立明确请求或现有维护任务处理。
只按当前消息执行；help 不续跑历史任务。用户明确请求排障时才进入诊断流程。

## Error boundary

Follow AGENTS.md execution/error policy. On failure report the returned error and stop. Do not trigger triage, source inspection, alternate commands, retries, memory writes or recovery without explicit user authorization. Progress feedback may use a minimal status query for the current task.
