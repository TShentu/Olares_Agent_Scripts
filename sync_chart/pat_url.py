"""
Cross-platform GitHub HTTPS URLs with embedded PAT.

Uses patterns documented for non-interactive Git over HTTPS (no macOS keychain / GCM UI).
Primary: ``https://oauth2:<PAT>@github.com/owner/repo.git``
See: GitHub docs on using a PAT from the command line.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import quote, urlsplit

__all__ = (
    "github_https_repo_path_tail",
    "github_authenticated_https_url",
)


def github_https_repo_path_tail(remote_url: str) -> Optional[str]:
    """Return ``owner/repo`` for ``https://github.com/owner/repo(.git)``, else None."""
    raw = remote_url.strip()
    if raw.startswith("git@"):
        return None
    u = urlsplit(raw)
    if u.scheme != "https":
        return None
    host = (u.hostname or "").lower()
    if host not in ("github.com", "www.github.com"):
        return None
    path = u.path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    tail = path.lstrip("/")
    if not tail or "/" not in tail:
        return None
    return tail


def github_authenticated_https_url(
    remote_url: str,
    token: str,
    *,
    style: str = "oauth2",
) -> Optional[str]:
    """
    Build an HTTPS URL with embedded PAT for ``git fetch`` / ``git push``.

    ``style``:
    - ``oauth2`` — ``https://oauth2:<PAT>@github.com/...`` (default, widely documented)
    - ``x_access_token`` — ``https://x-access-token:<PAT>@github.com/...``
    - ``token_user`` — ``https://<PAT>@github.com/...`` (username-only form)
    """
    tail = github_https_repo_path_tail(remote_url)
    if not tail:
        return None
    qt = quote(token, safe="")
    if style == "oauth2":
        return f"https://oauth2:{qt}@github.com/{tail}.git"
    if style == "x_access_token":
        return f"https://x-access-token:{qt}@github.com/{tail}.git"
    if style == "token_user":
        return f"https://{qt}@github.com/{tail}.git"
    raise ValueError(f"unknown style: {style!r}")
