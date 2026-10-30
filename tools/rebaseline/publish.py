"""Publish verified lab assets through an explicitly selected fork, never clobber."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from lab import digest, verify


def run(args, env, input_text=None):
    return subprocess.check_output(['gh', *args], env=env, text=True, input=input_text)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', required=True)
    p.add_argument('--dist', required=True, type=Path)
    p.add_argument('--previous', type=Path)
    p.add_argument('--kind', choices=['baseline','delta'], required=True)
    p.add_argument('--stage', choices=['draft','verify','activate'], required=True)
    p.add_argument('--credential-ssh', required=True)
    p.add_argument('--release-id', type=int)
    args = p.parse_args()
    verify(args.dist, args.previous)
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
    if args.release_id is not None:
        existing = json.loads(run(['api',f'repos/{args.repo}/releases/{args.release_id}'],env))
        renamed_draft = existing['draft'] and existing['tag_name'].startswith('untagged-') and existing['name'] == tag
        if existing['tag_name'] != tag and not renamed_draft:
            raise ValueError('Release ID does not identify the requested candidate')
    else:
        pages = run(['api','--paginate','--slurp',f'repos/{args.repo}/releases?per_page=100'],env)
        existing = next((r for page in json.loads(pages) for r in page if r['tag_name']==tag),None)
    if args.stage=='draft':
        if existing and not existing['draft']:raise ValueError('Release is already public')
        if existing is None:
            existing = json.loads(run(['api','--method','POST',f'repos/{args.repo}/releases','--input','-'], env,
                json.dumps({'tag_name':tag, 'draft':True, 'make_latest':'false', 'name':tag,
                            'body':'Isolated re-baseline experiment candidate.'})))
        assets={a['name']:a for a in existing['assets']}
        for path in files:
            if path.name in assets:
                if assets[path.name].get('digest')!='sha256:'+digest(path):raise ValueError('Existing asset differs')
            else:
                run(['release','upload',existing['tag_name'],'-R',args.repo,str(path)],env)
    if existing is None:
        raise ValueError('Release does not exist')
    existing=json.loads(run(['api',f"repos/{args.repo}/releases/{existing['id']}"],env))
    assets={a['name']:a for a in existing['assets']}
    if set(assets)!=set(names):raise ValueError('Release asset set differs')
    for path in files:
        a=assets[path.name]
        if a['size']!=path.stat().st_size or a.get('digest')!='sha256:'+digest(path):
            raise ValueError('Remote asset mismatch '+path.name)
    if args.stage=='activate':
        published = json.loads(run(['api','--method','PATCH',f"repos/{args.repo}/releases/{existing['id']}",'--input','-'],env,
            json.dumps({'tag_name':tag,'target_commitish':existing['target_commitish'],'draft':False,'make_latest':'false'})))
        if published['draft'] or published['tag_name'] != tag:
            raise ValueError('Release activation was not confirmed')
    print(json.dumps({'repo':args.repo,'tag':tag,'stage':args.stage,'assets_verified':len(files),'release_id':existing['id']}))

if __name__=='__main__':main()
