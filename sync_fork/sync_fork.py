#!/usr/bin/env python3
"""
Sync fork repos with upstream main.
Drops any diverged commits on fork main (force push to match upstream).
Usage: python3 sync_fork.py [--dry-run]
"""

import subprocess
import sys
import re
from pathlib import Path

REPOS = [
    {
        "name": "static",
        "path": "/home/userdata/home/Code/Olares_Project/static",
        "upstream_branch": "upstream/main",
    },
    {
        "name": "Olares",
        "path": "/home/userdata/home/Code/Olares_Project/Olares",
        "upstream_branch": "upstream/main",
    },
    {
        "name": "apps",
        "path": "/home/userdata/home/Code/Olares_Project/apps",
        "upstream_branch": "upstream/main",
    },
    {
        "name": "terminus-apps",
        "path": "/home/userdata/home/Code/Olares_Project/terminus-apps",
        "upstream_branch": "upstream/main",
    },
]


def run(cmd, cwd=None, capture=True):
    result = subprocess.run(
        cmd, shell=True, cwd=cwd, capture_output=capture, text=capture
    )
    return result


def get_current_commit(repo_path, branch):
    """Get current commit hash for a branch."""
    result = run(f"git rev-parse {branch}", cwd=repo_path)
    return result.stdout.strip() if result.returncode == 0 else None


def sync_repo(repo):
    name = repo["name"]
    path = repo["path"]
    upstream_branch = repo["upstream_branch"]

    print(f"\n{'='*50}")
    print(f"Syncing {name}...")

    # Check if directory exists
    if not Path(path).exists():
        print(f"  [SKIP] Directory not found: {path}")
        return False, "directory not found"

    # Fetch upstream
    print(f"  Fetching upstream...")
    result = run("git fetch upstream", cwd=path)
    if result.returncode != 0:
        print(f"  [ERROR] git fetch failed: {result.stderr}")
        return False, result.stderr

    # Get upstream commit
    upstream_commit = get_current_commit(path, upstream_branch)
    upstream_short = upstream_commit[:8] if upstream_commit else "?"

    # Get fork main commit
    fork_commit = get_current_commit(path, "origin/main")
    fork_short = fork_commit[:8] if fork_commit else "?"

    print(f"  Upstream main: {upstream_short}")
    print(f"  Fork main:     {fork_short}")

    if upstream_commit == fork_commit:
        print(f"  [OK] Already in sync, nothing to do")
        return True, "already in sync"

    # Check for diverged commits (fork ahead of upstream)
    # Use git rev-list to count commits fork main is ahead of upstream
    result = run(
        f"git rev-list --count {upstream_branch}..origin/main",
        cwd=path
    )
    if result.returncode == 0:
        ahead = int(result.stdout.strip())
        print(f"  Fork ahead of upstream by {ahead} commits (will be dropped)")

    # Reset fork main to upstream
    print(f"  Resetting fork main to {upstream_branch}...")
    result = run(f"git reset --hard {upstream_branch}", cwd=path)
    if result.returncode != 0:
        print(f"  [ERROR] git reset failed: {result.stderr}")
        return False, result.stderr

    # Force push to origin
    print(f"  Force pushing to origin/main...")
    result = run("git push --force origin main", cwd=path)
    if result.returncode != 0:
        print(f"  [ERROR] git push failed: {result.stderr}")
        return False, result.stderr

    new_commit = get_current_commit(path, "HEAD")
    print(f"  [OK] Synced successfully: {new_commit[:8]}")
    return True, new_commit


def main():
    dry_run = "--dry-run" in sys.argv

    print(f"=== Fork Sync {'(DRY RUN)' if dry_run else ''} ===")
    print(f"Repos: {[r['name'] for r in REPOS]}")

    results = {}
    for repo in REPOS:
        success, detail = sync_repo(repo)
        results[repo["name"]] = {"success": success, "detail": detail}

    print(f"\n{'='*50}")
    print("SUMMARY:")
    for name, r in results.items():
        status = "✅ OK" if r["success"] else f"❌ FAIL: {r['detail']}"
        print(f"  {name}: {status}")

    failed = [n for n, r in results.items() if not r["success"]]
    if failed:
        print(f"\nFailed repos: {failed}")
        sys.exit(1)
    else:
        print(f"\nAll repos synced successfully!")


if __name__ == "__main__":
    main()