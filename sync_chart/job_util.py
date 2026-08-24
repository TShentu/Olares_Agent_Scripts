"""Background batch job status, lock, and process detach for sync_chart."""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
JOBS_ROOT = SCRIPT_DIR / "output" / "jobs"
LOCK_PATH = SCRIPT_DIR / "output" / ".sync.lock"
STATUS_NAME = "status.json"
LOG_NAME = "sync.log"
LATEST_NAME = "latest"
ACTIVE_STATES = frozenset({"queued", "running"})


def configure_stdio() -> None:
    """Line-buffer stdout/stderr so Agent / 日志能及时看到进度。"""
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(line_buffering=True)
            except Exception:
                pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_job_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(2).hex()


def job_dir_for(job_id: str) -> Path:
    return JOBS_ROOT / job_id


def status_path(job_dir: Path) -> Path:
    return job_dir / STATUS_NAME


def log_path(job_dir: Path) -> Path:
    return job_dir / LOG_NAME


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_status(job_dir: Path) -> Optional[dict[str, Any]]:
    p = status_path(job_dir)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def save_status(job_dir: Path, data: dict[str, Any]) -> None:
    data["updated_at"] = utc_now_iso()
    atomic_write_json(status_path(job_dir), data)


def write_latest(job_id: str) -> None:
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    (JOBS_ROOT / LATEST_NAME).write_text(job_id + "\n", encoding="utf-8")


def read_latest_job_id() -> Optional[str]:
    p = JOBS_ROOT / LATEST_NAME
    if not p.is_file():
        return None
    jid = p.read_text(encoding="utf-8").strip()
    return jid or None


def create_job(charts: list[str]) -> Path:
    jid = new_job_id()
    job_dir = job_dir_for(jid)
    job_dir.mkdir(parents=True, exist_ok=True)
    write_latest(jid)
    save_status(
        job_dir,
        {
            "job_id": jid,
            "pid": os.getpid(),
            "state": "queued",
            "started_at": utc_now_iso(),
            "finished_at": None,
            "total": len(charts),
            "index": 0,
            "current_chart": None,
            "charts": list(charts),
            "results": [],
            "message": None,
            "cancel_requested": False,
        },
    )
    return job_dir


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def mark_dead_if_needed(st: dict[str, Any], job_dir: Path) -> dict[str, Any]:
    state = str(st.get("state") or "")
    pid = st.get("pid")
    if state in ACTIVE_STATES and isinstance(pid, int) and not pid_alive(pid):
        if st.get("cancel_requested"):
            st["state"] = "cancelled"
            st["message"] = "任务已中断"
        else:
            st["state"] = "crashed"
            st["message"] = "工作进程已退出，任务未正常结束"
        st["finished_at"] = utc_now_iso()
        save_status(job_dir, st)
    return st


def find_active_job() -> Optional[tuple[Path, dict[str, Any]]]:
    """Return the latest job if it is still queued/running with a live pid."""
    job_dir = resolve_job_dir("")
    if job_dir is None:
        return None
    st = load_status(job_dir)
    if st is None:
        return None
    st = mark_dead_if_needed(st, job_dir)
    state = str(st.get("state") or "")
    pid = st.get("pid")
    if state in ACTIVE_STATES and isinstance(pid, int) and pid_alive(pid):
        return job_dir, st
    return None


def print_busy_error(st: dict[str, Any]) -> None:
    jid = st.get("job_id") or "?"
    script = SCRIPT_DIR / "sync_chart.py"
    progress = f"{st.get('index', 0)}/{st.get('total', 0)}"
    current = st.get("current_chart")
    extra = f"  当前: {current}" if current else ""
    print("错误: 已有同步任务正在运行，拒绝新的提交。", file=sys.stderr)
    print(f"job_id: {jid}", file=sys.stderr)
    print(f"状态: {st.get('state')}", file=sys.stderr)
    print(f"pid: {st.get('pid')}", file=sys.stderr)
    print(f"进度: {progress}{extra}", file=sys.stderr)
    print("请等待当前任务完成后再提交。", file=sys.stderr)
    print(f"查询: python3 {script} --status {jid}", file=sys.stderr)
    print("若需插队（先中断当前任务，再提交新任务）:", file=sys.stderr)
    print(f"  python3 {script} --cancel", file=sys.stderr)


def tail_text(path: Path, *, max_lines: int = 20) -> str:
    if not path.is_file():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-max_lines:])


def format_status(st: dict[str, Any], job_dir: Path) -> str:
    results = st.get("results") if isinstance(st.get("results"), list) else []
    ok = [r for r in results if isinstance(r, dict) and r.get("outcome") == "ok"]
    skipped = [r for r in results if isinstance(r, dict) and r.get("outcome") == "skip"]
    failed = [r for r in results if isinstance(r, dict) and r.get("outcome") == "fail"]
    lines = [
        f"job_id: {st.get('job_id', job_dir.name)}",
        f"状态: {st.get('state', '?')}",
        f"pid: {st.get('pid', '-')}",
        f"进度: {st.get('index', 0)}/{st.get('total', 0)}"
        + (f"  当前: {st['current_chart']}" if st.get("current_chart") else ""),
        f"成功: {len(ok)}  跳过: {len(skipped)}  失败: {len(failed)}",
    ]
    if st.get("message"):
        lines.append(f"说明: {st['message']}")
    if ok:
        lines.append("PR:")
        for r in ok:
            url = r.get("pr_url") or "(无 URL)"
            lines.append(f"  {r.get('name')}: {url}")
    if skipped:
        lines.append("跳过:")
        for r in skipped:
            lines.append(f"  {r.get('name')}: {r.get('error') or '无差异'}")
    if failed:
        lines.append("失败:")
        for r in failed:
            lines.append(f"  {r.get('name')}: {r.get('error') or 'unknown'}")
    log_tail = tail_text(log_path(job_dir))
    if log_tail:
        lines.append("最近日志:")
        lines.append(log_tail)
    lines.append(f"status: {status_path(job_dir)}")
    lines.append(f"log: {log_path(job_dir)}")
    return "\n".join(lines)


