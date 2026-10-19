#!/usr/bin/env python3
"""Prepare an isolated publish checkout and a consistent, restorable data snapshot.

This command performs no push and opens no public network listener. It never
initializes a nested Git repository in the working source checkout.
"""
import asyncio
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ttf_news.store import Store
from ttf_news.context import aggregate, price_context
from ttf_news.calendar import iso, session_at
from ttf_news.evaluation import monitoring_report
from ttf_news.cli import export


def main():
    target = ROOT / '.runtime/github-publish'
    refresh = '--refresh-snapshot' in sys.argv[1:]
    if (target / '.git').exists() and not refresh:
        raise SystemExit('Publish checkout already has Git history; update it explicitly rather than replacing it')
    if refresh and not (target / '.git').exists():
        raise SystemExit('Snapshot refresh requires the existing managed publish checkout')
    target.mkdir(parents=True, exist_ok=True)
    if not refresh:
        for name in ('ttf_news', 'scripts', 'tests', 'reports', 'deployment', '.github'):
            shutil.copytree(ROOT/name, target/name, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for name in ('README.md', 'pyproject.toml', 'requirements.lock', '.gitignore'):
            shutil.copy2(ROOT/name, target/name)
        shutil.copytree(ROOT/'frontend', target/'frontend', dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('node_modules', 'dist', 'dist-pages', '*.tsbuildinfo', '.DS_Store'))
        (target/'data').mkdir(exist_ok=True)
        for p in (ROOT/'data').iterdir():
            if p.is_file() and p.suffix in ('.json', '.md'):
                shutil.copy2(p, target/'data'/p.name)
    # Capture all tables, including inputs, revisions, predictions and monitoring.
    raw = ROOT/'data/deployment-snapshot.sqlite3'
    with sqlite3.connect(ROOT/'data/ttf_news.sqlite3') as src, sqlite3.connect(raw) as dst:
        src.backup(dst)
    store = Store(raw)
    captured = iso()
    snap = target/'frontend/public/snapshot'
    snap.mkdir(parents=True, exist_ok=True)
    def save(name, data):
        p = snap/name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    now = iso()
    session = session_at(now)
    articles = store.window_news(session['trade_date'], now)
    events = store.retrieve_events(' '.join(str(n.get('title', '')) for n in articles), now)
    monitor = monitoring_report(store)
    import httpx
    with httpx.Client(timeout=30) as client:
        health = client.get('http://127.0.0.1:8765/api/health').json()
    health['counts'] = store.counts()
    health['sources'] = store.sources()
    save('health.json', health)
    from ttf_news.market import market_view
    save('market.json', market_view(store, now))
    save('dashboard.json', {'session': session, 'daily': aggregate(articles),
         'latest_analysis': store.latest_analysis(trade_date=session['trade_date'], completed_only=True, live_only=True),
         'important_events': events, 'coverage': {'counts': store.counts(), 'sources': store.sources()},
         'price_reference': price_context(store, now), 'monitoring': monitor})
    save('events.json', {'items': store.events(1000000), 'total': store.counts()['events']})
    save('coverage.json', store.coverage())
    save('monitoring.json', monitor)
    with store.connection() as c:
        ids = [r[0] for r in c.execute('SELECT id FROM analyses ORDER BY created_at')]
        table_counts = {r[0]: c.execute('SELECT COUNT(*) FROM "'+r[0]+'"').fetchone()[0]
                        for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'event_search%' AND name NOT LIKE 'sqlite_%'").fetchall()}
        baseline = c.execute("SELECT created_at FROM metrics WHERE type='validation_started' ORDER BY id DESC LIMIT 1").fetchone()
    analyses = {identifier: store.analysis(identifier, include_input=True) for identifier in ids}
    latest_by_news = {}
    for a in analyses.values():
        save('analyses/'+a['id']+'.json', a)
        for part in ('brief','detail'):
            translated=store.cached_translation(a['id'],part)
            save('analyses/'+a['id']+'.en.'+part+'.json', translated if translated and translated['status']=='completed' else {'status':'not_requested'})
        if a.get('news_id'):
            latest_by_news[a['news_id']] = a
    for scope in ('all', 'live', 'recent'):
        enriched = []
        for n in store.news(1000000, scope=scope):
            a = latest_by_news.get(n['id'])
            enriched.append({**n, 'analysis_id': a['id'] if a else None,
                'analysis_status': a['status'] if a else 'not_requested',
                'analysis_created_at': a.get('created_at') if a else None,
                'analysis_completed_at': a.get('completed_at') if a else None,
                'analysis': a.get('result') if a else None})
        save('news/'+scope+'.json', {'items': enriched, 'total': store.counts()['news'], 'scope': scope})
    export(store, snap/'exports')
    compressed = raw.with_suffix('.sqlite3.gz')
    with raw.open('rb') as src, gzip.open(compressed, 'wb', compresslevel=6) as dst:
        shutil.copyfileobj(src, dst)
    manifest = {'captured_at': captured, 'counts': store.counts(), 'all_table_counts': table_counts,
                'validation_started': baseline[0] if baseline else None,
                'sqlite_sha256': hashlib.sha256(raw.read_bytes()).hexdigest(),
                'sqlite_gzip_sha256': hashlib.sha256(compressed.read_bytes()).hexdigest(),
                'sqlite_gzip_bytes': compressed.stat().st_size,
                'mode': 'Live API when available; explicit static snapshot otherwise',
                'model_weights': 'Download the same gpt-oss:20b digest with Ollama; no changed model or fine-tuning',
                'excluded_runtime_files': ['virtual environment', 'node_modules', 'logs', 'tokenizer cache', 'duplicate dated backups'],
                'files': {str(p.relative_to(snap)): {'bytes':p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                          for p in sorted(snap.rglob('*')) if p.is_file() and p.name!='manifest.json'}}
    save('manifest.json', manifest)
    (target/'reports/github_snapshot_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    # Dataset CSV/JSONL is served by Pages, complete SQLite is a GitHub release asset.
    print(json.dumps({'checkout': str(target), 'sqlite_asset': str(compressed),
                      'snapshot_bytes': sum(p.stat().st_size for p in snap.rglob('*') if p.is_file()),
                      'counts': manifest['counts'], 'captured_at': captured}, ensure_ascii=False))


if __name__ == '__main__':
    main()
