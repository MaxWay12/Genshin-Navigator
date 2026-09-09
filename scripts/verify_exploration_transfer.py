"""Read-only source snapshot -> isolated destination -> schema v4 preservation."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from genshin_navigator.portable_transfer import PortableTransfer, atomic_json
from genshin_navigator.data_store import SqliteDataProvider


def tables(path):
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {name:sorted(db.execute('SELECT * FROM "'+name.replace('"','""')+'"').fetchall(),key=repr) for name in names}


def verify(source,destination):
    source=Path(source).resolve(); destination=Path(destination).resolve()
    destination.mkdir(parents=True,exist_ok=False)
    transfer=PortableTransfer(destination)
    files=transfer._files(source)
    before={p.relative_to(source):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    preview=transfer.preview(source)
    if preview['external_paths']: raise ValueError('Source snapshot has external paths')
    transfer.apply(source,stopped=True)
    results=[]
    for original in files:
        if original.suffix not in ('.db','.sqlite','.sqlite3'): continue
        expected=tables(original)
        target=destination/original.relative_to(source)
        provider=SqliteDataProvider(target)
        actual=tables(target)
        for name,rows in expected.items():
            if actual.get(name)!=rows: raise AssertionError('Changed existing table: '+name)
        with closing(sqlite3.connect(target)) as db:
            if db.execute('PRAGMA user_version').fetchone()[0]!=4: raise AssertionError('Migration missing')
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise AssertionError('Integrity failure')
        results.append({name:len(rows) for name,rows in expected.items()})
    if any(hashlib.sha256((source/path).read_bytes()).hexdigest()!=digest for path,digest in before.items()):
        raise AssertionError('Source installation changed')
    if (destination/'datasets/local/auth').exists(): raise AssertionError('Auth was transferred')
    result=dict(passed=True,files=len(files),databases=results,source_unchanged=True,auth_transferred=False)
    atomic_json(destination.parent/'transfer-result.json',result)
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('source'); parser.add_argument('destination')
    args=parser.parse_args(); verify(args.source,args.destination)