def resolve_job_dir(job_id: str) -> Optional[Path]:
    jid = (job_id or "").strip()
    if not jid:
        jid = read_latest_job_id() or ""
    if not jid:
        return None
    d = job_dir_for(jid)
    return d if d.is_dir() else None


def cmd_status(job_id: str) -> int:
    job_dir = resolve_job_dir(job_id)
    if job_dir is None:
        print("错误: 没有可查询的任务。请先提交同步，或指定有效的 JOB_ID。", file=sys.stderr)
        return 1
    st = load_status(job_dir)
    if st is None:
        print(f"错误: 找不到状态文件: {status_path(job_dir)}", file=sys.stderr)
        return 1
    st = mark_dead_if_needed(st, job_dir)
    print(format_status(st, job_dir))
    state = str(st.get("state") or "")
    if state == "done":
        return 0
    if state in ("failed", "crashed", "cancelled"):
        return 1
    return 0


def cmd_cancel(job_id: str) -> int:
    """Ask the active worker to stop after the current chart, then wait briefly."""
    job_dir = resolve_job_dir(job_id)
    if job_dir is None:
        print("错误: 没有可中断的任务。", file=sys.stderr)
        return 1
    st = load_status(job_dir)
    if st is None:
        print(f"错误: 找不到状态文件: {status_path(job_dir)}", file=sys.stderr)
        return 1
    st = mark_dead_if_needed(st, job_dir)
    state = str(st.get("state") or "")
    if state not in ACTIVE_STATES:
        print(f"当前没有运行中的任务（状态: {state}）。", file=sys.stderr)
        return 1

    jid = st.get("job_id") or job_dir.name
    st["cancel_requested"] = True
    st["message"] = "已请求中断，将在当前 chart 结束后停止"
    save_status(job_dir, st)

    pid = st.get("pid")
    if isinstance(pid, int) and pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError as e:
            print(f"错误: 无法向 pid {pid} 发送信号: {e}", file=sys.stderr)
            return 1
        print(f"已请求中断 job {jid}（pid {pid}）。")
        print("当前正在处理的 chart 会跑完，随后停止。")
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if not pid_alive(pid):
                break
            time.sleep(0.5)
        st = load_status(job_dir) or st
        st = mark_dead_if_needed(st, job_dir)
        if not pid_alive(pid) and str(st.get("state") or "") in ACTIVE_STATES:
            st["state"] = "cancelled"
            st["finished_at"] = utc_now_iso()
            st["current_chart"] = None
            st["message"] = "任务已中断"
            save_status(job_dir, st)
        print(format_status(st, job_dir))
        if str(st.get("state") or "") == "cancelled" or not pid_alive(pid):
            print("可以提交新任务了。")
            return 0
        print("工作进程仍在收尾；请稍后 python3 sync_chart.py --status 确认已停止，再提交新任务。")
        return 0

    st["state"] = "cancelled"
    st["finished_at"] = utc_now_iso()
    st["current_chart"] = None
    st["message"] = "任务已中断"
    save_status(job_dir, st)
    print(format_status(st, job_dir))
    print("可以提交新任务了。")
    return 0


class RepoLock:
    """Non-blocking exclusive lock so two syncs cannot share the prod worktree."""

    def __init__(self, path: Path = LOCK_PATH):
        self.path = path
        self._fd: Optional[Any] = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = open(self.path, "a+", encoding="utf-8")
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fd.close()
            return False
        fd.seek(0)
        fd.truncate()
        fd.write(str(os.getpid()) + "\n")
        fd.flush()
        self._fd = fd
        return True

    def release(self) -> None:
        fd = self._fd
        self._fd = None
        if fd is None:
            return
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        finally:
            fd.close()


_SKIP_FLAGS = frozenset({"--detach", "--background", "--foreground", "--cancel"})


def child_argv(script: Path, job_dir: Path) -> list[str]:
    rest: list[str] = []
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a in _SKIP_FLAGS:
            i += 1
            continue
        if a == "--job-dir":
            i += 2
            continue
        if a.startswith("--job-dir="):
            i += 1
            continue
        rest.append(a)
        i += 1
    return [
        sys.executable,
        "-u",
        str(script),
        *rest,
        "--foreground",
        "--job-dir",
        str(job_dir),
    ]


def spawn_detached_worker(script: Path, job_dir: Path) -> int:
    """Start a new session so Agent 超时杀进程组时不会带走后台任务。"""
    cmd = child_argv(script, job_dir)
    log_f = open(log_path(job_dir), "w", encoding="utf-8", buffering=1)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    # Preserve in-process sys.path (e.g. run_sync.py inserting site-packages).
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    proc = subprocess.Popen(
        cmd,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        cwd=str(Path.cwd()),
        env=env,
        close_fds=True,
    )
    return proc.pid


def print_detach_banner(job_dir: Path, pid: int) -> None:
    jid = job_dir.name
    print("批量同步已转入后台运行（避免 Agent 调用超时）。")
    print(f"job_id: {jid}")
    print(f"pid: {pid}")
    print(f"status: {status_path(job_dir)}")
    print(f"log: {log_path(job_dir)}")
    print("查询进度（请用短命令轮询，不要再次提交整批同步）:")
    print(f"  python3 {SCRIPT_DIR / 'sync_chart.py'} --status {jid}")
    print("后台启动成功 ≠ 同步完成；以 --status 的状态与 PR 列表为准。")
