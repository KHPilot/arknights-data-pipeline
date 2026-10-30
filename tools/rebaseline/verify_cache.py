"""Verify local baseline ZIP content against a digest-verified official index."""
import argparse,hashlib,json,zipfile
from pathlib import Path
from lab import digest,save

p=argparse.ArgumentParser(description=__doc__)
for name in ['index','cache','releases','output']:p.add_argument('--'+name,type=Path,required=True)
a=p.parse_args();index=json.loads(a.index.read_text());releases=json.loads(a.releases.read_text())
sentinel=next(x for x in releases if x['tag_name']=='images-'+index['currentVersion'])
asset=next(x for x in sentinel['assets'] if x['name']=='index.json')
if 'sha256:'+digest(a.index)!=asset['digest']:raise ValueError('Official index digest mismatch')
receipts={}
for key,name in index['shards'].items():
 shard,tier=key.rsplit('-',1);path=a.cache/name
 expected={e[tier]['file']:e[tier] for e in index['artworks'].values() if e['shard']==shard and tier in e}
 with zipfile.ZipFile(path) as archive:
  names=archive.namelist()
  if len(names)!=len(set(names)) or set(names)!=set(expected):raise ValueError('Shard membership differs')
  for name,value in expected.items():
   h=hashlib.sha256();count=0
   with archive.open(name) as f:
    for b in iter(lambda:f.read(1024*1024),b''):h.update(b);count+=len(b)
   if h.hexdigest()!=value['sha256'] or count!=value['bytes']:raise ValueError('Cached PNG differs '+name)
 receipts[key]={'file':str(path.resolve()),'sha256':digest(path),'files':len(expected),'bytes':path.stat().st_size,
                'official_index_sha256':digest(a.index),'provenance':'PNG contents match official index; ZIP bytes are local cache identity'}
save(a.output,receipts)
print(json.dumps({'verified_shards':len(receipts),'verified_png_entries':sum(x['files'] for x in receipts.values())}))
