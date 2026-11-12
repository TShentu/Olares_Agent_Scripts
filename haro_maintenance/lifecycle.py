#!/usr/bin/env python3
"""Single-app GitHub release jobs; durable enqueue, scheduled execution and follow-up."""
from __future__ import annotations
import argparse
import copy
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import yaml

from config import Config, Error
from github_api import GitHub
from gitops import app_name

TERMINAL = {'merged', 'rejected', 'satisfied', 'superseded'}


def record_id():
    return 'app-' + uuid.uuid4().hex[:16]


def active_records(state, app):
    records = list(state.get('jobs', {}).values()) + list(state.get('tasks', {}).values())
    return [x for x in records if x.get('app') == app and x.get('status') not in TERMINAL]


def summary(x):
    return {k: x[k] for k in ('id', 'operation', 'app', 'source', 'target', 'source_sha', 'status',
        'actor', 'created_at', 'updated_at', 'url', 'error', 'children', 'followup_status', 'warnings',
        'merge_sha', 'title', 'version', 'previous_version', 'consistency', 'followup_error', 'backport_after_merge', 'planned_base', 'diff') if k in x}


def task_by_id(state, ident):
    for task in list(state.get('tasks', {}).values()) + state.get('task_history', []):
        if task.get('id') == ident:
            return task
    return None


def enqueue_backport(r, app, source, target, sha, parent=None):
    """Daily discovery and post-merge dispatch use the same app/target record."""
    key = target + '/' + app
    prior = r.state['tasks'].get(key)
    event = r.git.text('log', '-1', '--format=%H', sha, '--', app)
    if prior:
        prior.setdefault('id', record_id())
        same = prior.get('source') == source and r.git.tree(prior['source_sha'], app) == r.git.tree(sha, app)
        if same:
            if parent and parent not in prior.setdefault('parents', []):
                prior['parents'].append(parent)
            return prior
        # Never repurpose an in-flight PR or erase an older task's audit record.
        if prior.get('status') not in TERMINAL and prior.get('pr'):
            prior['next_event'] = {'app': app, 'source': source, 'target': target, 'source_sha': sha, 'parent': parent}
            return prior
        archived = copy.deepcopy(prior)
        if archived.get('status') not in TERMINAL:
            archived['previous_status'] = archived.get('status')
            archived['status'] = 'superseded'
        r.state.setdefault('task_history', []).append(archived)
    task = {'id': record_id(), 'operation': 'backport', 'app': app, 'source': source,
        'source_sha': sha, 'event': event, 'target': target, 'status': 'pending',
        'retry_count': 0, 'created_at': time.time(), 'parents': [parent] if parent else []}
    # Each new event has its own branch, including events after a rejected PR.
    task['branch'] = f"haro/backport/{source}/{app}/{task['id']}"
    r.state['tasks'][key] = task
    return task


def blocks_maintenance(r, task):
    return [summary(j) for j in r.state.get('jobs', {}).values()
            if j['app'] == task['app'] and j['target'] == task['target']
            and j.get('pr') and j.get('status') not in TERMINAL]


def followup(r, job):
    if job.get('status') != 'merged' or not job.get('backport_after_merge'):
        return
    try:
        env = r.cfg.environment(job['target'])
        head = r.git.fetch(env.upstream, env.branch, 'refs/haro/' + env.name + '/upstream')
        merged = job.get('merge_sha')
        if not merged or not r.git.ancestor(merged, head):
            raise Error('merged commit unavailable in target history; engineer must inspect')
        # Start at the actual merge, but never overwrite a later production update.
        source = merged
        if r.git.tree(head, job['app']) != r.git.tree(merged, job['app']):
            source = head
            job['followup_superseded_by'] = head
        children = job.setdefault('children', {})
        for name in job['backport_after_merge']:
            child = enqueue_backport(r, job['app'], env.name, name, source, job['id'])
            children[name] = child['id']
        job['followup_status'] = 'dispatched'
        job.pop('followup_error', None)
    except (Error, OSError, ValueError) as ex:
        job['followup_status'] = 'failed'
        job['followup_error'] = str(ex) if isinstance(ex, Error) else type(ex).__name__
    r.save()


