"""Small valid-PNG, twelve-version local v2 acceptance sequence."""
import argparse
import hashlib
import json
import random
import struct
import subprocess
import zlib
from pathlib import Path
from sharding import package, atomic_json


def png(seed):
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    data = random.Random(seed).randbytes(16 * 16 * 3)
    rows = b''.join(b'\0' + data[i:i+48] for i in range(0, len(data), 48))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 16,16,8,2,0,0,0)) + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b'')


def campaign(root, binary):
    root.mkdir(parents=True, exist_ok=False)
    versions = {}; releases = []; assets = {}; prior = None; receipts = []
    source = 'khpilot/arknights-data-pipeline'
    def url(tag, name): return f'https://github.com/{source}/releases/download/{tag}/{name}'
    for step in range(1, 13):
        version = f'00-02-{step:02d}-00-00-00_lab'
        images = root / f'input-{step}'; images.mkdir()
        if step != 8: versions[step + 110] = step
        if step == 1: versions.update({n:n for n in range(107)})
        if step in (3,7,11): versions[0] = 1000 + step
        if step in (4,10): del versions[step]
        index = {'currentVersion':version, 'artworks':{}}
        for n, seed in versions.items():
            entry = {'shard':'chararts'}
            for tier in ['large','preview','original']:
                name = f'{n:04d}.{tier}.png'; content = png(seed)
                (images/name).write_bytes(content)
                entry[tier] = {'file':name, 'bytes':len(content), 'sha256':hashlib.sha256(content).hexdigest(), 'w':16,'h':16}
            index['artworks'][str(n)] = entry
        dist = root/f'dist-{step}'
        current = package(images, index, dist, None if step in (1,5,9) else prior, 1100)
        tag = 'images2-' + version
        releases.insert(0, {'tag_name':tag, 'draft':False, 'prerelease':False})
        assets[url(tag,'index.json')] = str((dist/'index.json').relative_to(root))
        parts = current['baselineParts'] if step in (1,5,9) else current['deltaParts']
        part_tag = 'images2-baseline-' + version if step in (1,5,9) else tag
        for part in parts: assets[url(part_tag,part['file'])] = str((dist/part['file']).relative_to(root))
        fixture = root/'fixture.json'
        atomic_json(fixture, {'channel':'v2','source':source,'releases':releases,'assets':assets})
        for tier in ['default','original']:
            client = root/('client-'+tier)
            p = subprocess.run([str(binary), str(fixture), str(client), tier], capture_output=True,text=True)
            (root/f'{step}-{tier}.log').write_text(p.stdout+p.stderr)
            if p.returncode: raise RuntimeError(p.stdout+p.stderr)
            meta = json.loads((client/'.images_meta.json').read_text())
            active = client/meta['generation_root']
            wanted = {v['file']:v for e in current['artworks'].values() for t,v in e.items() if isinstance(v,dict) and (tier=='original' or t!='original')}
            actual = {p.name for p in active.glob('*.png')}
            assert actual == set(wanted)
            for name,v in wanted.items():assert hashlib.sha256((active/name).read_bytes()).hexdigest()==v['sha256']
            receipts.append({'step':step,'tier':tier,'verified_files':len(wanted),'runtime':json.loads(p.stdout)})
        prior = current
        atomic_json(root/'receipts.json', receipts)
    return receipts

if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--binary',type=Path,required=True)
    a=p.parse_args(); print(json.dumps({'passed':True,'trials':len(campaign(a.root.resolve(),a.binary.resolve()))}))
