"""Cache immutable production image releases on the workstation for replay."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
from lab import digest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',required=True)
    p.add_argument('--through',required=True)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--cache',type=Path)
    args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    receipt=args.output/'releases.json'
    if receipt.exists():
        releases=json.loads(receipt.read_text())
    else:
        pages=json.loads(subprocess.check_output(['gh','api','--paginate','--slurp',f'repos/{args.repo}/releases?per_page=100'],text=True))
        releases=[]
        for page in pages:
            for r in page:
                tag=r['tag_name']
                if r['draft'] or not tag.startswith('images-'):continue
                version=tag.removeprefix('images-baseline-') if tag.startswith('images-baseline-') else tag.removeprefix('images-')
                if version<=args.through:releases.append(r)
        with receipt.open('x') as f:json.dump(releases,f,indent=2)
    saved={}
    for r in releases:
        directory=args.output/r['tag_name'];directory.mkdir(exist_ok=True)
        for a in r['assets']:
            name=a['name']
            if Path(name).name!=name:raise ValueError('Invalid asset name')
            dest=directory/name
            expected=a.get('digest')
            if not expected or not expected.startswith('sha256:'):raise ValueError('Missing upstream asset digest')
            def good(path):return path.is_file() and path.stat().st_size==a['size'] and 'sha256:'+digest(path)==expected
            if not good(dest):
                cached=args.cache/name if args.cache else None
                if cached and good(cached):shutil.copyfile(cached,dest)
                else:
                    if dest.exists():raise ValueError('Existing seed differs; preserve for investigation')
                    subprocess.run(['gh','release','download',r['tag_name'],'-R',args.repo,'--pattern',name,'--dir',str(directory)],check=True)
                if not good(dest):raise ValueError('Downloaded asset differs')
            saved[a['browser_download_url']]=dest.relative_to(args.output).as_posix()
            print(json.dumps({'tag':r['tag_name'],'asset':name,'bytes':a['size']}),flush=True)
    (args.output/'assets.json').write_text(json.dumps(saved,indent=2))

if __name__=='__main__':main()
