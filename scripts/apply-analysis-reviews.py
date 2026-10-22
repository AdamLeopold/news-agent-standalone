#!/usr/bin/env python3
"""Verify an explicit review manifest; append only with --apply after deployment.

The default is a read-only preflight. Canonical analyses, frozen predictions
and the validation baseline are never updated by this command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def preflight(database: Path, manifest: dict) -> list[dict]:
    reviews=manifest.get('pending_reviews')
    if not isinstance(reviews,list) or not reviews:
        raise ValueError('No explicit pending reviews in manifest')
    checked=[]
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True) as c:
        c.row_factory=sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        for item in reviews:
            row=c.execute('SELECT input_snapshot,result,detail_result FROM analyses WHERE id=?',(item['analysis_id'],)).fetchone()
            if row is None:
                raise ValueError('Reviewed analysis is missing')
            hashes={key:hashlib.sha256(row[key].encode()).hexdigest() if row[key] is not None else None
                    for key in ('input_snapshot','result','detail_result')}
            parts=item['review']['affected_parts']
            if not parts or any(part not in ('brief','detail') for part in parts):
                raise ValueError('Unknown review part')
            required=['input_snapshot']+['result' if part=='brief' else 'detail_result' for part in parts]
            if any(hashes[key] is None or hashes[key]!=item['protected_hashes'].get(key) for key in required):
                raise ValueError('Reviewed canonical fields changed; inspect before appending')
            checked.append({'analysis_id':item['analysis_id'],'affected_parts':parts,
                            'protected_hashes_match':True,'review_id':item['review']['review_id']})
    return checked


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--database',type=Path,default=ROOT/'data/ttf_news.sqlite3')
    parser.add_argument('--apply',action='store_true',help='Append reviewed metadata after code deployment')
    args=parser.parse_args()
    manifest=json.loads(args.manifest.read_text())
    checked=preflight(args.database,manifest)
    if args.apply:
        from ttf_news.store import Store
        store=Store(args.database)
        for item in manifest['pending_reviews']:
            store.append_analysis_review(item['analysis_id'],item['review'],expected_hashes=item['protected_hashes'])
    print(json.dumps({'status':'applied' if args.apply else 'verified_read_only',
                      'canonical_analyses_modified':False,'reviews':checked},ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
