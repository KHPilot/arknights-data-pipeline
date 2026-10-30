"""Offline re-baseline experiment: inventory, build, verify and compare.

Run with the factory's Python environment and PYTHONPATH=src. No publication
or upstream lookup occurs in this module. Inputs must be frozen beforehand.
"""
import argparse
import hashlib
import json
import platform
import importlib.metadata
import shutil
import subprocess
import time
import zipfile
from pathlib import Path, PurePosixPath


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def inventory(root):
    return {p.relative_to(root).as_posix(): {'bytes': p.stat().st_size, 'sha256': digest(p)}
            for p in sorted(root.rglob('*')) if p.is_file()}


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')


def freeze(cache, excel, output):
    from akdp.images_fetch import _safe_filename
    info = json.loads((cache / 'hashes.json').read_text())
    if not info.get('versionId') or not info.get('hashes'):
        raise ValueError('Missing version or bundle manifest')
    expected = {_safe_filename(name) for name in info['hashes']}
    missing = sorted(name for name in expected if not (cache / name).is_file())
    if missing:
        raise ValueError(f'Incomplete AB cache: {len(missing)} missing, e.g. {missing[:3]}')
    data_manifest = excel.parent / 'manifest.json'
    if not data_manifest.is_file() or json.loads(data_manifest.read_text()).get('source', {}).get('versionId') != info['versionId']:
        raise ValueError('Data tables must have a matching source manifest')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'cache').mkdir()
    (output / 'excel').mkdir()
    for name in sorted(expected | {'hashes.json'}):
        shutil.copyfile(cache / name, output / 'cache' / name)
    for name in ['skin_table.json', 'character_table.json']:
        shutil.copyfile(excel / name, output / 'excel' / name)
    for name in ['upstream.json', 'capture.json']:
        if (cache.parent / name).is_file():
            shutil.copyfile(cache.parent / name, output / name)
    if (excel.parent / 'manifest.json').is_file():
        shutil.copyfile(excel.parent / 'manifest.json', output / 'data-manifest.json')
    files = inventory(output)
    save(output / 'snapshot.json', {'versionId': info['versionId'], 'files': files,
         'python': platform.python_version(),
         'dependencies': sorted(f'{d.metadata["Name"]}=={d.version}' for d in importlib.metadata.distributions())})
    return {'versionId': info['versionId'], 'files': len(files)}


def verify_snapshot(root):
    receipt = json.loads((root / 'snapshot.json').read_text())
    actual = inventory(root)
    actual.pop('snapshot.json')
    if actual != receipt['files']:
        raise ValueError('Snapshot bytes changed')
    return receipt


def build(snapshot, output, previous=None):
    from akdp import images_extract, images_index, images_variants, images_package, images_state
    receipt = verify_snapshot(snapshot)
    dependencies = sorted(f'{d.metadata["Name"]}=={d.version}' for d in importlib.metadata.distributions())
    if dependencies != receipt['dependencies'] or platform.python_version() != receipt['python']:
        raise ValueError('Frozen toolchain differs from current environment')
    source_root = Path(__file__).resolve().parents[2]
    source_id = {'commit': subprocess.check_output(['git','-C',str(source_root),'rev-parse','HEAD'], text=True).strip(),
                 'lab_sha256': digest(Path(__file__)),
                 'source_diff_sha256': hashlib.sha256(subprocess.check_output(['git','-C',str(source_root),'diff','HEAD'])).hexdigest()}
    start = time.monotonic()
    output.mkdir(parents=True, exist_ok=False)
    images = output / 'images'
    images.mkdir()
    hashes = json.loads((snapshot / 'cache' / 'hashes.json').read_text())['hashes']
    stats = images_extract.extract_images(snapshot / 'cache', images, snapshot / 'excel', set(hashes))
    if stats.failed:
        raise ValueError(f'Extraction failures: {stats.failed}')
    index, index_stats = images_index.generate_index(images, snapshot / 'excel', version_id=receipt['versionId'])
    valid = images_extract._load_skin_ids(snapshot / 'excel')
    missing = valid - set(index['artworks'])
    if missing:
        raise ValueError(f'Missing valid skins: {sorted(missing)[:20]}')
    images_index.write_index_atomic(images / 'index.json', index)
    variants = images_variants.generate_variants(images, images / 'index.json')
    if variants.failed:
        raise ValueError(f'Variant failures: {variants.failed}')
    dist = output / 'dist'
    final, package = images_package.package_images(images, dist, previous, receipt['versionId'])
    hashes = json.loads((snapshot / 'cache' / 'hashes.json').read_text())['hashes']
    state = images_state.build_next_state(None, version_id=receipt['versionId'], bundle_hashes=hashes,
              processed_candidates=stats.bundle_candidates, valid_skin_ids=valid)
    if set(state.bundle_candidates) != set(hashes):
        raise ValueError('Build state lost bundle provenance')
    images_state.write_build_state(dist / images_state.BUILD_STATE_ASSET, state)
    result = {'versionId': receipt['versionId'], 'snapshot_sha256': digest(snapshot / 'snapshot.json'),
              'previous_index_sha256': digest(previous) if previous else None,
              'seconds': time.monotonic() - start,
              'factory_commit': source_id,
              'extract': stats.to_dict(),
              'index': index_stats.to_dict(), 'package': package.to_dict(), 'assets': inventory(dist)}
    save(output / 'build.json', result)
    return result


