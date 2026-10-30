import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from subprocess import CompletedProcess
from lab import digest
from sharding_publish import publish

class PublishRecoveryTests(unittest.TestCase):
    def test_starter_on_draft_is_replaced_and_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist=Path(tmp);(dist/'part.zip').write_bytes(b'archive')
            (dist/'index.json').write_text(json.dumps({'currentVersion':'L','baselineVersion':'P','deltaParts':[{'file':'part.zip'}]}))
            release={'id':1,'tag_name':'images2-L','name':'images2-L','draft':True,'target_commitish':'abc'}
            assets={'part.zip':{'id':9,'name':'part.zip','state':'starter','size':0}}
            deleted=[];uploaded=[]
            def run(cmd,**kw):
                args=cmd[1:];out=None
                if args==['api','user']:out={'login':'KHPilot'}
                elif args==['api','repos/KHPilot/arknights-data-pipeline']:out={'owner':{'login':'KHPilot'},'fork':True}
                elif args[0]=='release':
                    file=Path(args[-1]);uploaded.append(file.name);assets[file.name]={'id':10+len(uploaded),'name':file.name,'state':'uploaded','size':file.stat().st_size,'digest':'sha256:'+digest(file)}
                elif 'DELETE' in args:
                    deleted.append(args[-1]);assets.pop('part.zip')
                elif 'PATCH' in args:release['draft']=False;out=release
                elif args[-1].endswith('/assets?per_page=100'):out=[list(assets.values())]
                elif args[-1].endswith('/releases?per_page=100'):out=[[release]]
                else:raise AssertionError(args)
                return CompletedProcess(cmd,0,json.dumps(out) if out is not None else '', '')
            with patch('sharding_publish.verify'),patch('sharding_publish.subprocess.check_output',return_value='test-token'),patch('sharding_publish.subprocess.run',side_effect=run):
                receipt=publish(dist,'KHPilot/arknights-data-pipeline','test-host',{})
            self.assertEqual(len(deleted),1);self.assertEqual(uploaded,['part.zip','index.json'])
            self.assertEqual(receipt[0]['state'],'public')

if __name__=='__main__':unittest.main()
