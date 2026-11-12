"""Small GitHub client with bounded read retries and explicit write reconciliation."""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from config import Error


class CheckpointError(Error):
    """A failed state write stops all further mutations in this run."""


class ApiError(Error):
    def __init__(self, status, endpoint):
        self.status = status
        super().__init__(f"GitHub HTTP {status}: {endpoint.split('?')[0]}")


class GitHub:
    def __init__(self, token):
        self.token = token
        self.memberships = {}

    def request(self, method, path, data=None):
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request("https://api.github.com/" + path, data=body, method=method,
            headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
                     "User-Agent": "haro-maintenance"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=45) as response:
                    content = response.read()
                    return json.loads(content) if content else {}
            except urllib.error.HTTPError as ex:
                if method == "GET" and ex.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise ApiError(ex.code, path) from None
            except (urllib.error.URLError, TimeoutError):
                if method == "GET" and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise Error("GitHub transport error; write outcome may be unknown") from None

    def is_org_member(self, org, login):
        key = (org.casefold(), login.casefold())
        if key not in self.memberships:
            try:
                member = self.request("GET", "user/memberships/orgs/" + urllib.parse.quote(org, safe=""))
            except ApiError as ex:
                if ex.status == 404:
                    self.memberships[key] = False
                    return False
                raise Error("cannot verify organization owner membership: " + str(ex)) from None
            self.memberships[key] = (member.get("state") == "active" and
                (member.get("user") or {}).get("login", "").casefold() == login.casefold() and
                (member.get("organization") or {}).get("login", "").casefold() == org.casefold())
        return self.memberships[key]

    def pages(self, path):
        result = []
        for page in range(1, 101):
            data = self.request("GET", path + ("&" if "?" in path else "?") + f"per_page=100&page={page}")
            result.extend(data)
            if len(data) < 100:
                return result
        raise Error("GitHub pagination exceeds safety bound")

    def check_environment(self, e):
        fork = self.request("GET", "repos/" + e.fork)
        if not fork.get("permissions", {}).get("push"):
            raise Error("no fork push permission: " + e.name)
        upstream = self.request("GET", "repos/" + e.repo)
        fork_root = (fork.get("source") or fork).get("full_name", "").lower()
        upstream_root = (upstream.get("source") or upstream).get("full_name", "").lower()
        if fork_root != upstream_root:
            raise Error("fork and upstream are not in the same network: " + e.name)

    def pulls(self, e, state="open"):
        return self.pages("repos/" + e.repo + "/pulls?" + urllib.parse.urlencode({"state": state, "base": e.branch}))

    def get_pr(self, repo, number):
        return self.request("GET", f"repos/{repo}/pulls/{number}")

    def status(self, repo, head, state, description):
        return self.request("POST", f"repos/{repo}/statuses/{head}",
                            {"state": state, "context": "haro/source-snapshot", "description": description[:140]})


class RemoteState:
    """Contents API compare-and-swap; state lives on a non-publication branch."""
    def __init__(self, api, repo, branch="haro-maintenance-state", path="haro-state.json"):
        self.api, self.repo, self.branch, self.path = api, repo, branch, path
        self.sha = None
        self.uncertain = False

    def load(self):
        endpoint = f"repos/{self.repo}/contents/{self.path}?ref=" + urllib.parse.quote(self.branch, safe="")
        try:
            result = self.api.request("GET", endpoint)
        except ApiError as ex:
            if ex.status == 404:
                return None
            raise
        self.sha = result["sha"]
        return json.loads(base64.b64decode(result["content"]))

    def initialize_branch(self):
        endpoint = f"repos/{self.repo}/git/ref/heads/" + urllib.parse.quote(self.branch, safe="")
        try:
            self.api.request("GET", endpoint)
            return
        except ApiError as ex:
            if ex.status != 404:
                raise
        info = self.api.request("GET", "repos/" + self.repo)
        ref = self.api.request("GET", f"repos/{self.repo}/git/ref/heads/" + info["default_branch"])
        try:
            self.api.request("POST", f"repos/{self.repo}/git/refs", {"ref": "refs/heads/" + self.branch, "sha": ref["object"]["sha"]})
        except Error:
            # A lost response or concurrent initializer must be reconciled.
            self.api.request("GET", endpoint)

    def save(self, value):
        payload = {"message": "Update Haro maintenance checkpoint", "branch": self.branch,
                   "content": base64.b64encode(json.dumps(value, sort_keys=True).encode()).decode()}
        if self.sha:
            payload["sha"] = self.sha
        try:
            result = self.api.request("PUT", f"repos/{self.repo}/contents/{self.path}", payload)
            self.sha = result["content"]["sha"]
        except Error as cause:
            self.uncertain = True
            old_sha = self.sha
            try:
                actual = self.load()
            except Error:
                raise CheckpointError("checkpoint reconciliation failed; stop this run") from None
            if actual != value:
                self.sha = old_sha
                raise CheckpointError("remote checkpoint changed or write is uncertain; stop this run (" + str(cause) + ")") from None
            self.uncertain = False
