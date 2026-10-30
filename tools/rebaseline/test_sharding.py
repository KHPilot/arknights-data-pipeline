import copy
import hashlib
import json
import random
import tempfile
import unittest
from pathlib import Path
from sharding import package, verify

class ShardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.images = self.root / 'images'; self.images.mkdir()
        self.index = {'currentVersion': '26-01-01', 'artworks': {}}

    def add(self, n, seed=0):
        data = random.Random(seed + n).randbytes(512)
        name = f'{n:04d}.large.png'
        (self.images / name).write_bytes(data)
        self.index['artworks'][str(n)] = {'shard': 'chararts', 'large': {'file': name,
            'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'w': 1, 'h': 1}}

    def build(self, name, previous=None, target=1500):
        return package(self.images, self.index, self.root / name, previous, target)

    def test_growth_determinism_and_resume(self):
        for n in range(107): self.add(n)
        a = self.build('a', target=700)
        self.assertEqual(len(a['baselineParts']), 107)
        self.assertEqual(a, self.build('b', target=700))
        self.assertEqual(a, self.build('a', target=700))
        self.assertEqual(verify(self.root/'a')['members'], 107)

    def test_delta_changes_deletion_empty_and_predecessor(self):
        for n in range(6): self.add(n)
        p = self.build('p')
        self.index['currentVersion'] = '26-01-02'
        self.add(0, 100); self.add(7); del self.index['artworks']['1']
        l = self.build('l', p, 700)
        self.assertEqual(verify(self.root/'l', p)['members'], 2)
        self.assertEqual(l['baselineParts'], p['baselineParts'])
        self.index['currentVersion'] = '26-01-03'
        empty = self.build('empty', l)
        self.assertEqual(empty['deltaParts'], [])
        with self.assertRaises(ValueError): verify(self.root/'l', l)

    def test_bad_input_and_corrupted_part(self):
        self.add(1)
        p = self.build('p')
        asset = self.root/'p'/p['baselineParts'][0]['file']
        asset.write_bytes(b'bad')
        with self.assertRaises(ValueError): verify(self.root/'p')
        with self.assertRaises(ValueError): self.build('p')
        (self.images/'0001.large.png').write_bytes(b'bad')
        with self.assertRaises(ValueError): self.build('new')

    def test_identity_and_path_rejection(self):
        self.add(1); self.build('p')
        self.index['currentVersion'] = '26-01-02'
        with self.assertRaises(ValueError): self.build('p')
        self.index['artworks']['1']['large']['file'] = '../escape.png'
        with self.assertRaises(ValueError): self.build('unsafe')

    def test_actual_zip_overhead_triggers_split(self):
        for n in range(3): self.add(n)
        p = self.build('p', target=1100)
        self.assertEqual(len(p['baselineParts']), 3)
        self.assertTrue(all(x['bytes'] <= 1100 for x in p['baselineParts']))

if __name__ == '__main__': unittest.main()
