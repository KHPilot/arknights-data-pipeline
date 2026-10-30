import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('lab', Path(__file__).with_name('lab.py'))
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)

class LabTests(unittest.TestCase):
    def candidate(self, root):
        raw = b'png-example'
        import hashlib
        index = {'baselineVersion':'P','currentVersion':'P','shards':{'chararts-large':'a.zip'},
                 'artworks':{'a':{'shard':'chararts','large':{'file':'a.large.png','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}}}}
        (root/'index.json').write_text(json.dumps(index))
        with zipfile.ZipFile(root/'a.zip','w') as z:z.writestr('chararts/a.large.png',raw)
        return index

    def test_baseline_complete_and_corruption(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.candidate(root)
            self.assertEqual(lab.verify(root)['files_verified'],1)
            with zipfile.ZipFile(root/'a.zip','w') as z:z.writestr('chararts/a.large.png',b'bad')
            with self.assertRaisesRegex(ValueError,'mismatch'):lab.verify(root)

    def test_missing_baseline_and_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.candidate(root)
            import shutil
            shutil.copyfile(root/'a.zip',root/'b.zip')
            with self.assertRaisesRegex(ValueError,'Duplicate'):lab.verify(root)
            (root/'b.zip').unlink();(root/'a.zip').unlink()
            with self.assertRaisesRegex(ValueError,'Incomplete'):lab.verify(root)

    def test_tree_comparison_reports_removed_added_changed(self):
        with tempfile.TemporaryDirectory() as d:
            a=Path(d)/'a';b=Path(d)/'b';a.mkdir();b.mkdir()
            (a/'changed.png').write_bytes(b'a');(b/'changed.png').write_bytes(b'b')
            (a/'removed.png').write_bytes(b'x');(b/'added.png').write_bytes(b'x')
            r=lab.compare(a,b)
            self.assertFalse(r['equal']);self.assertEqual(r['changed'],['changed.png'])
            self.assertEqual(r['left_only'],['removed.png']);self.assertEqual(r['right_only'],['added.png'])

    def test_snapshot_changed_bytes_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'a').write_bytes(b'a')
            lab.save(root/'snapshot.json',{'files':lab.inventory(root)})
            lab.verify_snapshot(root)
            (root/'a').write_bytes(b'b')
            with self.assertRaisesRegex(ValueError,'changed'):lab.verify_snapshot(root)

if __name__=='__main__':unittest.main()
