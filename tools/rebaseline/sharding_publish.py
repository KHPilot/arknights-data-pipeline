"""Resume verified v2 publication in an explicitly selected account-owned fork."""
import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from lab import digest
from sharding import atomic_json, verify


def publish(dist, repo, credential_ssh, previous=None, fault=None):
    verify(dist, previous)
    token = subprocess.check_output(['ssh', '-o', 'BatchMode=yes', credential_ssh, 'gh auth token'], text=True).strip()
    env = dict(os.environ, GH_TOKEN=token); env.pop('GITHUB_TOKEN', None)
    def gh(args, body=None):
        for attempt in range(3):
            p = subprocess.run(['gh', *args], env=env, input=json.dumps(body) if body is not None else None,
                               text=True, capture_output=True)
            if p.returncode == 0:
                return json.loads(p.stdout) if p.stdout.strip() else None
            if attempt == 2: raise RuntimeError(p.stderr)
            time.sleep([5, 20][attempt])
    owner = gh(['api', 'user'])['login']
    info = gh(['api', 'repos/' + repo])
    if info['owner']['login'].lower() != owner.lower() or not info['fork']:
        raise ValueError('target must be active identity owned fork')
    index = json.loads((dist/'index.json').read_text())
    version = index['currentVersion']
    baseline = index['baselineVersion'] == version
    jobs = [('images2-baseline-' + version, index['baselineParts']), ('images2-' + version, [])] if baseline else [('images2-' + version, index['deltaParts'])]
    records = []
    for tag, parts in jobs:
        names = [x['file'] for x in parts] + ['index.json']
        path = dist/(tag + '.publication.json')
        release_id = json.loads(path.read_text())['release_id'] if path.exists() else None
        pages = gh(['api', '--paginate', '--slurp', f'repos/{repo}/releases?per_page=100'])
        release = next((r for page in pages for r in page if r['id'] == release_id or r['tag_name'] == tag), None)
        if release is None:
            release = gh(['api', '--method', 'POST', f'repos/{repo}/releases', '--input', '-'],
                         {'tag_name': tag, 'name': tag, 'draft': True, 'make_latest': 'false',
                          'body': 'Isolated second-phase automatic sharding experiment.'})
        if release['tag_name'] != tag and not (release['draft'] and release['name'] == tag and release['tag_name'].startswith('untagged-')):
            raise ValueError('release identity mismatch')
        atomic_json(path, {'repo': repo, 'tag': tag, 'release_id': release['id'], 'state': 'uploading'})
        endpoint = f"repos/{repo}/releases/{release['id']}"
        def assets():
            pages = gh(['api', '--paginate', '--slurp', endpoint + '/assets?per_page=100'])
            return {a['name']: a for page in pages for a in page}
        existing = assets()
        if set(existing) - set(names): raise ValueError('unexpected remote assets')
        for name in names:
            file = dist/name
            if name in existing and existing[name].get('state') == 'starter':
                if not release['draft']: raise ValueError('starter asset on public release')
                gh(['api', '--method', 'DELETE', f"repos/{repo}/releases/assets/{existing[name]['id']}"])
                existing = assets()
            if name not in existing:
                if not release['draft']: raise ValueError('incomplete public release')
                # Upload responses are not JSON. Reconcile an ambiguous successful upload before retry.
                for attempt in range(3):
                    p = subprocess.run(['gh', 'release', 'upload', release['tag_name'], '-R', repo, str(file)], env=env, text=True, capture_output=True)
                    existing = assets()
                    if name in existing and existing[name].get('state') != 'starter': break
                    if name in existing:
                        gh(['api', '--method', 'DELETE', f"repos/{repo}/releases/assets/{existing[name]['id']}"])
                        existing = assets()
                    if attempt == 2: raise RuntimeError(p.stderr)
                    time.sleep([5, 20][attempt])
            if fault == 'after_upload': raise RuntimeError('injected interruption after upload')
            a = existing[name]
            if a['size'] != file.stat().st_size or a.get('digest') != 'sha256:' + digest(file):
                raise ValueError('remote content mismatch')
        existing = assets()
        if set(existing) != set(names): raise ValueError('incomplete asset set')
        if release['draft']:
            release = gh(['api', '--method', 'PATCH', endpoint, '--input', '-'],
                         {'tag_name': tag, 'target_commitish': release['target_commitish'], 'draft': False, 'make_latest': 'false'})
        if release['draft'] or release['tag_name'] != tag: raise ValueError('activation failed')
        receipt = {'repo': repo, 'tag': tag, 'release_id': release['id'], 'state': 'public', 'assets': len(names)}
        atomic_json(path, receipt); records.append(receipt)
        if fault == 'after_baseline' and tag.startswith('images2-baseline-'):
            raise RuntimeError('injected interruption after baseline activation')
    return records

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--dist', type=Path, required=True); p.add_argument('--repo', required=True)
    p.add_argument('--credential-ssh', required=True); p.add_argument('--previous', type=Path)
    a = p.parse_args()
    print(json.dumps(publish(a.dist, a.repo, a.credential_ssh, json.loads(a.previous.read_text()) if a.previous else None)))