def members(index):
    result = {}
    for artwork in index['artworks'].values():
        for variant in ['large', 'preview', 'original']:
            if variant not in artwork:
                continue
            item = artwork[variant]
            name = item['file']
            p = PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or '\\' in name:
                raise ValueError('Unsafe asset path')
            metadata = {'bytes': item['bytes'], 'sha256': item['sha256']}
            if name in result and result[name] != metadata:
                raise ValueError('Conflicting asset path')
            result[name] = metadata
    return result


def verify(dist, previous=None):
    index = json.loads((dist / 'index.json').read_text())
    state = json.loads((dist / 'images-build-state.json').read_text())
    if state.get('schemaVersion') != 'akdp-images-build-state/v1' or state.get('versionId') != index['currentVersion']:
        raise ValueError('Build state version differs from index')
    expected = members(index)
    required = expected
    if index['baselineVersion'] != index['currentVersion']:
        if previous is None:
            raise ValueError('Delta validation requires previous index')
        from akdp.images_diff import compute_delta
        prior = json.loads(previous.read_text())
        if prior['baselineVersion'] != index['baselineVersion'] or prior['currentVersion'] >= index['currentVersion']:
            raise ValueError('Delta predecessor does not match chain')
        delta = compute_delta(index, prior)
        required = members({'artworks': {k: index['artworks'][k] for k in delta['added'] | delta['changed']}})
    delta_asset = dist / f"images-delta-{index['currentVersion']}.zip"
    if not delta_asset.is_file():
        raise ValueError('Missing delta/sentinel ZIP')
    seen = {}
    assets = {}
    for path in sorted(dist.glob('*.zip')):
        if path.stat().st_size >= 2 * 1024**3:
            raise ValueError(f'Asset reaches 2 GiB: {path.name}')
        assigned = required
        if index['baselineVersion'] == index['currentVersion']:
            keys = [k for k, v in index['shards'].items() if v == path.name]
            if len(keys) == 1:
                shard, tier = keys[0].rsplit('-', 1)
                assigned = {a[tier]['file']: expected[a[tier]['file']] for a in index['artworks'].values() if a['shard'] == shard and tier in a}
            elif path.name == f"images-delta-{index['currentVersion']}.zip":
                assigned = {}
            else:
                raise ValueError('Unexpected or ambiguous baseline ZIP')
        elif path.name != f"images-delta-{index['currentVersion']}.zip":
            raise ValueError('Unexpected delta ZIP')
        with zipfile.ZipFile(path) as archive:
            if {e.filename for e in archive.infolist() if not e.is_dir()} != set(assigned):
                raise ValueError('ZIP shard membership differs')
            for entry in archive.infolist():
                if entry.is_dir():
                    continue
                if entry.filename in seen:
                    raise ValueError('Duplicate ZIP member')
                h = hashlib.sha256()
                with archive.open(entry) as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        h.update(block)
                value = {'bytes': entry.file_size, 'sha256': h.hexdigest()}
                if expected.get(entry.filename) != value:
                    raise ValueError(f'ZIP/index mismatch: {entry.filename}')
                seen[entry.filename] = value
        assets[path.name] = {'bytes': path.stat().st_size, 'sha256': digest(path)}
    if seen != required:
        raise ValueError('Incomplete baseline or delta membership differs')
    if index['baselineVersion'] == index['currentVersion']:
        if not set(index['shards'].values()).issubset(assets):
            raise ValueError('Missing declared shard')
    return {'files_verified': len(seen), 'assets': assets}



