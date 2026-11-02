"""Idempotently install !app routing while preserving unrelated workspace content."""
from pathlib import Path
import argparse
import hashlib
import shutil
import tempfile


def install(workspace):
    workspace = Path(workspace)
    router = workspace / 'skills/olares-commands/SKILL.md'
    agents = workspace / 'AGENTS.md'
    route = '| `!app` | `commands/app` | Haro 维护帮助、状态、同步、backport 与路由预览 |'
    notice = '\n## Haro !app 维护入口\n\n`!app` 已接入 `skills/olares-commands/commands/app/SKILL.md`。收到 `!app` 时读取当前路由和该文件，不以旧会话记忆判定入口缺失。使用 `!app help` 查看已上线范围。实际 promote/publish 和 Market 精确查询尚未上线，原业务命令保留。\n'
    changes = {}
    text = router.read_text()
    if route not in text:
        if '| `!app` |' in text:
            raise ValueError('existing !app route needs manual reconciliation')
        text = text.replace('| `!add_cdn` |', route + '\n| `!add_cdn` |', 1)
        text = text.replace('| `commands/add-cdn` |', '| `commands/app` | `!app` | 已实现的 Haro 仓库维护能力 |\n| `commands/add-cdn` |', 1)
        text += '\n`!app` 按完整 token 匹配；`!appstore` 不匹配。帮助和可用子命令以 `commands/app/SKILL.md` 为准。\n'
        assert route in text
        changes[router] = text
    text = agents.read_text()
    if '## Haro !app 维护入口' not in text:
        text += notice
    text = text.replace('实际 promote/publish 和 Market 精确查询尚未上线，原业务命令保留。',
        '单应用 promote/publish、job/jobs 已上线；批量与仓库历史分发查询尚未上线。原业务命令保留。')
    if text != agents.read_text():
        changes[agents] = text
    backup = workspace / 'state/haro-maintenance/route-backups'
    backup.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).parent / 'skills/app/SKILL.md'
    dest = workspace / 'skills/olares-commands/commands/app/SKILL.md'
    changes[dest] = source.read_text()
    for path, text in changes.items():
        if path.exists():
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()[:16]
            saved = backup / (path.parent.name + '-' + path.name + '-' + digest)
            if not saved.exists():
                saved.write_bytes(raw)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.app-route-', delete=False) as f:
            f.write(text)
            tmp = Path(f.name)
        tmp.chmod(0o644)
        tmp.replace(path)
    return {'route': '!app', 'workspace': str(workspace), 'backup': str(backup)}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--workspace', required=True)
    print(install(p.parse_args().workspace))
