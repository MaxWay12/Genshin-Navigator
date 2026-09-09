"""Exercise the unchanged 0.1.4 updater with a real ZIP, without publishing it."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from genshin_navigator.updates import ReleaseInfo, UpdateService
from genshin_navigator.portable_transfer import PortableTransfer, atomic_json


def verify(source,archive,destination):
    source=Path(source).resolve(); archive=Path(archive).resolve(); destination=Path(destination).resolve()
    checksum=Path(str(archive)+'.sha256')
    url='https://github.com/MaxWay12/Genshin-Navigator/releases/download/v0.2.0-alpha/'+archive.name
    files=PortableTransfer(destination)._files(source)
    hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    def opener(request,timeout):
        if request.full_url==url: return archive.open('rb')
        if request.full_url==url+'.sha256': return checksum.open('rb')
        raise ValueError('Unexpected network request')
    release=ReleaseInfo('v0.2.0-alpha','local smoke',archive.name,url,url+'.sha256',archive.stat().st_size)
    result=UpdateService(source,opener=opener).apply(release,destination,stopped=True)
    executable=destination/'GenshinNavigator.exe'
    version=subprocess.run([str(executable),'--version'],cwd=destination,capture_output=True,text=True,check=True,timeout=20).stdout.strip()
    status=subprocess.run([str(executable),'data-status','--config','config.json'],cwd=destination,capture_output=True,text=True,check=True,timeout=30).stdout
    data=json.loads(status)
    if data['schema_version']!=4: raise AssertionError('Expected schema v4')
    if any(hashlib.sha256(p.read_bytes()).hexdigest()!=digest for p,digest in hashes.items()): raise AssertionError('Source changed')
    result=dict(passed=True,version=version,schema_version=data['schema_version'],poi_count=data['poi_count'],
                collected_count=data['collected_count'],source_unchanged=True,real_network_used=False)
    atomic_json(destination.parent/'update-result.json',result)
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('source'); parser.add_argument('archive'); parser.add_argument('destination')
    args=parser.parse_args(); verify(args.source,args.archive,args.destination)