def delta(full_build, previous, output):
    """Package P->L from a verified full L extraction, without re-decoding ABs."""
    from akdp.images_package import package_images
    receipt = json.loads((full_build / 'build.json').read_text())
    for name in ['index.json', 'images-build-state.json']:
        path = full_build / 'dist' / name
        if {'bytes': path.stat().st_size, 'sha256': digest(path)} != receipt['assets'][name]:
            raise ValueError('Full-build asset differs from receipt')
    source_index = json.loads((full_build / 'dist' / 'index.json').read_text())
    source_state = json.loads((full_build / 'dist' / 'images-build-state.json').read_text())
    images = full_build / 'images'
    index = json.loads((images / 'index.json').read_text())
    if not (receipt['versionId'] == source_index['currentVersion'] == source_state['versionId'] == index['currentVersion']):
        raise ValueError('Full-build versions differ')
    if members(index) != members(source_index):
        raise ValueError('Extraction index differs from full-build index')
    expected = members(index)
    actual = {k: v for k, v in inventory(images).items() if k.endswith('.png')}
    if actual != expected:
        raise ValueError('Full extraction differs from its index')
    prior = json.loads(previous.read_text())
    if prior['currentVersion'] >= index['currentVersion']:
        raise ValueError('Delta must advance the version')
    output.mkdir(parents=True, exist_ok=False)
    package_images(images, output, previous, index['currentVersion'])
    shutil.copyfile(full_build / 'dist' / 'images-build-state.json', output / 'images-build-state.json')
    result = verify(output, previous)
    result.update({'full_build_receipt_sha256': digest(full_build / 'build.json'),
                   'previous_index_sha256': digest(previous)})
    save(output.parent / (output.name + '-receipt.json'), result)
    return result


def compare(left, right):
    if not left.is_dir() or not right.is_dir():
        raise ValueError('Comparison requires two existing directories')
    a, b = inventory(left), inventory(right)
    a = {k: v for k, v in a.items() if k.endswith('.png')}
    b = {k: v for k, v in b.items() if k.endswith('.png')}
    if not a or not b:
        raise ValueError('Comparison requires nonempty PNG inventories')
    return {'equal': a == b, 'left_only': sorted(a.keys() - b.keys()),
            'right_only': sorted(b.keys() - a.keys()),
            'changed': sorted(k for k in a.keys() & b.keys() if a[k] != b[k]),
            'left_files': len(a), 'right_files': len(b)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('freeze')
    for name in ['cache', 'excel', 'output']:
        p.add_argument('--' + name, type=Path, required=True)
    p = sub.add_parser('build')
    for name in ['snapshot', 'output']:
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--previous', type=Path)
    p = sub.add_parser('verify')
    p.add_argument('--dist', type=Path, required=True)
    p.add_argument('--previous', type=Path)
    p = sub.add_parser('delta')
    for name in ['full-build', 'previous', 'output']:
        p.add_argument('--' + name, type=Path, required=True)
    p = sub.add_parser('compare')
    for name in ['left', 'right']:
        p.add_argument('--' + name, type=Path, required=True)
    args = vars(parser.parse_args())
    command = args.pop('command')
    print(json.dumps(globals()[command](**args), indent=2))


if __name__ == '__main__':
    main()
