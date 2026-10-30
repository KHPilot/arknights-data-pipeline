"""Create replay fixtures, recording content-equivalent local baseline ZIPs."""
import argparse,json,subprocess
from pathlib import Path
from lab import digest

p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);args=p.parse_args();r=args.root
releases=json.loads((r/'old-chain/releases.json').read_text());assets={};receipts={}
cache=json.loads((r/'equivalent-baseline-cache.json').read_text())
for release in releases:
 for asset in release['assets']:
  name=asset['name'];path=r/'old-chain'/release['tag_name']/name
  equivalent=False
  if release['tag_name'].startswith('images-baseline-'):
   path=r/'equivalent-baseline'/name
   if name in [x['file'].split('/')[-1] for x in cache.values()]:
    evidence=next(x for x in cache.values() if Path(x['file']).name==name)
    if digest(path)!=evidence['sha256']:raise ValueError('Equivalent cache changed')
    equivalent=True
  elif release['tag_name']=='images-26-08-03-23-34-20_a745fc':
   path=r/'baseline-index/index.json' if name=='index.json' else r/'equivalent-baseline'/name
  if not path.exists():
   path.parent.mkdir(parents=True,exist_ok=True)
   subprocess.run(['gh','release','download',release['tag_name'],'-R','3aKHP/arknights-data-pipeline','--pattern',name,'--dir',str(path.parent)],check=True)
  sha=digest(path)
  if not equivalent and (asset['size']!=path.stat().st_size or asset['digest']!='sha256:'+sha):raise ValueError('Seed asset mismatch '+name)
  url=asset['browser_download_url'].replace('3aKHP/arknights-data-pipeline','khpilot/arknights-data-pipeline')
  asset['browser_download_url']=url;assets[url]=path.relative_to(r).as_posix()
  receipts[assets[url]]={'sha256':sha,'bytes':path.stat().st_size,'content_equivalent_cache':equivalent,'official_zip_digest':asset['digest']}
for label,target in [('P','26-10-14-13-45-10-99469376'),('L','26-10-22-14-02-11-51447808')]:
 selected=[x for x in releases if x['tag_name'].removeprefix('images-baseline-').removeprefix('images-')<=target]
 (r/('old-'+label+'-fixture.json')).write_text(json.dumps({'source':'KHPilot/arknights-data-pipeline','releases':selected,'assets':assets},indent=2))
(r/'old-chain-input-receipts.json').write_text(json.dumps(receipts,indent=2))
print('Old-chain fixtures and provenance receipts ready')
