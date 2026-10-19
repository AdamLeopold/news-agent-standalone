#!/usr/bin/env python3
"""Record the exported delivery snapshot without claiming future validation."""
import hashlib
import json
from pathlib import Path
import urllib.request

from ttf_news.calendar import iso
from ttf_news.config import ROOT
from ttf_news.evaluation import monitoring_report
from ttf_news.prompts import PROMPT_VERSION
from ttf_news.store import Store


def main():
    store = Store()
    artifacts = []
    for path in sorted((ROOT / 'data' / 'exports').glob('*')):
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        lines = 0
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
                lines += chunk.count(b'\n')
        artifacts.append({'file': str(path.relative_to(ROOT)), 'bytes': path.stat().st_size,
                          'sha256': digest.hexdigest(),
                          'records': lines if path.suffix == '.jsonl' else None})
    with urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=10) as response:
        health = json.load(response)
    smoke = json.loads((ROOT / 'reports' / 'product_smoke.json').read_text())
    report = {'generated_at': iso(), 'project': str(ROOT), 'counts': store.counts(),
              'prompt_version': PROMPT_VERSION, 'runtime_health': health,
              'real_model_smoke_status': smoke['status'],
              'smoke_prompt_version': smoke.get('health', {}).get('prompt_version'),
              'seven_day_monitoring': monitoring_report(store), 'artifacts': artifacts,
              'limitations': ['Selected free-source coverage is documented in reports/coverage.md.',
                              'Date-only articles are exported but not assigned a fabricated settlement window.',
                              'Reference prices are excluded from official settlement accuracy.',
                              'Seven-day validation requires seven actual days and recorded heartbeat continuity.',
                              'UI supports Chinese and English; model analysis defaults to Chinese.']}
    (ROOT / 'reports' / 'delivery_manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({'counts': report['counts'], 'smoke': smoke['status'],
                      'collector_enabled': health['collector']['enabled'],
                      'seven_day_complete': report['seven_day_monitoring']['seven_day_validation_complete'],
                      'manifest': str(ROOT / 'reports' / 'delivery_manifest.json')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
