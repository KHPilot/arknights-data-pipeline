"""Publish verified lab assets through an explicitly selected fork, never clobber."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from lab import digest, verify


def run(args, env):
    return subprocess.check_output(['gh', *args], env=env, text=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', required=True)
    p.add_argument('--dist', required=True, type=Path)
    p.add_argument('--kind', choices=['baseline','delta'], required=True)
    p.add_argument('--stage', choices=['draft','verify','activate'], required=True)
    p.add_argument('--credential-ssh', required=True)
    args = p.parse_args()
    verify(args.dist)
    token = subprocess.check_output(['ssh','-o','BatchMode=yes',args.credential_ssh,'gh auth token'], text=True).strip()
    env = {**os.environ, 'GH_TOKEN':token}
    env.pop('GITHUB_TOKEN',None)
    owner = json.loads(run(['api','user'],env))['login']
    repo = json.loads(run(['api','repos/'+args.repo],env))
    if repo['owner']['login'].lower() != owner.lower() or not repo.get('fork'):
        raise ValueError('Publication target must be a fork owned by the active lab identity')
    index = json.loads((args.dist/'index.json').read_text())
    version = index['baselineVersion'] if args.kind=='baseline' else index['currentVersion']
    tag = ('images-baseline-' if args.kind=='baseline' else 'images-') + version
    names = list(index['shards'].values()) if args.kind=='baseline' else [f'images-delta-{version}.zip']
    names += ['index.json','images-build-state.json']
    files = [args.dist/name for name in names]
    for path in files:
        if not path.is_file():raise ValueError('Missing asset '+path.name)
    # List endpoint distinguishes an absent tag from auth/network failures.
    pages = run(['api','--paginate','--slurp',f'repos/{args.repo}/releases?per_page=100'],env)
    existing = next((r for page in json.loads(pages) for r in page if r['tag_name']==tag),None)
    if args.stage=='draft':
        if existing and not existing['draft']:raise ValueError('Release is already public')
        if existing is None:
            run(['release','create',tag,'-R',args.repo,'--draft','--latest=false','--title',tag,
                 '--notes','Isolated re-baseline experiment candidate.'],env)
            existing=json.loads(run(['api',f'repos/{args.repo}/releases/tags/{tag}'],env))
        assets={a['name']:a for a in existing['assets']}
        for path in files:
            if path.name in assets:
                if assets[path.name].get('digest')!='sha256:'+digest(path):raise ValueError('Existing asset differs')
            else:
                run(['release','upload',tag,'-R',args.repo,str(path)],env)
    existing=json.loads(run(['api',f'repos/{args.repo}/releases/tags/{tag}'],env))
    assets={a['name']:a for a in existing['assets']}
    if set(assets)!=set(names):raise ValueError('Release asset set differs')
    for path in files:
        a=assets[path.name]
        if a['size']!=path.stat().st_size or a.get('digest')!='sha256:'+digest(path):
            raise ValueError('Remote asset mismatch '+path.name)
    if args.stage=='activate':
        run(['release','edit',tag,'-R',args.repo,'--draft=false','--latest=false'],env)
    print(json.dumps({'repo':args.repo,'tag':tag,'stage':args.stage,'assets_verified':len(files)}))

if __name__=='__main__':main()