def tick(r):
    """Heartbeat: process queued jobs; merge/close detection never depends on chat."""
    jobs = r.state.setdefault('jobs', {})
    for job in list(jobs.values()):
        if job['status'] == 'needs_attention' and job.get('pr'):
            try:
                pr = r.api.get_pr(r.cfg.environment(job['target']).repo, job['pr'])
                if pr['state'] == 'closed':
                    job['status'] = 'merged' if pr.get('merged_at') else 'rejected'
                    job['merge_sha'] = pr.get('merge_commit_sha')
                    job.pop('error', None)
                    r.save()
            except Error as ex:
                job['error'] = str(ex)
        if job['status'] not in TERMINAL and job['status'] != 'needs_attention':
            competing = [t for t in r.state['tasks'].values() if t['app'] == job['app']
                         and t['target'] == job['target'] and t.get('pr') and t['status'] not in TERMINAL]
            older = [j for j in jobs.values() if j['id'] != job['id'] and j['app'] == job['app']
                     and j['target'] == job['target'] and j['created_at'] < job['created_at']
                     and j['status'] not in TERMINAL and j['status'] != 'needs_attention']
            if competing or older:
                job.update(status='waiting', warnings=[summary(t) for t in competing + older])
            else:
                try:
                    job.pop('error', None)
                    r.process(job)
                except (Error, OSError, ValueError) as ex:
                    job['error'] = str(ex) if isinstance(ex, Error) else type(ex).__name__
                    job['retry_count'] = job.get('retry_count', 0) + 1
                    job['status'] = 'needs_attention' if job['retry_count'] >= 3 else 'blocked'
            job['updated_at'] = time.time()
            r.save()
        # A failed creation of a follow-up is retried by subsequent heartbeats/daily runs.
        followup(r, job)


def audit(r):
    """Persist visibility of failed follow-ups even after discovery checkpoint advances."""
    for job in r.state.get('jobs', {}).values():
        rows = []
        if job.get('status') == 'merged':
            for name in job.get('backport_after_merge', []):
                child = task_by_id(r.state, job.get('children', {}).get(name))
                current = r.state['tasks'].get(name + '/' + job['app'])
                if current and child and current['id'] != child['id']:
                    child = current  # newer daily event supersedes the earlier follow-up
                src = r.heads[job['target']]
                dst = r.heads[name]
                equal = r.git.tree(src, job['app']) == r.git.tree(dst, job['app'])
                state = 'consistent' if equal else ('awaiting_review' if child and child.get('status') == 'awaiting_review' else 'needs_engineer')
                rows.append({'target': name, 'status': state, 'source_sha': src, 'target_sha': dst,
                             'job': child.get('id') if child else None, 'url': child.get('url') if child else None,
                             'error': child.get('error') if child else job.get('followup_error')})
            job['consistency'] = rows
            if rows:
                job['followup_status'] = 'complete' if all(x['status'] == 'consistent' for x in rows) else (
                    'needs_engineer' if any(x['status'] == 'needs_engineer' for x in rows) else 'awaiting_review')
        r.report.setdefault('jobs', []).append(summary(job))
    r.save()


def chart_version(git, commit, app):
    """Chart version of an app at a commit; None when it cannot be read."""
    raw = git.file(commit, app + '/Chart.yaml')
    if not raw:
        return None
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None
    return str(data.get('version')) if isinstance(data, dict) and data.get('version') else None


def identical_release(r, app, source, target, source_sha):
    """Block a release whose source and target already hold identical app content."""
    try:
        r.fetch(sync_forks=False, names={source, target})
        target_head = r.heads.get(target)
        source_tree = r.git.tree(source_sha, app)
        target_tree = r.git.tree(target_head, app) if target_head else None
        if not source_tree or source_tree != target_tree:
            return None
        versions = {'source': chart_version(r.git, source_sha, app),
                    'target': chart_version(r.git, target_head, app)}
    except (Error, OSError):
        # An unreadable comparison never blocks a submission; the worker re-checks.
        return None
    return {'status': 'blocked', 'reason': 'identical', 'app': app,
            'source': source, 'target': target, 'source_sha': source_sha, 'target_sha': target_head,
            'versions': versions,
            'message': 'source and target already contain identical app content; nothing to submit'}


