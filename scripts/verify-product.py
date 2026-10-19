"""Real local Ollama/API smoke test. Creates isolated manual analysis records, not news."""
import json
from pathlib import Path
import sys
import time
import httpx

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8765'
client=httpx.Client(base_url=BASE,timeout=90,trust_env=False)

def request(path,body=None):
    r=client.get(path) if body is None else client.post(path,json=body)
    r.raise_for_status()
    return r.json()

def wait(aid,field):
    end=time.monotonic()+1300
    last=None
    while time.monotonic()<end:
        a=request('/api/analyses/'+aid+'?include_input=true')
        if a[field]!=last:
            print(aid,field,a[field],flush=True)
            last=a[field]
        if a[field]=='failed':
            raise RuntimeError(a.get('detail_error' if field=='detail_status' else 'error'))
        if a[field]=='completed':
            return a
        time.sleep(2)
    raise TimeoutError('Real model did not finish in the bounded verification interval')

report={'health':request('/api/health'),'cases':{},'started_at':time.time(),'status':'running'}
path=ROOT/'reports'/'product_smoke.json'
try:
    before=report['health']['counts']['news']
    manual=request('/api/manual/analyze',{'title':'验收用供需冲突情景','source':'手动验收情景（未核实新闻）','text':'测试情景：挪威天然气处理设施发生意外停产，运营方预计持续三天；与此同时欧洲天气温和，LNG到港增加。请分析各自传导和净影响；本输入是假设情景，不是已经核实的新闻。'})
    news=request('/api/news?scope=recent&limit=200')['items']
    candidates=[n for n in news if n['source']=='gassco' and n.get('event_end')]
    if not candidates:
        raise RuntimeError('No recent real Gassco record is available for this smoke test')
    actual=next((n for n in candidates if '344-A' in str(n.get('source_message_id'))),candidates[0])
    article=request('/api/news/'+actual['id']+'/analyze',{})
    ids={'manual':manual['id'],'article':article['id']}
    (ROOT/'reports'/'final_model_validation_ids.json').write_text(json.dumps(ids,indent=2))
    for key,aid in ids.items():
        a=wait(aid,'status')
        request('/api/analyses/'+aid+'/detail',{})
        report['cases'][key]=a
    for key,aid in ids.items():
        a=wait(aid,'detail_status')
        assert a['result']['_meta']['model']=='gpt-oss:20b'
        assert a['detail_result']['detail']['references']
        cached=request('/api/analyses/'+aid+'/detail',{})
        assert cached['status']=='completed'
        report['cases'][key]=a
    content=client.get('/api/export/news.jsonl');content.raise_for_status()
    rows=[json.loads(line) for line in content.text.splitlines() if line]
    report['exported_news_count']=len(rows)
    assert len(rows)==before==request('/api/health')['counts']['news']
    assert all(r.get('summary') and r.get('url') for r in rows)
    report['status']='passed'
except Exception as exc:
    report['status']='failed'
    report['error']=str(exc)
finally:
    report['finished_at']=time.time()
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('saved',path,report['status'],flush=True)
if report['status']!='passed':
    sys.exit(1)
