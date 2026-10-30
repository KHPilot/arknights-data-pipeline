"""Capture one current upstream manifest and its exact asset URLs on workstation."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from arkprts import network
from arkprts.assets.bundle import unzip_only_file
from akdp.cdn import asset_url
from akdp.images_fetch import _is_art_bundle, _safe_filename

async def capture(root):
    root.mkdir(parents=True, exist_ok=True)
    session = network.NetworkSession(default_server='cn')
    try:
        receipt = root / 'upstream.json'
        if receipt.exists():
            fixed = json.loads(receipt.read_text())
        else:
            platform = session.default_platform or 'Android'
            await session.load_version_config('cn', platform)
            url = asset_url(session, 'hot_update_list.json', 'cn')
            async with session.session.get(url) as response:
                response.raise_for_status()
                manifest = await response.json(content_type=None)
            fixed = {'manifest': manifest, 'manifest_url': url,
                     'urls': {x['name']: asset_url(session, x['name'], 'cn')
                              for x in manifest['abInfos'] if _is_art_bundle(x['name'])}}
            with receipt.open('x') as f:
                json.dump(fixed, f, indent=2)
        cache = root / 'cache'
        cache.mkdir(exist_ok=True)
        sem = asyncio.Semaphore(4)
        hashes = {}
        async def one(name, url):
            async with sem:
                dest = cache / _safe_filename(name)
                note = dest.with_suffix('.receipt.json')
                if dest.exists() and note.exists():
                    record = json.loads(note.read_text())
                    if hashlib.sha256(dest.read_bytes()).hexdigest() != record['sha256']:
                        raise ValueError('Cached capture changed: ' + name)
                    hashes[name] = record
                    return
                async with session.session.get(url) as response:
                    response.raise_for_status()
                    zipped = await response.read()
                data = unzip_only_file(zipped)
                temp = dest.with_suffix('.part')
                temp.write_bytes(data)
                temp.replace(dest)
                record = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
                          'download_bytes': len(zipped), 'archive_sha256': hashlib.sha256(zipped).hexdigest()}
                note.write_text(json.dumps(record))
                hashes[name] = record
                print(json.dumps({'captured': name, 'bytes': len(data)}), flush=True)
        await asyncio.gather(*(one(n, u) for n, u in fixed['urls'].items()))
        info = {'versionId': fixed['manifest']['versionId'],
                'hashes': {x['name']: x['hash'] for x in fixed['manifest']['abInfos'] if x['name'] in hashes}}
        (cache / 'hashes.json').write_text(json.dumps(info, indent=2))
        (root / 'capture.json').write_text(json.dumps(hashes, indent=2))
        print(json.dumps({'complete': True, 'versionId': info['versionId'], 'bundles': len(hashes)}))
    finally:
        await session.session.close()

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory', type=Path)
    asyncio.run(capture(p.parse_args().directory))