def submit(r, operation, app, source, actor, title=None, ack=None, request_id=None):
    app_name(app)
    route = r.cfg.route(operation, source)
    r.open()
    try:
        jobs = r.state.setdefault('jobs', {})
        if request_id:
            found = next((j for j in jobs.values() if j.get('request_id') == request_id), None)
            if found:
                if (found['operation'], found['app'], found['source'], found['actor'], found.get('requested_title')) != (operation, app, route['source'], actor, title):
                    raise Error('request id already belongs to a different operation')
                return {'job': summary(found), 'duplicate': True}
        # Repository HEAD captured at submission; worker never silently switches source.
        env = r.cfg.environment(route['source'])
        sha = r.api.request('GET', f'repos/{env.repo}/commits/{env.branch}')['sha']
        # Preflight guard: an identical source/target pair has nothing to submit.
        blocked = identical_release(r, app, route['source'], route['target'], sha)
        if blocked:
            return blocked
        duplicate = next((j for j in jobs.values() if j['operation'] == operation and j['app'] == app
                          and j['source'] == route['source'] and j['target'] == route['target']
                          and j['source_sha'] == sha and j['status'] not in {'rejected', 'needs_attention'}), None)
        if duplicate:
            return {'job': summary(duplicate), 'duplicate': True}
        active = active_records(r.state, app)
        for item in active:
            item.setdefault('id', record_id())
        warnings = [summary(x) for x in active]
        if r.apply and active and set(ack or []) != {x['id'] for x in active}:
            return {'status': 'confirmation_required', 'warnings': warnings,
                    'message': 'active app operations exist; review and acknowledge the current job IDs'}
        job = {'id': record_id(), 'operation': operation, 'app': app, **route,
               'source_sha': sha, 'event': sha, 'actor': actor, 'request_id': request_id,
               'status': 'queued', 'created_at': time.time(), 'requested_title': title, 'warnings': warnings}
        job['branch'] = f"haro/{operation}/{app}/{job['id']}"
        if r.apply:
            jobs[job['id']] = job
            r.save()
        else:
            r.fetch(sync_forks=False)
            r.process(job)
        return {'job': summary(job), 'warnings': warnings, 'mode': 'apply' if r.apply else 'dry-run'}
    finally:
        r.close()


def query(cfg, api, job_id=None, app=None):
    from github_api import RemoteState
    rec = cfg.raw['recovery']
    state = RemoteState(api, rec['repository'], rec['branch']).load() or {}
    records = list(state.get('jobs', {}).values()) + list(state.get('tasks', {}).values()) + state.get('task_history', [])
    records = [x for x in records if (not app or x.get('app') == app) and (not job_id or x.get('id') == job_id)]
    if job_id and not records:
        raise Error('job not found')
    return {'jobs': [summary(x) for x in records], 'environments': [e.name for e in cfg.envs]}


def main():
    from maintenance import Runner
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', required=True)
    subs = p.add_subparsers(dest='command', required=True)
    for name in ('promote', 'publish'):
        s = subs.add_parser(name)
        s.add_argument('app'); s.add_argument('--from', dest='source')
        s.add_argument('--title'); s.add_argument('--actor', required=True)
        s.add_argument('--request-id'); s.add_argument('--ack-active')
        s.add_argument('--apply', action='store_true')
    s = subs.add_parser('job'); s.add_argument('id')
    s = subs.add_parser('jobs'); s.add_argument('--app')
    a = p.parse_args()
    try:
        cfg = Config(a.config); api = GitHub(cfg.token())
        if a.command in {'job', 'jobs'}:
            result = query(cfg, api, getattr(a, 'id', None), getattr(a, 'app', None))
        else:
            if a.apply and not cfg.maintenance.get('enabled'):
                raise Error('maintenance is disabled')
            cfg.root.mkdir(parents=True, exist_ok=True)
            with (cfg.root/'run.lock').open('w') as lock:
                try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError: raise Error('maintenance busy; retry submission shortly')
                result = submit(Runner(cfg, api, a.apply), a.command, a.app, a.source, a.actor,
                                a.title, a.ack_active.split(',') if a.ack_active else None, a.request_id)
            if a.apply and result.get('job', {}).get('status') == 'queued':
                # Durable enqueue happened first. Heartbeat recovers if launch fails or lock is busy.
                try:
                    with (cfg.root/'job-worker.log').open('ab') as log:
                        subprocess.Popen([sys.executable, str(Path(__file__).with_name('maintenance.py')),
                            '--config', str(cfg.path), 'refresh', '--apply'], stdout=log, stderr=log,
                            start_new_session=True, stdin=subprocess.DEVNULL)
                except OSError:
                    result['worker'] = 'queued; heartbeat will execute within its next interval'
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (Error, OSError, ValueError) as ex:
        print(json.dumps({'error': str(ex) if isinstance(ex, Error) else type(ex).__name__}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
