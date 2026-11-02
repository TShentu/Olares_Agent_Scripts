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
| `!app sync` | `run` | 三环境同步及新增生产更新 backport 预检查 |
| `!app sync --apply` | `run --apply` | 执行 fork 同步与新增更新的 Draft PR 处理 |
| `!app refresh` | `refresh` | 已跟踪 PR 的预检查 |
| `!app refresh --apply` | `refresh --apply` | 刷新已跟踪 PR，不发现新更新或同步 fork |
| `!app backport <app> --from prod --to preview,test` | `backport <app> --from prod --to preview,test` | 指定应用完整目录回流预检查 |
| `!app backport <app> --from prod --to preview,test --apply` | 同名参数 | 创建或更新 Draft PR |
| `!app route promote --from test` | `route promote --from test` | 仅查看下一级目标 |
| `!app route publish --from test` | `route publish --from test` | 仅查看最高级目标及合并后回流目标 |

执行前读取工作区 `skills/haro-reconcile/SKILL.md` 及其 `runtime.json`，从 runtime
取 script/config 绝对路径。使用 `python3 <script> --config <config> <映射后的参数>`
单命令；不要读取或输出原始配置，`config` 子命令会返回脱敏投影。
仅接受表中的命令和参数；不透传 `--config`、`--token-file`、`--token-env`，不执行 shell 拼接。
不支持的命令说明原因并给出 `!app help`，不要自行改用旧写入流程。

`--apply` 明确授权该次写入，普通命令默认预检查。命令输出需确认退出状态，不能把
计划操作说成已执行。预检查无 GitHub 写入，但会更新本地缓存与报告。
同步是对配置中的所有环境执行；不接受尚未实现的 `sync --env`。

帮助必须明确：当前 `promote`、`publish` 实际发布、`show/list/count/diff` Market
查询、`update/suspend/resume/remove` 新入口、`job/conflicts` 专用查询均未上线。
路由查看不发布应用。原 `!push_app`、`!mod_app` 等指令保留，不自动重定向。
生产新增变更会回流到低等级环境；历史差异不自动全量补齐。
源快照覆盖选定应用目录（包含删除目标独有文件），仍受 owner/版本校验约束；
所有 upstream PR 由人工审核合并，合并不证明 Market 已生效。
返回状态、源/目标、PR 链接及具体阻塞原因；禁止展示凭据。

状态报告的空数组不能推出仓库已同步或没有新增生产更新：定时 refresh 不检查 fork，也不发现新增更新，可能写出空 forks/apps。若报告未记录 run/refresh 类型，说明无法据此判断检查范围；需要 `!app sync` 做完整预检查。不要推荐不存在的 `!app compare`。
