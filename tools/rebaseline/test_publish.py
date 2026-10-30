import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import publish

class PublishTests(unittest.TestCase):
    def exercise(self, wrong=False):
        with tempfile.TemporaryDirectory() as temporary:
            dist=Path(temporary);tag='images-baseline-P';changes=[]
            (dist/'index.json').write_text(json.dumps({'baselineVersion':'P','currentVersion':'P','shards':{'chararts-large':'one.zip'}}))
            (dist/'one.zip').write_bytes(b'fixture');(dist/'images-build-state.json').write_text('{}')
            release={'id':7,'tag_name':'untagged-placeholder','name':'wrong' if wrong else tag,'draft':True,'target_commitish':'source-commit','assets':[{'name':p.name,'size':p.stat().st_size,'digest':'sha256:'+hashlib.sha256(p.read_bytes()).hexdigest()} for p in dist.iterdir()]}
            def api(args,env,input_text=None):
                if args==['api','user']:return json.dumps({'login':'KHPilot'})
                if args==['api','repos/KHPilot/arknights-data-pipeline']:return json.dumps({'owner':{'login':'KHPilot'},'fork':True})
                if args==['api','repos/KHPilot/arknights-data-pipeline/releases/7']:return json.dumps(release)
                if args[:3]==['api','--method','PATCH']:
                    self.assertEqual(args[3],'repos/KHPilot/arknights-data-pipeline/releases/7')
                    body=json.loads(input_text);changes.append(body);return json.dumps({**release,**body})
                self.fail('Unexpected API or by-tag lookup: '+repr(args))
            argv=['publish.py','--repo','KHPilot/arknights-data-pipeline','--dist',str(dist),'--kind','baseline','--stage','activate','--credential-ssh','lab','--release-id','7']
            with patch('sys.argv',argv),patch.object(publish,'run',side_effect=api),patch.object(publish,'verify'),patch.object(publish.subprocess,'check_output',return_value='fixture-token'),contextlib.redirect_stdout(io.StringIO()):
                if wrong:
                    with self.assertRaisesRegex(ValueError,'does not identify'):publish.main()
                    self.assertEqual(changes,[])
                else:
                    publish.main()
                    self.assertEqual(changes,[{'tag_name':tag,'target_commitish':'source-commit','draft':False,'make_latest':'false'}])

    def test_activate_renamed_draft_by_id(self):self.exercise()
    def test_wrong_draft_identity_never_activates(self):self.exercise(wrong=True)

if __name__=='__main__':unittest.main()
