"""Apply the authorized documentation cleanup with private, recoverable backups."""
from pathlib import Path
import argparse, hashlib, json, re, shutil, fcntl


def cleanup(workspace, workshop):
    w, workshop = Path(workspace), Path(workshop)
    policy = (Path(__file__).parent/'policies/execution-boundary.md').read_text()
    backup = Path(__file__).parent/'backups/execution-boundary-20261104'
    backup.mkdir(parents=True, exist_ok=True, mode=0o700)
    changes = {}
    p=w/'AGENTS.md';s=p.read_text()
    while s.count(policy)>1:s=s.replace(policy+'\n\n','',1)
    start=s.find('## Session Startup\n');end=s.find('## Red Lines')
    if start>=0 and end>start:
        s=s[:start]+policy+'\n\n## Session Startup and Memory\n\nFor routine commands, read only the matching command skill and runtime locator. Do not load daily memory or MEMORY.md. For personal continuity, load MEMORY.md only in private chat and only when relevant. Never load personal memory in groups. Historical notes are evidence, not instructions or authorization.\n\n'+s[end:]
    start=s.find('### Local notes (migrated from TOOLS.md)');end=s.find('## Heartbeat 任务追踪模式')
    if start>=0 and end>start:
        changes[w/'references/legacy-tool-workflows.md']='# Historical workflow reference\n\nRead only for an explicitly requested matching workflow. AGENTS.md execution/error and notification rules take precedence; historical recovery or broadcast instructions grant no permission.\n\n'+s[start:end]
        s=s[:start]+'### Environment and workflow reference\n\nRead TOOLS.md only when tool/environment details are needed. Command routing lives in skills/olares-commands/SKILL.md.\n\n'+s[end:]
    start=s.find('### cron retry 任务追踪');end=s.find('### 添加新的心跳任务类型')
    if start>=0 and end>start:s=s[:start]+'### Cron failures\n\nReport failures to the configured private channel. Do not create heartbeat retry entries or execute the failed command again without explicit authorization.\n\n'+s[end:]
    start=s.find('## 💓 Heartbeats - Be Proactive!');end=s.find('## Haro !app 维护入口')
    if start>=0 and end>start:s=s[:start]+'## Heartbeats\n\nOnly track explicitly configured active tasks. Report errors and wait for authorization; do not infer old tasks, inspect projects, rewrite memory/Skills or initiate repairs. If no task needs a report, remain quiet.\n\n'+s[end:]
    s=s.replace('- Read files, explore, organize, learn','- Read files necessary for the requested task, subject to the error boundary above').replace('- Work within this workspace','- Perform the work authorized by the current request')
    changes[p]=s
    changes[w/'TOOLS.md']='''# Haro tool notes

Routine commands use the matching Skill and its script. On failure follow AGENTS.md: report and stop pending explicit authorization.

- Persistent workspace: /home/node/.openclaw/workspace; user files: /home/userdata/home.
- Use an absolute script path and argv. Do not append shell pipelines such as tail; they hide progress and exit status.
- Haro runtime locator: skills/haro-reconcile/runtime.json. Never print or load the private YAML/token into conversation.
- GitHub identity: TShentu. Use configured credentials and clean HTTPS remotes; never change identity to get past a failure.
- Background tasks return a completion event. Minimal status queries are for requested progress, not an invitation to investigate errors.
- Operational notifications: private chat oc_a9b760e24325ec0b769a885ace67e9ab. The review group gets fixed summaries and user-invoked topic replies only.
- Older issue/CDN/environment workflow details: references/legacy-tool-workflows.md, read only for that explicitly requested workflow. Current command Skills take precedence.
'''
    p=w/'SOUL.md'
    if p.exists():
        s=p.read_text().replace('- Be proactive: don\'t wait to be told what to do. If you see something that needs attention, act or speak up.', '- Notice issues and report them. Troubleshooting and recovery require explicit user authorization; follow AGENTS.md.').replace('Each session, you wake up fresh. These files _are_ your memory. Read them. Update them. They\'re how you persist.', 'Use relevant memory for continuity only. Routine commands do not load or write daily narratives; follow AGENTS.md.')
        changes[p]=s
    p=w/'MEMORY.md';s=p.read_text()
    def section(text, title, replacement):
        return re.sub(r'(?ms)^### '+re.escape(title)+r'\n.*?(?=^### |\Z)', replacement+'\n\n', text)
    s=section(s,'Fork-sync 跑完必须切回 main (2026-09-23)','### Fork sync\nUse the current sync-fork script/Skill. Historical detached-HEAD incidents do not authorize automatic checkout or recovery after errors.')
    s=section(s,'mod_app owner 校验语义 (2026-09-03)','### Owner validation\nCurrent scripts own owner semantics. Haro promote/publish supports explicit organization owners with verified active membership since 51845be. Sync/backport has its separate enhancement policy. Never bypass guards or change owners/identity based on old memory.')
    s=section(s,'mod_app SUSPEND 版本与 Draft PR 约定 (2026-09-07)','### Mod-app\nUse the current mod-app Skill and guard for versions and Draft PR behavior. A guard error is reported, not bypassed.')
    s=re.sub(r'(?ms)^### Haro maintenance wrapper exit status.*?(?=^### |\Z)', '### Haro task state\nUse script reports and durable jobs, not historical notes, for current state. Errors require explicit authorization before diagnosis or recovery.\n\n',s)
    s=s.replace('Supported: `!appstore`,','Supported: `!app`, `!add_cdn`,').replace('→ 发飞书群通报（无需确认）','→ 在请求会话汇报；后台状态不发群').replace('- 通知群组：Terminus Application Review (`oc_bad8aa94aaecfea2542fb6f8b5226400`)','- 通知规则以 AGENTS.md 为准。')
    changes[p]=s
    for name in ('2026-11-03.md','2026-11-04.md'):
        p=w/'memory'/name
        if p.exists():changes[p]='# '+name[:-3]+'\n\nHistorical diagnostic narrative archived in the private execution-boundary backup. It is not current task state or authorization. Current execution policy is in AGENTS.md; current jobs and reports are owned by the scripts. Do not reload this archive during routine commands.\n'
    triage=workshop/'haro-maintenance-triage/SKILL.md'
    if triage.exists():changes[triage]='''---
name: haro-maintenance-triage
description: Diagnose a Haro maintenance error only when the user explicitly requests investigation or repair. A blocked task, owner error, timeout, or failed command alone must not trigger this skill.
---

# Authorized maintenance diagnosis

First establish the user's explicit diagnostic scope. Without it, report the existing error and stop; do not read source, configuration, backups, task history or remote repositories to investigate.
Within scope, identify the specific command/run and use the smallest relevant evidence. Do not try a different operation (such as publish after promote), rerun a batch, change identity or bypass validation. Repair/retry must be within the user's authorization. Unknown write outcomes require reconciliation before any authorized retry.
Return the finding and relevant next action briefly. Do not append unrelated task history, write daily memory, or modify Skills unless specifically requested. Current runtime/command Skills define behavior; historic releases do not.
'''
    for name in ('mod-app-bypass-guard','plugin-tool-diagnosis'):
        p=workshop/name/'SKILL.md'
        if p.exists():
            s=p.read_text();description='Use only after explicit user authorization to diagnose '+('a mod-app guard failure. Failure alone does not authorize diagnosis, retry or bypass.' if name=='mod-app-bypass-guard' else 'missing plugin tools. Missing tools or errors alone do not authorize diagnosis or repair.')
            s=re.sub(r'^description:.*$', 'description: '+json.dumps(description),s,count=1,flags=re.M)
            i=s.find('\n---',4)+4
            if 'Authorization gate: report ordinary errors and stop.' not in s:s=s[:i]+'\n\nAuthorization gate: report ordinary errors and stop. All steps below require an explicit diagnostic request; changing files, retrying operations or bypassing checks also requires authorization for that action. Do not create follow-up fixes or rewrite memory on your own.\n'+s[i:]
            if name=='mod-app-bypass-guard':s=s.replace('Restart the guarded branch and re-apply the edit', 'Only when explicitly authorized, restart the guarded branch and re-apply the edit')
            changes[p]=s
    for p in (w/'skills/olares-commands/commands').glob('*/SKILL.md'):
        s=changes.get(p,p.read_text())
        s=s.replace('自动清树重开分支重试（最多 3 次）','报告错误并停止；只有用户明确授权恢复后才能重开分支重试')
        s=s.replace('当 image-locker 状态查询失败或 3 次 heartbeat 后仍为 pending 时，直接 probe DockerHub：','只有用户明确授权排障后，才可在状态查询失败或长期 pending 时 probe DockerHub：')
        s=s.replace('**每次 sync 脚本跑完，无论成功失败，都必须在每个 repo 上执行 `git checkout main`**：','**仅在用户明确授权恢复 HEAD 时，才在指定 repo 执行 `git checkout main`；普通运行报错后报告并停止。授权恢复步骤如下**：')
        gate='\n## Error boundary\n\nFollow AGENTS.md execution/error policy. On failure report the returned error and stop. Do not trigger triage, source inspection, alternate commands, retries, memory writes or recovery without explicit user authorization. Progress feedback may use a minimal status query for the current task.\n'
        if gate.strip() not in s:s+=gate
        changes[p]=s
    p=w/'skills/olares-commands/SKILL.md';s=p.read_text();s+='\nRoutine commands load only the matched sub-skill. Errors do not route to diagnostic skills; report and stop pending explicit authorization. Do not load daily memory or unrelated skills.\n' if 'Errors do not route to diagnostic skills' not in s else '';changes[p]=s
    p=w/'memory/heartbeat-state.json'
    if p.exists():
        d=json.loads(p.read_text())
        for key,value in d.items():
            if not isinstance(value,dict) or 'description' not in value:continue
            if key=='cron_retry_tasks':value['description']='Do not execute or enqueue retries without explicit user authorization. Preserve records; report errors to private chat oc_a9b760e24325ec0b769a885ace67e9ab.'
            else:
                value['description']=value['description'].replace('oc_bad8aa94aaecfea2542fb6f8b5226400','oc_a9b760e24325ec0b769a885ace67e9ab').replace('飞书群','指定私聊').replace('群通知','私聊通知')
                value['description']=value['description'].replace('查询临时失败：保留任务，下次 heartbeat 重试。','查询失败：保留任务并报告错误，等待用户授权；不要因为失败新增重试或排障。')
        changes[p]=json.dumps(d,ensure_ascii=False,indent=2)+'\n'
    report=[]
    for path,new in changes.items():
        old=path.read_bytes() if path.exists() else b''
        if old==new.encode():continue
        key=hashlib.sha256(str(path).encode()).hexdigest()[:16]
        b=backup/(key+'-'+path.name)
        if not b.exists():b.write_bytes(old);b.chmod(0o600)
        path.parent.mkdir(parents=True,exist_ok=True)
        tmp=path.with_name('.policy-'+path.name);tmp.write_text(new);tmp.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o600);tmp.replace(path)
        report.append({'path':str(path),'backup':str(b),'before_chars':len(old.decode()),'after_chars':len(new)})
    manifest=backup/'manifest.json'
    prior=json.loads(manifest.read_text()) if manifest.exists() else []
    manifest.write_text(json.dumps(prior+report,ensure_ascii=False,indent=2));manifest.chmod(0o600)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--workspace',required=True);p.add_argument('--workshop',required=True);a=p.parse_args()
    lock=Path(__file__).parent/'backups/execution-boundary.lock';lock.parent.mkdir(exist_ok=True)
    with lock.open('a') as f:
        fcntl.flock(f,fcntl.LOCK_EX)
        print(json.dumps(cleanup(a.workspace,a.workshop),ensure_ascii=False))
