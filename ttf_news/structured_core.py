"""Private 2.4 prototype: typed source facts -> weighted mechanisms -> two renderers.
No transport, DB, production hooks, free narrative generation, or label repair.
"""
from __future__ import annotations
import copy, hashlib, json, math
from dataclasses import dataclass, field
from datetime import timezone
from typing import Any
from ttf_news.analysis import _json, _known, _datetime
from ttf_news.evidence import source_body
from ttf_news.semantics import _moment

VERSION='ttf-structured-reasoning-2.4.5'
DIRECTIONS=('extreme_up','up','flat','down','extreme_down')
CASES=('baseline_persists','news_dominates','retrace_to_flat','reverse','insufficient_anchor','uncertain_conflict','no_directional_signal')
SCOPES=('expectation','current_physical','longer_term','background')
REGIONS=('europe','global_lng','region_only','unspecified')
REALIZATIONS=('observed_as_reported','historical_as_reported','future','conditional','unspecified')
ROLES=('upstream','lng_export','lng_import','end_use','storage','technology','corporate','other')
# This one table generates the native discriminated union AND validates its
# structural type/state/role contract. It is not a semantic-entailment oracle.
TYPE_CONTRACTS={
 'future_project_approval':(('future',),('upstream','lng_export'),REGIONS),
 'future_project_development':(('future',),('upstream','lng_export'),REGIONS),
 'future_import_capacity':(('future','conditional'),('lng_import',),REGIONS),
 'future_additional_lng_consumption':(('future','conditional'),('end_use','lng_import'),REGIONS),
 'future_european_receiving_capacity':(('future','conditional'),('lng_import',),('europe',)),
 'corporate_contract':(REALIZATIONS,ROLES,REGIONS),
 'measured_supply_increase':(('observed_as_reported','historical_as_reported'),('upstream','lng_export','lng_import','storage','other'),REGIONS),
 'measured_supply_decrease':(('observed_as_reported','historical_as_reported'),('upstream','lng_export','lng_import','storage','other'),REGIONS),
 'demand_increase':(REALIZATIONS,('end_use','lng_import','other'),REGIONS),
 'demand_decrease':(REALIZATIONS,('end_use','lng_import','other'),REGIONS),
 'storage_build':(('observed_as_reported','historical_as_reported','future','conditional'),('storage','other'),REGIONS),
 'storage_draw':(('observed_as_reported','historical_as_reported','future','conditional'),('storage','other'),REGIONS),
 'supply_risk_increase':(REALIZATIONS,('upstream','lng_export','lng_import','storage','other'),REGIONS),
 'supply_risk_decrease':(REALIZATIONS,('upstream','lng_export','lng_import','storage','other'),REGIONS),
 'exploration_uncommitted':(REALIZATIONS,('upstream','other'),REGIONS),
 'technology_test':(REALIZATIONS,('technology','other'),REGIONS),
 'market_price_observation':(('observed_as_reported','historical_as_reported'),('other',),REGIONS),
 'other':(REALIZATIONS,ROLES,REGIONS)}
TYPES=tuple(TYPE_CONTRACTS)
IMPORTANCE=('direct_european_channel','future_expectation','low_window_materiality','weak_indirect_link','marginal_revision')
IMPORTANCE_TEXT={
 'direct_european_channel':('直接欧洲市场传导','direct European market channel'),
 'future_expectation':('未来预期传导','future expectations channel'),'low_window_materiality':('本窗口重要性低','low materiality in this window'),
 'weak_indirect_link':('间接联系较弱','weak indirect connection'),'marginal_revision':('边际修订','marginal revision')}
FLAT_SCORE=.25
EXTREME_SCORE=2.5
WORDS={'zh':{'extreme_up':'异常上涨','up':'上涨','flat':'基本持平','down':'下跌','extreme_down':'异常下跌',None:'方向信息不足'},
       'en':{'extreme_up':'rise unusually','up':'rise','flat':'remain broadly flat','down':'fall','extreme_down':'fall unusually',None:'insufficient directional evidence'}}
# A mechanism's economic sign is a definition, never supplied by a model.
DEFS={
 'future_supply':(-1,'潜在未来供给增加','potential future supply expansion'),
 'future_additional_lng_consumption':(1,'来源明确的未来新增最终用气可能增强LNG竞争需求','source-reported additional future end-use consumption may increase LNG demand competition'),
 'future_european_receiving_capacity':(-1,'未来欧洲接收能力或进口瓶颈缓解可能提高供给可达性','future European receiving capacity or import-bottleneck relief may improve supply accessibility'),
 'measured_supply_increase':(-1,'来源报道的生产或供给增加','source-reported physical production or supply increase'),
 'measured_supply_decrease':(1,'来源报道的生产或供给减少','source-reported physical production or supply decrease'),
 'demand_increase':(1,'需求增加','increasing demand'), 'demand_decrease':(-1,'需求减少','decreasing demand'),
 'storage_build':(-1,'库存增加','storage build'), 'storage_draw':(1,'库存下降','storage draw'),
 'supply_risk_increase':(1,'供给约束风险提高','increasing supply-constraint risk'),
 'supply_risk_decrease':(-1,'供给约束风险降低','decreasing supply-constraint risk'),
 'announced_constraint':(1,'公告容量约束的条件上行压力；不是已测减供','conditional upward pressure from announced capacity constraints; not measured lost flow'),
 'revision_tightening':(1,'相对上一已知版本的公告约束增强；不是实流下降','tighter announced constraints relative to the preceding known version; not lost flow'),
 'revision_relief':(-1,'相对上一已知版本的公告约束缓解；不是实流恢复','announced constraint relief relative to the preceding known version; not actual flow recovery'),
 'notice_withdrawn':(-1,'仅该公告约束撤销；整体供气仍未核实','withdrawal of this notice only; overall physical supply remains unverified'),
 'background':(0,'背景事实，不据此推断当前TTF供需变化','background information without an established current TTF supply-demand channel')}


def digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _revision_evidence(raw: dict, doc: dict, prepared: dict) -> dict | None:
    """Bind a real current publisher revision, never first-observed old content.

    Exact paragraph differences identify candidate evidence units, not semantic
    novelty. The model must separately compare each selected fact with prior
    evidence, including old claims that share a changed paragraph with new ones.
    """
    delta=raw.get('revision_delta') or {};basis=delta.get('source_version_metadata') or {}
    before=basis.get('before') or {};after=basis.get('after') or {}
    if delta.get('comparison_status')!='compared' or delta.get('substantive_change') is not True:return None
    left,now=_moment(prepared['session']['window_start']),_moment(prepared['as_of'])
    published=_moment(raw.get('published_at'));updated=_moment(after.get('updated_at'))
    old_updated=_moment(before.get('updated_at'));known=_moment(raw.get('version_seen_at'))
    previous_known=_moment(delta.get('previous_known_at'))
    if not all((left,now,published,updated,old_updated,known,previous_known)):return None
    if not (published<left<=updated<=known<=now and old_updated<updated and old_updated<=previous_known<=known):return None
    if _moment(raw.get('updated_at'))!=updated:return None
    try:
        old_version=int(delta['previous_version']);current_version=int(doc['source_version'])
    except (ValueError,TypeError,KeyError):return None
    if not 0<old_version<current_version:return None
    # A transport/body-authority migration is an observation change, not proof
    # that the publisher issued new facts at this time.
    authority=('source','timestamp_source','excerpt_method','excerpt_status')
    if not before.get('source') or not before.get('timestamp_source'):return None
    if any(before.get(k)!=after.get(k) for k in authority):return None
    if after.get('source')!=raw.get('source') or after.get('timestamp_source')!=raw.get('timestamp_source'):return None
    # Same URL/transport is not an immutable publisher article identity. An
    # API migration/reissue can retain a slug while replacing its document ID.
    previous_id=before.get('publisher_article_id');current_id=after.get('publisher_article_id')
    api_authority=(after.get('timestamp_source')=='publisher API publishedAt' or previous_id is not None or current_id is not None)
    if api_authority and (previous_id is None or current_id is None or str(previous_id).strip()=='' or str(current_id).strip()=='' or str(previous_id)!=str(current_id)):return None
    if current_id is not None and str(current_id)!=str(raw.get('publisher_article_id')):return None
    changes=delta.get('changed_fields') or {}
    if not any(key in changes for key in ('title','summary','fact_excerpt')):return None
    prior=copy.deepcopy(raw)
    for key in ('title','summary','fact_excerpt'):
        if key in changes:
            if changes[key].get('after')!=raw.get(key):return None
            prior[key]=changes[key].get('before')
    previous_doc={'source_id':doc['source_id'],'source_version':old_version,'source':doc['source'],
                  'title':str(prior.get('title') or ''),'body':source_body(prior)}
    identity={key:previous_doc[key] for key in ('source_id','source_version','source','title','body')}
    previous_doc['source_identity_sha256']=digest(identity)
    evidence=[]
    parts=[('title',0,previous_doc['title']),*[('body',i,text) for i,text in enumerate(previous_doc['body'].splitlines(keepends=True)) if text.strip()]]
    for field,ordinal,text in parts:
        evidence.append({'evidence_id':f"P.{previous_doc['source_identity_sha256'][:6]}{field[0]}{ordinal}",
                         'source_field':field,'ordinal':ordinal,'text':text,
                         'text_sha256':hashlib.sha256(text.encode()).hexdigest(),
                         'original_source_id':doc['source_id'],'source_version':old_version,
                         'source_identity_sha256':previous_doc['source_identity_sha256']})
    return {'kind':'publisher_revision_comparison','published_at':raw['published_at'],
            'publisher_updated_at':after['updated_at'],'previous_publisher_updated_at':before['updated_at'],
            'current_known_at':raw['version_seen_at'],'previous_known_at':delta['previous_known_at'],
            'previous_document':previous_doc,'previous_evidence':evidence,
            'novelty_unit':'model-selected claim compared across immutable full paragraphs',
            'semantic_novelty_programmatically_verified':False}


def _revision_fact_timing(item: dict, selected: dict, doc: dict, default_historical: bool) -> tuple[bool,dict | None]:
    bundle=doc.get('publisher_revision')
    if not bundle:return default_historical,None
    novelty=item['novelty'];prior_ids=item['prior_evidence_ids']
    if novelty not in {'unchanged','added','revised'} or not isinstance(prior_ids,list) or len(prior_ids)>3 or len(set(prior_ids))!=len(prior_ids):
        raise ValueError('Invalid source-revision claim comparison')
    previous={row['evidence_id']:row for row in bundle['previous_evidence']}
    if any(pid not in previous for pid in prior_ids):raise ValueError('Prior evidence ID is not from this source version')
    same=[row['evidence_id'] for row in previous.values() if row['source_field']==selected['source_field'] and row['text_sha256']==selected['text_sha256']]
    if same and novelty!='unchanged':raise ValueError('An unchanged complete evidence paragraph is not a new publisher claim')
    if novelty=='added' and prior_ids:raise ValueError('An added claim must not claim a replaced prior fact')
    if novelty=='revised' and not prior_ids:raise ValueError('A revised claim requires prior source evidence')
    if novelty=='unchanged' and not (same or prior_ids):raise ValueError('An unchanged claim requires its previous source evidence')
    temporal={'novelty':novelty,'publisher_updated_at':bundle['publisher_updated_at'],
              'current_known_at':bundle['current_known_at'],'previous_known_at':bundle['previous_known_at'],
              'prior_evidence':[copy.deepcopy(previous[pid]) for pid in (prior_ids or same)],
              'semantic_novelty_programmatically_verified':False}
    return (item['realization']=='historical_as_reported' or novelty=='unchanged'),temporal


def _finite(value: Any,low: float,high: float) -> float:
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
        raise ValueError('Expected a finite number within its declared bounds')
    return float(value)


def _keys(value: dict,allowed: set[str],required: set[str]) -> None:
    if not isinstance(value,dict) or set(value)-allowed or required-set(value):raise ValueError('Invalid structured keys')


def _cards(prepared: dict) -> dict:
    rows=[prepared.get('current_news') or {},*prepared.get('window_news',[])]
    return {row['id']:row for row in rows if row.get('id') and (row.get('title') or row.get('facts'))}


def _ordinary(prepared: dict) -> dict:
    return {k:v for k,v in _cards(prepared).items() if v.get('source')!='gassco'}


@dataclass
class FactCatalog:
    prepared: dict
    facts: list[dict]=field(default_factory=list)
    mechanisms: list[dict]=field(default_factory=list)
    conditions: list[dict]=field(default_factory=list)
    classifications: dict=field(default_factory=dict)
    versions: dict=field(default_factory=dict)

    def snapshot(self) -> dict:
        return {'version':VERSION,'as_of':self.prepared['as_of'],'session':self.prepared.get('session'),
                'facts':copy.deepcopy(self.facts),'mechanisms':copy.deepcopy(self.mechanisms),
                'conditions':copy.deepcopy(self.conditions),'baseline':copy.deepcopy(self.prepared.get('price_reference',{})),
                'history':copy.deepcopy(self.prepared.get('historical_events',[]))}


def attach_source_documents(prepared: dict, input_snapshot: dict) -> dict:
    """Bind complete stored source text by explicit known id/version, never alias order.

    Stored source_body may include a bounded publisher excerpt; it is not a
    claim that the complete publisher article was downloaded.
    """
    out=copy.deepcopy(prepared);index={r['alias']:r for r in out.get('source_index',[]) if r.get('alias')}
    now=_datetime(out['as_of']);raw_rows=[input_snapshot.get('current_news') or {},*input_snapshot.get('window_news',[])]
    if now is None:raise ValueError('Source documents require a valid as_of')
    documents={}
    for sid,card in _ordinary(out).items():
        binding=index.get(sid)
        if not binding or not binding.get('id'):raise ValueError('Ordinary source lacks an explicit source_index identity')
        candidates=[]
        for raw in raw_rows:
            if str(raw.get('id',''))!=str(binding['id']) or not _known(raw,now):continue
            if binding.get('version') is not None and raw.get('version')!=binding['version']:continue
            if binding.get('source') and raw.get('source')!=binding['source']:continue
            if binding.get('title') is not None and str(raw.get('title') or '')!=binding['title']:continue
            if str(raw.get('title') or '')!=str(card.get('title') or ''):continue
            doc={'source_id':str(binding['id']),'source_version':raw.get('version'),'source':raw.get('source'),
                 'title':str(raw.get('title') or ''),'body':source_body(raw),'entities':copy.deepcopy(raw.get('entities') or []),
                 'published_at':raw.get('published_at'),'version_seen_at':raw.get('version_seen_at'),
                 'first_seen_at':raw.get('first_seen_at'),'body_basis':'complete_stored_source_body',
                 'publisher_article_completeness':'not_asserted','known_as_of':out['as_of']}
            doc['body_sha256']=hashlib.sha256(doc['body'].encode()).hexdigest()
            identity={key:doc[key]for key in ('source_id','source_version','source','title','body')}
            doc['source_identity_sha256']=digest(identity)
            revision=_revision_evidence(raw,doc,out)
            if revision:doc['publisher_revision']=revision
            candidates.append(doc)
        unique={doc['source_identity_sha256']:doc for doc in candidates}
        if len(unique)!=1:raise ValueError('Ordinary source text identity is absent or ambiguous')
        documents[sid]=next(iter(unique.values()))
    out['source_documents']=documents
    return out


def _evidence_cards(prepared: dict) -> dict:
    evidence={};documents=prepared.get('source_documents') or {}
    for sid,card in _ordinary(prepared).items():
        doc=documents.get(sid)
        if not doc or doc.get('body_basis')!='complete_stored_source_body':raise ValueError('Complete stored source document is required')
        if doc.get('title')!=card.get('title') or doc.get('known_as_of')!=prepared['as_of']:raise ValueError('Source document does not match its frozen card/as_of')
        if hashlib.sha256(doc['body'].encode()).hexdigest()!=doc.get('body_sha256'):raise ValueError('Stored source body changed')
        identity={key:doc[key]for key in ('source_id','source_version','source','title','body')}
        if digest(identity)!=doc.get('source_identity_sha256'):raise ValueError('Source identity changed')
        # Whole original paragraphs retain punctuation, negation, abbreviations,
        # whitespace and numeric precision; no protected-clause filtering.
        parts=[('title',0,doc['title']),*[('body',i,text) for i,text in enumerate(doc['body'].splitlines(keepends=True)) if text.strip()]]
        for field,ordinal,text in parts:
            if not text.strip():continue
            eid=f"{sid}.{doc['source_identity_sha256'][:6]}{field[0]}{ordinal}"
            evidence[eid]={'evidence_id':eid,'source_id':sid,'source_field':field,'ordinal':ordinal,'text':text,
                           'original_source_id':doc['source_id'],'source_version':doc['source_version'],
                           'source_identity_sha256':doc['source_identity_sha256'],
                           'text_sha256':hashlib.sha256(text.encode()).hexdigest()}
    return evidence


def _classification_request(prepared: dict) -> tuple[str,dict,dict]:
    sources=_ordinary(prepared)
    evidence=_evidence_cards(prepared)
    revisions={sid:prepared['source_documents'][sid]['publisher_revision'] for sid in sources
               if prepared['source_documents'][sid].get('publisher_revision')}
    if revisions and set(revisions)!=set(sources):raise ValueError('Revision normalization requires one complete source per request')
    item={'type':'object','additionalProperties':False,'required':['source_id','evidence_id','fact_type','region_scope','realization','supply_chain_role','confidence'],
          'properties':{'source_id':{'type':'string','enum':list(sources)},'fact_type':{'type':'string','enum':list(TYPES)},
          'evidence_id':{'type':'string','enum':list(evidence)},
          'region_scope':{'type':'string','enum':list(REGIONS)},'realization':{'type':'string','enum':list(REALIZATIONS)},
          'supply_chain_role':{'type':'string','enum':list(ROLES)},
          'confidence':{'type':'number','minimum':0,'maximum':1}}}
    if revisions:
        prior_ids=[row['evidence_id'] for revision in revisions.values() for row in revision['previous_evidence']]
        item['required']+=['novelty','prior_evidence_ids']
        item['properties'].update(novelty={'type':'string','enum':['unchanged','added','revised']},
                                  prior_evidence_ids={'type':'array','maxItems':3,'uniqueItems':True,
                                                      'items':{'type':'string','enum':prior_ids}})
    branches=[]
    for typ,(realizations,roles,regions) in TYPE_CONTRACTS.items():
        branch=copy.deepcopy(item)
        branch['properties']['fact_type']={'type':'string','const':typ}
        for name,choices in (('realization',realizations),('supply_chain_role',roles),('region_scope',regions)):
            branch['properties'][name]={'type':'string','enum':list(choices)}
        branches.append(branch)
    schema={'type':'object','additionalProperties':False,'required':['classifications'],'properties':{
        'classifications':{'type':'array','minItems':len(sources),'maxItems':len(sources)*3,'items':{'oneOf':branches}}}}
    field_definitions=(
        'supply_chain_role describes the activity asserted by the source: upstream=raw gas extraction or field development; '
        'lng_export=liquefaction, loading or LNG export; lng_import=receiving or regasification; end_use=final gas consumption; '
        'storage=physical storage; technology=technical investigation, survey, research, equipment or testing; '
        'corporate=an explicit agreement/contract transaction, not every activity performed by a company. '
        'region_scope describes the supported physical/commodity link: europe requires a source-established European gas supply/demand connection; '
        'global_lng includes an explicit LNG liquefaction/export supply chain without requiring an identified European destination; '
        'region_only describes a local activity, while unspecified means geography is not established. Neither label alone proves an export-chain transmission is absent. '
        'The TTF analysis target, a European company or a European shareholder does not establish europe. '
        'A completed survey is not automatically corporate_contract: preserve its completed technical fact, and separately classify a material future facility plan if the source reports one.')
    schema['description']=field_definitions
    system=('Normalize source facts only. Do not forecast prices or generate explanations. Classify every source_id at least once, covering material independent claims with at most three classifications per source. '
            'Select evidence_id from the immutable title/body paragraphs of the same source_id. Do not rewrite a quote or entity. The program restores the full original paragraph. '
            'Read both title and body: a promotional engineering-contract title is not proof of physical production; select the paragraph that actually supports each claim. '
            'Classify the asserted physical/event fact, not a company benefit or the price sign. Preserve report-time versus future/historical realization. '
            'measured_supply_* are internal names for explicitly source-reported physical production/supply changes: a clear resumption statement suffices; do not invent a measured volume or independent meter verification. '
            'Future supply projects require upstream or LNG export roles. future_import_capacity is a facility fact/background, not automatically new demand. '
            'A future supply project requires an explicit commitment/approval/development of gas production or LNG export capacity. Technology validation, equipment orders, subsea trees, joint industry research, and equipment deployment alone are technology_test or corporate_contract/background; they are not new gas production, capacity, or a production-start commitment. A European company participating does not establish a European physical-supply project. '
            'An SPA, trading purchase, or procurement contract is corporate_contract/background; buying gas is not by itself additional end-use gas consumption. '
            'future_additional_lng_consumption requires an explicit separate future incremental final-use consumption claim and an established regional/global-LNG demand channel; use end_use or lng_import role. '
            'Explicit future European receiving capability or import-bottleneck relief supports future_european_receiving_capacity, not demand growth. '
            'Europe includes the UK and Norway, not only EU members. A regional US fact is region_only unless the source establishes a European/global-LNG link. '
            'market_price_observation describes a reported market price, not a physical supply/demand cause; it is background, not a new TTF driver. Use other/unspecified when unsupported. Verbatim grounding establishes quotation, not programmatically verified semantic entailment. '
            'Six short contrasts: '
            '1) "The field has resumed gas production" => measured_supply_increase, upstream, observed_as_reported; if explicitly a past historical event, historical_as_reported, never future supply risk or storage. '
            '2) "Regulator approved future development of a North Sea gas field" => future_project_approval, upstream, future, europe; not production already added. '
            '3) "The operator awarded an EPCM engineering contract" => corporate_contract, corporate, observed_as_reported for the award; not gas production or a plant opening. '
            '4) "Survey for a proposed Alaska LNG import terminal" => future_import_capacity, lng_import, future, region_only; it alone proves neither export supply nor incremental LNG consumption. '
            '5) "Maintenance next week makes capacity unavailable; actual flow is unverified" => supply_risk_increase, future; not measured_supply_decrease or proof of actual lost flow. '
            '6) "Last summer US heat raised power-sector gas demand while Henry Hub prices fell 6%" contains two distinct claims: heat-related demand => demand_increase; old Henry Hub price decline => market_price_observation. Both historical_as_reported, region_only; neither exploration nor storage, and falling prices do not turn increased demand into demand_decrease or a current TTF shock.')
    system+=' Field definitions: '+field_definitions
    compact_sources={sid:{key:copy.deepcopy(card[key])for key in ('id','time','time_precision','source','version') if key in card}for sid,card in sources.items()}
    for sid in compact_sources:
        doc=prepared['source_documents'][sid]
        compact_sources[sid].update({key:copy.deepcopy(doc[key])for key in ('source_id','source_version','published_at','version_seen_at','first_seen_at','known_as_of','body_basis')})
    visible_evidence=[{key:e[key]for key in ('evidence_id','source_id','source_field','ordinal','text')}for e in evidence.values()]
    data={'version':VERSION,'as_of':prepared['as_of'],'sources':compact_sources,'evidence':visible_evidence}
    if revisions:
        system+=(' This source has a verified current publisher-revision comparison. Publication time remains original. '
                 'For each fact, compare current evidence with previous_evidence: novelty=unchanged for a pre-existing claim, '
                 'added for a genuinely added claim, revised for a changed/retracted prior claim. A changed paragraph can contain '
                 'both an unchanged contract and a changed production claim: do not refresh every claim in it. '
                 'For revised or unchanged claims cite the supporting prior_evidence_ids; added uses an empty array. '
                 'Classify the changed physical claim relative to its prior-version claim, not the unchanged project level. '
                 'Explicit historical_as_reported remains historical regardless of publication revision. '
                 'Publisher update and local known times are distinct; neither is a new original publication time.')
        data['publisher_revisions']={sid:{key:copy.deepcopy(value) for key,value in revision.items()
                                         if key!='previous_document'} for sid,revision in revisions.items()}
    return system,data,schema


def classification_requests(prepared: dict) -> list[dict]:
    """One complete ordinary source per normalization request, then merge facts.

    Each validation context removes other ordinary cards/documents explicitly;
    canonical operator facts and non-news context are unchanged. Final catalog
    construction still requires coverage of every ordinary source.
    """
    requests=[];ordinary=_ordinary(prepared)
    for sid in ordinary:
        single=copy.deepcopy(prepared)
        current=single.get('current_news') or {}
        if current.get('id') in ordinary and current.get('id')!=sid:single['current_news']={}
        single['window_news']=[card for card in single.get('window_news',[]) if card.get('id') not in ordinary or card.get('id')==sid]
        single['source_documents']={sid:copy.deepcopy((prepared.get('source_documents') or {}).get(sid))}
        requests.append({'source_id':sid,'request':_classification_request(single),'validation_context':single})
    return requests


def _ordinary_transmission(fact: dict,kind: str) -> tuple[list[str],str]:
    """Geographic metadata and potential transmission are separate axes."""
    region=fact['region_scope'];role=fact['supply_chain_role']
    if kind=='background' or fact['historical_report']:return ['background'],'background_only'
    if role=='lng_export' and region in {'unspecified','region_only'}:
        return ['expectation','longer_term'],'global_lng_export_expectations'
    if kind=='future_supply' and region in {'region_only','unspecified'}:
        return ['expectation','longer_term'],'conditional_regional_future_supply_expectations'
    if region=='region_only':return ['background'],'regional_background'
    if region=='unspecified':return ['expectation','longer_term'],'unresolved_geography_expectations'
    scopes=['expectation','longer_term'] if fact['realization'] in {'future','conditional'} else ['expectation','current_physical']
    return scopes,'european_source_channel' if region=='europe' else 'global_lng_source_channel'


def build_catalog(prepared: dict,classifications: dict | None=None) -> FactCatalog:
    p=copy.deepcopy(prepared);c=FactCatalog(p,classifications=copy.deepcopy(classifications or {'classifications':[]}))
    c.versions={'prepared':copy.deepcopy(p),'prepared_sha256':digest(p),'classifications':copy.deepcopy(c.classifications),'source_index':copy.deepcopy(p.get('source_index',[]))}
    cards=_cards(p);now=_moment(p['as_of']);left=_moment(p['session']['window_start']);right=_moment(p['session'].get('window_end') or p['session']['cutoff_utc'])
    def add_mechanism(fact,kind,scopes,group=None,attributes=None,channel=None):
        sign,zh,en=DEFS[kind];mid='M'+str(len(c.mechanisms));entity=fact.get('entity') or fact['source_id']
        m={'id':mid,'fact_id':fact['id'],'source_id':fact['source_id'],'kind':kind,'sign':sign,
           'allowed_scopes':scopes,'group':group or entity+':'+kind,'description_zh':zh,'description_en':en,
           'attributes':attributes or {},'channel':channel or ('background_only' if scopes==['background'] else 'european_operator_announcement')}
        c.mechanisms.append(m)
        if sign:
            c.conditions.append({'id':'C'+str(len(c.conditions)),'mechanism_id':mid,'sign':sign,
                                 'kind':'regional_link_required' if scopes==['background'] or channel=='conditional_regional_future_supply_expectations' else 'transmission_strengthens'})
        if kind=='announced_constraint':
            c.conditions.append({'id':'C'+str(len(c.conditions)),'mechanism_id':mid,'sign':-1,'kind':'notice_cancelled_or_shortened'})
    for row in (p.get('evidence_ledger') or {}).get('notice_facts',[]):
        sid=row['id'];state=row.get('state');start,end=_moment(row.get('event_start_utc')),_moment(row.get('event_end_utc'))
        fact={'id':'F'+str(len(c.facts)),'source_id':sid,'kind':'gassco_notice','entity':row.get('facility') or sid,
              'region_scope':'europe','origin':'canonical_operator_fields','semantic_classification':'structured_source',
              'state':state,'source_fields':copy.deepcopy(row),'as_of':p['as_of'],
              'physical_at_as_of':'not_started' if start and start>now else 'start_reached_flow_unverified',
              'overlaps_target':bool(start and end and max(start,left)<min(end,right))}
        c.facts.append(fact)
        group=fact['entity']+':announced_supply_risk'
        if state in {'active_notice','scheduled_notice'}:add_mechanism(fact,'announced_constraint',['expectation','longer_term'],group)
        elif state=='dismissed_notice':add_mechanism(fact,'notice_withdrawn',['expectation'],fact['entity']+':revision_relief')
        else:add_mechanism(fact,'background',['background'])
        revision=(cards.get(sid) or {}).get('revision_delta') or {};known=_moment(revision.get('previous_known_at'))
        if state not in {'active_notice','scheduled_notice'} or revision.get('comparison_status')!='compared' or not known or known>now or revision.get('substantive_change') is False:continue
        change=(revision.get('changed_fields') or {}).get('event_end') or {};before,after=_moment(change.get('before')),_moment(change.get('after'))
        if before and after and after==end and before!=after:
            add_mechanism(fact,'revision_relief' if after<before else 'revision_tightening',['expectation'],
                          fact['entity']+(':revision_relief' if after<before else ':announced_supply_risk'),
                          {'field':'event_end','before':change['before'],'after':change['after']})
        change=(revision.get('capacity_changes') or {}).get('unavailable_capacity') or {}
        try:
            before,after,delta=(float(change[k])for k in ('before','after','delta'))
            valid=all(math.isfinite(v) for v in (before,after,delta)) and abs(after-before-delta)<1e-8 and after==float(row['unavailable_capacity']) and change.get('unit')==row.get('capacity_unit')
        except (KeyError,TypeError,ValueError):valid=False
        if valid and delta:
            add_mechanism(fact,'revision_tightening' if delta>0 else 'revision_relief',['expectation'],
                          fact['entity']+(':announced_supply_risk' if delta>0 else ':revision_relief'),{'field':'unavailable_capacity',**copy.deepcopy(change)})
    ordinary=_ordinary(p);evidence=_evidence_cards(p);seen=set();duplicates=set()
    _keys(c.classifications,{'classifications'},{'classifications'})
    for item in c.classifications['classifications']:
        sid=item['source_id'];typ=item['fact_type'];card=ordinary.get(sid)
        if not card or typ not in TYPE_CONTRACTS:raise ValueError('Unknown source or fact classification')
        fields={'source_id','evidence_id','fact_type','region_scope','realization','supply_chain_role','confidence'}
        if p['source_documents'][sid].get('publisher_revision'):fields|={'novelty','prior_evidence_ids'}
        _keys(item,fields,fields)
        for field,choices in zip(('realization','supply_chain_role','region_scope'),TYPE_CONTRACTS[typ]):
            if item[field] not in choices:raise ValueError(f'{typ}: {field} must be one of {list(choices)}')
        selected=evidence.get(item['evidence_id'])
        if not selected or selected['source_id']!=sid:raise ValueError('Evidence ID does not belong to the selected source')
        doc=p['source_documents'][sid];quote=selected['text']
        historical=(item['realization']=='historical_as_reported' or bool(_moment(card.get('time')) and _moment(card.get('time'))<left))
        historical,revision_timing=_revision_fact_timing(item,selected,doc,historical)
        _finite(item['confidence'],0,1)
        key=(sid,typ,item['evidence_id'])
        if key in duplicates:raise ValueError('Duplicate normalized fact')
        duplicates.add(key);seen.add(sid)
        entities=doc.get('entities') or [];entity=next((value for value in entities if isinstance(value,str) and 0<len(value)<=80),sid) if isinstance(entities,list) else sid
        fact={'id':'F'+str(len(c.facts)),'source_id':sid,'kind':typ,'entity':entity,
              'region_scope':item['region_scope'],'realization':item['realization'],'origin':'model_normalized',
              'quote_grounded':True,'semantic_entailment_programmatically_verified':False,
              'classification_confidence':item['confidence'],'quote':quote,'source_title':doc['title'],
              'evidence_id':item['evidence_id'],'evidence_source_field':selected['source_field'],
              'original_source_id':doc['source_id'],'source_version':doc['source_version'],
              'source_identity_sha256':doc['source_identity_sha256'],'quote_sha256':selected['text_sha256'],
              'reported_at':card.get('time'),'supply_chain_role':item['supply_chain_role'],
              'historical_report':historical}
        if revision_timing:fact['source_revision']=revision_timing
        c.facts.append(fact)
        kind='future_supply' if typ in {'future_project_approval','future_project_development'} else typ if typ in DEFS else 'background'
        scopes,channel=_ordinary_transmission(fact,kind)
        fact['transmission_channel']=channel
        add_mechanism(fact,kind,scopes,doc['source_id']+':'+kind,channel=channel)
    if seen!=set(ordinary):raise ValueError('Every ordinary news source must be classified, including neutral/other sources')
    if (p.get('price_reference') or {}).get('market_baseline',{}).get('available'):
        c.conditions.append({'id':'C'+str(len(c.conditions)),'mechanism_id':None,'sign':0,'kind':'baseline_persists'})
    c.versions['catalog_sha256']=digest(c.snapshot())
    return c


def _importance_choices(catalog: FactCatalog,mechanism: dict) -> list[str]:
    if mechanism['allowed_scopes']==['background']:return ['low_window_materiality']
    fact=next(f for f in catalog.facts if f['id']==mechanism['fact_id'])
    choices=['low_window_materiality','weak_indirect_link']
    if fact.get('origin')=='canonical_operator_fields' or fact.get('region_scope')=='europe':choices.insert(0,'direct_european_channel')
    if fact.get('realization') in {'future','conditional'} or fact.get('state')=='scheduled_notice':choices.append('future_expectation')
    if fact.get('origin')=='canonical_operator_fields' and mechanism['kind'] in {'revision_relief','revision_tightening'}:choices.append('marginal_revision')
    return choices


def decision_request(catalog: FactCatalog) -> tuple[str,dict,dict]:
    mechanisms=catalog.mechanisms;ids=[m['id']for m in mechanisms];conditions=[v['id']for v in catalog.conditions]
    item_branches=[]
    for m in mechanisms:
        item_branches.append({'type':'object','additionalProperties':False,'required':['mechanism_id','weight','confidence','scope','importance_reason'],'properties':{
            'mechanism_id':{'const':m['id'],'type':'string'},'weight':{'type':'number','minimum':0,'maximum':0 if m['allowed_scopes']==['background'] else 3},
            'confidence':{'type':'number','minimum':0,'maximum':1},'scope':{'type':'string','enum':m['allowed_scopes']},
            'importance_reason':{'type':'string','enum':_importance_choices(catalog,m)}}})
    refs={'type':'array','maxItems':len(ids),'uniqueItems':True,'items':{'type':'string','enum':ids}}
    history_ids=[row['id']for row in catalog.prepared.get('historical_events',[]) if row.get('id')]
    schema={'type':'object','additionalProperties':False,'required':['uses','closing','scenarios','history'],'properties':{
        'uses':{'type':'array','minItems':len(ids),'maxItems':len(ids),'items':{'oneOf':item_branches}},
        'closing':{'type':'object','additionalProperties':False,'required':['case','driver_ids','confidence'],'properties':{
            'case':{'type':'string','enum':list(CASES)},'driver_ids':refs,'confidence':{'type':'number','minimum':0,'maximum':1}}},
        'scenarios':{'type':'object','additionalProperties':False,'required':['bullish','base','bearish'],'properties':{
            key:{'type':'array','maxItems':2,'uniqueItems':True,'items':{'type':'string','enum':conditions}}for key in ('bullish','base','bearish')}},
        'history':{'type':'array','maxItems':2,'items':{'type':'object','additionalProperties':False,'required':['event_id','relation'],
                    'properties':{'event_id':{'type':'string','enum':history_ids},'relation':{'type':'string','enum':['analogue','contrast','background']}}}}}}
    for field in ('bullish','base','bearish'):
        permitted=[row['id']for row in catalog.conditions if (row['sign']>0 if field=='bullish' else row['sign']<0 if field=='bearish' else row['sign']==0)]
        schema['properties']['scenarios']['properties'][field]=({'type':'array','minItems':1,'maxItems':2,'uniqueItems':True,'items':{'type':'string','enum':permitted}}
            if permitted else {'type':'array','maxItems':0,'items':{'type':'string'}})
    if not ids:
        schema['properties']['uses'].update(minItems=0,maxItems=0,items={'type':'object'})
        schema['properties']['closing']['properties']['driver_ids']={'type':'array','maxItems':0,'items':{'type':'string'}}
    if not conditions:
        for key in ('bullish','base','bearish'):schema['properties']['scenarios']['properties'][key]={'type':'array','maxItems':0,'items':{'type':'string'}}
    if not history_ids:schema['properties']['history']={'type':'array','maxItems':0,'items':{'type':'object'}}
    system=('选择来源机制的非负权重、置信度和适用scope，以及收盘case/driver_ids；不生成事实、价格幅度、方向标签或摘要。'
            '经济sign由mechanism定义固定；同实体相关容量风险按max聚合不按公告数相加，修订缓解单列。'
            '未来供给预期可以影响期货，但不是当前实流；未知流量和未知计价不是中和证据。'
            'baseline_persists保留已知同合约基准方向；retrace/reverse需明确引用反向机制；信息不足仅按相应case声明。'
            '历史类比不是当前公告已计价或零反应的证据。每个mechanism必须显式出现一次，允许weight=0。news_dominates须引用与净作用同向的主要group贡献。')
    # Semantic stages receive the selected typed facts, not legacy regex-derived
    # mechanism candidates or protected-clause summaries. Full original source
    # documents/prepared/index remain in catalog.versions for audit and replay.
    p=catalog.prepared
    compact_mechanisms=[{key:copy.deepcopy(m[key])for key in ('id','fact_id','source_id','kind','sign','allowed_scopes','group','attributes','channel')}|{'description':m['description_zh'],
                        'weight_max':0 if m['allowed_scopes']==['background'] else 3,'importance_options':_importance_choices(catalog,m)}for m in catalog.mechanisms]
    coverage=p.get('coverage') or {}
    data={'version':VERSION,'as_of':p['as_of'],'session':copy.deepcopy(p['session']),
          'catalog':{'facts':copy.deepcopy(catalog.facts),'mechanisms':compact_mechanisms,'conditions':copy.deepcopy(catalog.conditions)},
          'previous_sessions':copy.deepcopy(p.get('previous_sessions',[])),
          'historical_events':copy.deepcopy(p.get('historical_events',[])),
          'price_reference':copy.deepcopy(p.get('price_reference',{})),
          'coverage':{key:copy.deepcopy(coverage[key])for key in ('article_count','observed_imprecise_count','risk_flags','observation_limits')if key in coverage}}
    return system,data,schema


def _label(score: float) -> str:
    return ('extreme_up' if score>=EXTREME_SCORE else 'up' if score>FLAT_SCORE else
            'extreme_down' if score<=-EXTREME_SCORE else 'down' if score<-FLAT_SCORE else 'flat')


def validate_weights(plan: dict,catalog: FactCatalog) -> dict:
    if digest(catalog.snapshot())!=catalog.versions['catalog_sha256']:raise ValueError('Canonical catalog mutated after construction')
    _keys(plan,{'uses','scenarios','history'},{'uses','scenarios','history'})
    mechanisms={m['id']:m for m in catalog.mechanisms};used={};groups={}
    fact_map={f['id']:f for f in catalog.facts}
    def active(mid):return fact_map[mechanisms[mid]['fact_id']].get('state')=='active_notice'
    for use in plan['uses']:
        _keys(use,{'mechanism_id','weight','confidence','scope','importance_reason'},{'mechanism_id','weight','confidence','scope','importance_reason'})
        mid=use['mechanism_id'];m=mechanisms.get(mid)
        if m is None or mid in used:raise ValueError('Unknown or duplicate mechanism ID')
        weight=_finite(use['weight'],0,3);confidence=_finite(use['confidence'],0,1)
        if use['importance_reason'] not in _importance_choices(catalog,m):raise ValueError('Importance reason is incompatible with this source mechanism')
        if use['scope'] not in m['allowed_scopes']:raise ValueError('Physical/future/regional scope is incompatible with the source fact')
        if use['scope']=='background' and weight:raise ValueError('Regional/background facts cannot be invented as a TTF price driver')
        used[mid]=copy.deepcopy(use);value=weight*confidence*m['sign'];key=(m['group'],m['sign'])
        if key not in groups or abs(value)>abs(groups[key]['value']) or (abs(value)==abs(groups[key]['value']) and active(mid) and not active(groups[key]['mechanism_id'])):groups[key]={'mechanism_id':mid,'value':value}
    for key,row in groups.items():
        row.update(group=key[0],sign=key[1],mechanism_ids=[mid for mid,m in mechanisms.items()if (m['group'],m['sign'])==key],
                   source_ids=list(dict.fromkeys(m['source_id']for m in mechanisms.values()if (m['group'],m['sign'])==key)))
    if set(used)!=set(mechanisms):raise ValueError('Every mechanism must be represented exactly once; use weight zero explicitly')
    score=max(-3.,min(3.,sum(row['value']for row in groups.values())));news_direction=_label(score)
    condition_map={v['id']:v for v in catalog.conditions};_keys(plan['scenarios'],{'bullish','base','bearish'},{'bullish','base','bearish'})
    for field,values in plan['scenarios'].items():
        supported=[v for v in catalog.conditions if (v['sign']>0 if field=='bullish' else v['sign']<0 if field=='bearish' else v['sign']==0)]
        if supported and not values:raise ValueError('Select at least one supported condition for each scenario sign')
        if not isinstance(values,list) or len(values)>2 or len(set(values))!=len(values):raise ValueError('Invalid scenario references')
        for cid in values:
            cond=condition_map.get(cid)
            if cond is None or (field=='bullish' and cond['sign']<=0) or (field=='bearish' and cond['sign']>=0) or (field=='base' and cond['sign']!=0):raise ValueError('Scenario sign/source mismatch')
    history_ids={row['id']for row in catalog.prepared.get('historical_events',[]) if row.get('id')}
    if not isinstance(plan['history'],list) or len(plan['history'])>2:raise ValueError('Invalid history selections')
    for use in plan['history']:
        _keys(use,{'event_id','relation'},{'event_id','relation'})
        if use['event_id'] not in history_ids or use['relation'] not in {'analogue','contrast','background'}:raise ValueError('Unprovided historical comparison')
    total=sum(u['weight']for u in used.values());confidence=sum(u['weight']*u['confidence']for u in used.values())/total if total else 0.
    out=copy.deepcopy(plan);out['_derived']={'news_score':round(score,6),'news_direction':news_direction,
        'news_confidence':round(confidence,6),'group_contributions':list(groups.values()),
        'catalog_sha256':catalog.versions['catalog_sha256']}
    return out


def weight_request(catalog: FactCatalog) -> tuple[str,dict,dict]:
    system,data,schema=decision_request(catalog)
    schema['properties'].pop('closing');schema['required'].remove('closing')
    system=('只选择所有机制的非负weight/confidence/scope、条件情景和历史引用。不要选收盘case或计算标签；后台会先按group去重聚合净作用，下一阶段才选择收盘case。'
            '每个mechanism必须显式一次，允许weight=0并必须给importance_reason；未知流量或计价不能作为零权重原因。'
            'weight含义：0为本窗口无显著重要性，0.1–0.5为弱间接/远期，0.5–1.5为较直接容量风险，1.5–3为重大有源冲击；这是指导而非硬范围路由。'
            'confidence是对本窗口价格传导/重要性判断的把握，不是公告数字真实性，不要机械全部填1。'
            '同实体相关风险取max而非逐条累加，修订缓解独立。')
    system+=' conditional_regional_future_supply_expectations仅开放条件预期：依据所附原文评估欧洲或全球LNG市场联系，缺少联系可以weight=0；开放scope不证明当前欧洲实流，也不代表已建立全球LNG联系。'
    return system,data,schema


def _closing_cases(catalog: FactCatalog,weighted: dict) -> list[str]:
    p=catalog.prepared.get('price_reference') or {};base=p.get('market_baseline') or {}
    if not (p.get('settlement_anchor') or {}).get('available'):return ['insufficient_anchor']
    d=weighted['_derived'];net=1 if d['news_direction'] in {'up','extreme_up'} else -1 if d['news_direction'] in {'down','extreme_down'} else 0
    baseline=base.get('observed_direction') if base.get('available') else None
    bsign=1 if baseline in {'up','extreme_up'} else -1 if baseline in {'down','extreme_down'} else 0
    signs={1 if row['value']>0 else -1 for row in d['group_contributions'] if row['value']}
    cases=['baseline_persists'] if base.get('available') else []
    if net:cases.append('news_dominates')
    if base.get('available') and net and bsign and net==-bsign:cases.extend(['retrace_to_flat','reverse'])
    conflict={-1,1}<=signs or (bsign and -bsign in signs)
    if conflict:cases.append('uncertain_conflict')
    if not base.get('available') and not net and not conflict:cases.append('no_directional_signal')
    if not cases:raise ValueError('No justified closing case: no intraday baseline or directional/contradictory source mechanism')
    return cases


def _verified_weights(weighted: dict,catalog: FactCatalog) -> dict:
    clean={key:copy.deepcopy(weighted[key])for key in ('uses','scenarios','history')}
    checked=validate_weights(clean,catalog)
    if weighted.get('_derived')!=checked['_derived']:raise ValueError('Weighted result or deterministic net was altered')
    return checked


def closing_request(catalog: FactCatalog,weighted: dict) -> tuple[str,dict,dict]:
    weighted=_verified_weights(weighted,catalog);_,data,schema=decision_request(catalog)
    close_schema=schema['properties']['closing'];close_schema['properties']['case']['enum']=_closing_cases(catalog,weighted)
    signs={m['id']:m['sign']for m in catalog.mechanisms}
    drivers=[u['mechanism_id']for u in weighted['uses']if u['weight']*u['confidence']>0 and signs[u['mechanism_id']]]
    close_schema['properties']['driver_ids']=({'type':'array','maxItems':len(drivers),'uniqueItems':True,'items':{'type':'string','enum':drivers}}
                                              if drivers else {'type':'array','maxItems':0,'items':{'type':'string'}})
    data['weighted']=copy.deepcopy(weighted)
    system=('后台已按同一group取max并计算news_score/news_direction及group_contributions，不要重算或改权重。'
            '仅从合法case枚举选择收盘case、driver_ids和confidence。balanced新闻不抵消已经观察到的价格变化；baseline_persists沿用已知行情基准。'
            'news_dominates需引用同净方向的主要group贡献；retrace/reverse只在去重净方向与基准相反时出现，reverse沿用该净方向。'
            'uncertain_conflict必须引用真实相反机制，不能把未知计价/流量当冲突；不足锚点仅可null。')
    return system,data,close_schema


def validate_closing(close: dict,weighted: dict,catalog: FactCatalog) -> dict:
    weighted=_verified_weights(weighted,catalog)
    _keys(close,{'case','driver_ids','confidence'},{'case','driver_ids','confidence'});_finite(close['confidence'],0,1)
    if close['case'] not in _closing_cases(catalog,weighted):raise ValueError('Closing case is incompatible with the already computed grouped net/baseline')
    mechanisms={m['id']:m for m in catalog.mechanisms};used={u['mechanism_id']:u for u in weighted['uses']}
    if not isinstance(close['driver_ids'],list) or len(set(close['driver_ids']))!=len(close['driver_ids']):raise ValueError('Invalid or duplicate closing drivers')
    drivers=[]
    for mid in close['driver_ids']:
        if mid not in used or used[mid]['weight']*used[mid]['confidence']<=0 or mechanisms[mid]['sign']==0:raise ValueError('Closing drivers must be selected nonzero source mechanisms')
        drivers.append(mechanisms[mid])
    d=weighted['_derived'];base=(catalog.prepared.get('price_reference') or {}).get('market_baseline') or {}
    case=close['case'];net=d['news_direction'];baseline=base.get('observed_direction')
    if case=='baseline_persists':direction=baseline
    elif case in {'news_dominates','retrace_to_flat','reverse'}:
        main=max((row for row in d['group_contributions'] if row['value']*d['news_score']>0),key=lambda row:abs(row['value']))
        if main['mechanism_id'] not in close['driver_ids']:raise ValueError('Closing case must cite the main same-sign grouped net contribution')
        direction='flat' if case=='retrace_to_flat' else net
    elif case in {'insufficient_anchor','no_directional_signal'}:direction=None
    else:
        signs={m['sign']for m in drivers};bsign=1 if baseline in {'up','extreme_up'} else -1 if baseline in {'down','extreme_down'} else 0
        if not ({-1,1}<=signs or (base.get('available') and bsign and -bsign in signs)):raise ValueError('Uncertain conflict needs actual cited opposing source mechanisms')
        direction=None
    out={key:copy.deepcopy(weighted[key])for key in ('uses','scenarios','history')};out['closing']=copy.deepcopy(close)
    out['_derived']={**d,'close_direction':direction,'evidence_status':'insufficient_evidence' if direction is None else 'forecast'}
    return out


def validate_plan(plan: dict,catalog: FactCatalog) -> dict:
    """Legacy/offline wrapper; live transport selects weights before closing."""
    _keys(plan,{'uses','closing','scenarios','history'},{'uses','closing','scenarios','history'})
    weighted=validate_weights({key:copy.deepcopy(plan[key])for key in ('uses','scenarios','history')},catalog)
    return validate_closing(plan['closing'],weighted,catalog)


def _fact_text(fact: dict,locale: str) -> str:
    sid=fact['source_id'];entity=fact['entity']
    if fact['kind']=='gassco_notice':
        f=fact['source_fields'];period=f"{f.get('event_start_utc','?')} → {f.get('event_end_utc','?')}";capacity=f"{f.get('unavailable_capacity','?')} {f.get('capacity_unit','?')}"
        if locale=='zh':
            state={'active_notice':'当前容量公告，实流未知','scheduled_notice':'未来容量公告，尚未开始','dismissed_notice':'仅该公告撤销，实流未知','announced_end_elapsed_unverified_flow':'公告时段已过，当前容量和实流未知'}.get(fact['state'],'公告状态未明确')
            return f"[{sid}]{entity}；{f.get('notice_type','类型未给')}；公告不可用容量{capacity}；UTC {period}；{state}。"
        state={'active_notice':'current capacity notice; actual flow unknown','scheduled_notice':'future capacity notice; not yet started','dismissed_notice':'this notice withdrawn; actual flow unknown','announced_end_elapsed_unverified_flow':'notice period elapsed; current capacity and flow unknown'}.get(fact['state'],'notice state unspecified')
        kind={'计划':'planned','非计划':'unplanned'}.get(f.get('notice_type'),'type unspecified')
        return f'[{sid}] {entity}; {kind}; announced unavailable capacity {capacity}; UTC {period}; {state}.'
    names={'future_project_approval':('未来上游/出口供应项目批准','future upstream/export supply project approval'),
           'future_project_development':('未来上游/出口供应项目开发','future upstream/export supply development'),
           'future_import_capacity':('未来LNG进口设施事实；未据此确认新增消费','future LNG import facility; additional consumption is not established'),
           'corporate_contract':('商业或工程合同；合同量不是新增产量','commercial/engineering contract; contract volume is not new production'),
           'exploration_uncommitted':('尚无承诺投产的勘探','exploration without committed production'),
           'technology_test':('技术测试；未据此确认供给变化','technology testing without an established supply change'),
           'market_price_observation':('来源报道的市场价格观察；非供需因果证据','source-reported market price observation; not physical supply-demand causality'),
           'other':('其他背景事实','other background information')}
    name=names.get(fact['kind'],DEFS.get(fact['kind'],DEFS['background'])[1:])[0 if locale=='zh' else 1]
    historic=fact.get('historical_report');stamp=fact.get('reported_at') or '?'
    regions={'europe':('来源地域为欧洲','source region European'),'global_lng':('来源涉及全球LNG','source relates to global LNG'),
             'region_only':('来源描述区域活动','source describes a regional activity'),'unspecified':('来源地域未明确','source geography unspecified')}
    channels={'background_only':('仅背景参考','background only'),'regional_background':('仅作区域背景','regional background'),
              'global_lng_export_expectations':('液化/出口链可有全球LNG预期传导','potential global LNG export-chain expectations'),
              'conditional_regional_future_supply_expectations':('区域未来供给的条件预期；欧洲/全球LNG联系待评估','conditional regional future-supply expectations; European/global-LNG link requires assessment'),
              'unresolved_geography_expectations':('仅讨论条件预期，未确认欧洲实流','conditional expectations, not verified European flow'),
              'european_source_channel':('来源所述欧洲供需渠道','source-classified European channel'),
              'global_lng_source_channel':('来源所述全球LNG渠道','source-classified global LNG channel')}
    region=regions[fact['region_scope']][0 if locale=='zh' else 1]
    channel=channels[fact['transmission_channel']][0 if locale=='zh' else 1]
    revision=fact.get('source_revision');revision_text=''
    if revision:
        if locale=='zh':revision_text=f" 修订比较={revision['novelty']}；原发布{stamp}；来源修订{revision['publisher_updated_at']}；本机获知{revision['current_known_at']}。"
        else:revision_text=f" Revision comparison={revision['novelty']}; originally published {stamp}; publisher revision {revision['publisher_updated_at']}; locally known {revision['current_known_at']}."
    if locale=='zh':return f"[{sid}]{entity}；{name}；{'原报道时点'+stamp+'的历史事实' if historic else '按原报道的未来/当前属性'}；{region}；{channel}。"+revision_text
    display_entity=entity if not any('\u4e00'<=ch<='\u9fff' for ch in entity) else 'the reported entity/project'
    return f"[{sid}] {display_entity}; {name}; {'historical as reported at '+stamp if historic else fact['realization'].replace('_',' ')}; {region}; {channel}."+revision_text


def _prior_revision_quotes(fact: dict,locale: str) -> str:
    revision=fact.get('source_revision') or {}
    rows=revision.get('prior_evidence') or []
    if not rows:return ''
    def display(row):
        if locale=='en' and any('\u4e00'<=ch<='\u9fff' for ch in row['text']):
            return f"[{row['evidence_id']}] source version {row['source_version']}; SHA-256 {row['text_sha256']}; original text retained in source metadata"
        return f"[{row['evidence_id']}] {row['text']}"
    return (' 前版证据：' if locale=='zh' else ' Prior-version evidence: ')+' | '.join(display(row) for row in rows)


def render_plan(plan: dict,catalog: FactCatalog,locale: str='zh',detail: bool=False) -> dict:
    if locale not in {'zh','en'}:raise ValueError('Unsupported rendering locale')
    original={key:copy.deepcopy(plan[key])for key in ('uses','closing','scenarios','history')};validated=validate_plan(original,catalog);derived=validated['_derived']
    mechanisms={m['id']:m for m in catalog.mechanisms};facts={f['id']:f for f in catalog.facts};used={v['mechanism_id']:v for v in plan['uses']}
    zh=locale=='zh';description='description_zh' if zh else 'description_en';evidence=[];bull=[];bear=[]
    groups=derived['group_contributions']
    ordered=sorted(used,key=lambda mid:abs(used[mid]['weight']*used[mid]['confidence']*mechanisms[mid]['sign']),reverse=True)
    all_evidence=[];individual={}
    for mid in ordered:
        use=used[mid];m=mechanisms[mid];f=facts[m['fact_id']]
        attributes=m.get('attributes') or {};revision=''
        if attributes.get('before') is not None and attributes.get('after') is not None:
            label={'event_end':('结束时刻','end time'),'unavailable_capacity':('不可用容量','unavailable capacity')}.get(attributes.get('field'),('公告字段','notice field'))[0 if zh else 1]
            unit=attributes.get('unit','')
            revision=(f"[{m['source_id']}]{f['entity']}；相对上版{label}：{attributes['before']} → {attributes['after']} {unit}。" if zh else f"[{m['source_id']}] {f['entity']}; {label} versus the prior version: {attributes['before']} -> {attributes['after']} {unit}.")
        reason=('仅背景参考' if zh else 'background only') if use['scope']=='background' else IMPORTANCE_TEXT[use['importance_reason']][0 if zh else 1]
        annotation=(f" 本窗口权重{use['weight']:g}，{reason}；传导判断置信度{use['confidence']:g}。" if zh else f" Window weight {use['weight']:g}, {reason}; transmission confidence {use['confidence']:g}.")
        text=(revision or _fact_text(f,locale))+(' 机制：' if zh else ' Mechanism: ')+m[description]+'.'+annotation
        individual[mid]=text;all_evidence.append(text)
    def group_text(row):
        m=mechanisms[row['mechanism_id']]
        members=list(dict.fromkeys(mechanisms[mid]['fact_id'] for mid in row['mechanism_ids']))
        members.sort(key=lambda fid:facts[fid].get('state')!='active_notice')
        if m['kind'] in {'announced_constraint','revision_tightening'} and len(members)>1:
            parts=[]
            for fid in members:
                f=facts[fid];raw=f['source_fields'];state=('当前' if f.get('state')=='active_notice' else '未来') if zh else ('current' if f.get('state')=='active_notice' else 'future')
                kind=raw.get('notice_type','?') if zh else {'计划':'planned','非计划':'unplanned'}.get(raw.get('notice_type'),'?')
                parts.append(f"[{f['source_id']}]{state}{kind}公告{raw.get('unavailable_capacity','?')}{raw.get('capacity_unit','?')}" if zh else f"[{f['source_id']}] {state} {kind} capacity notice {raw.get('unavailable_capacity','?')} {raw.get('capacity_unit','?')}")
            note=(f"；{facts[m['fact_id']]['entity']}相关公告同组，只计最大风险贡献{row['value']:g}；容量不是实流。" if zh else f"; related notices for {facts[m['fact_id']]['entity']} share one group; maximum risk contribution {row['value']:g}, not summed lost flow.")
            return '；'.join(parts)+note
        return individual[row['mechanism_id']]
    economic=[row for row in groups if row['sign'] and any(used[mid]['scope']!='background' for mid in row['mechanism_ids'])]
    economic.sort(key=lambda row:abs(row['value']),reverse=True)
    group_lines=[group_text(row) for row in economic]
    bull=[group_text(row) for row in economic if row['sign']>0][:3]
    bear=[group_text(row) for row in economic if row['sign']<0][:3]
    focus=next((row for row in economic if 'N0' in row['source_ids']),None)
    focus_mid=next((mid for mid in ordered if mechanisms[mid]['source_id']=='N0'),None)
    focus_text=group_text(focus) if focus else individual.get(focus_mid,'')
    evidence=list(dict.fromkeys([text for text in [focus_text,*group_lines] if text]))[:3] or all_evidence[:3]
    reversals=[individual[mid] for mid in ordered if mechanisms[mid]['kind'] in {'revision_relief','notice_withdrawn'}][:3]
    score=derived['news_score'];direction=derived['news_direction'];close_direction=derived['close_direction'];base=(catalog.prepared.get('price_reference') or {}).get('market_baseline') or {}
    signword=WORDS[locale][direction];closeword=WORDS[locale][close_direction]
    important=[]
    if focus_mid:
        use=used[focus_mid];m=mechanisms[focus_mid]
        importance=('仅背景参考' if zh else 'background only') if use['scope']=='background' else IMPORTANCE_TEXT[use['importance_reason']][0 if zh else 1]
        important.append('['+m['source_id']+']'+m[description]+('，本窗口'+importance if zh else ', '+importance))
    for row in economic:
        mid=row['mechanism_id'];m=mechanisms[mid]
        if mid!=focus_mid and len(important)<2:important.append('['+m['source_id']+']'+m[description])
    drivers_summary='；'.join(important)
    news_summary=(f'{drivers_summary}；综合权重后TTF新闻净压力指向{signword}。' if zh else
                  f'{drivers_summary}; weighted net news pressure implies the TTF price may {signword}.')
    if base.get('available'):
        unit=(catalog.prepared.get('price_reference') or {}).get('intraday',{}).get('unit')
        if not unit:raise ValueError('Observed quote unit was not supplied')
        price=(f"前日参考({base['previous_date']}) {base['previous_price']} {unit}；当前同合约 {base['latest_price']} {unit}，观测{base['observed_at']}、获知{base['available_at']}；当前对前日差{base['observed_delta']} {unit}。" if zh else
               f"Previous-day reference ({base['previous_date']}) {base['previous_price']} {unit}; current same-contract quote {base['latest_price']} {unit}, observed {base['observed_at']}, available {base['available_at']}; current-minus-prior change {base['observed_delta']} {unit}.")
    else:
        anchor=(catalog.prepared.get('price_reference') or {}).get('settlement_anchor') or {}
        price=('前日同合约比较锚点已知，但缺少可用的当日盘中基准。' if zh else 'The previous-day same-contract anchor is known, but a usable intraday baseline is unavailable.') if anchor.get('available') else ('未提供可用的前日同合约比较锚点。' if zh else 'No valid same-contract previous-day comparison anchor is available.')
    case=plan['closing']['case'];refs='、'.join('['+mechanisms[mid]['source_id']+']'for mid in plan['closing']['driver_ids'])
    reasons={'baseline_persists':('若已观察基准维持且没有足够反向传导','if the observed baseline persists without sufficient opposing transmission'),
             'news_dominates':('若所选净新闻机制主导后续交易','if the selected net news mechanisms dominate subsequent trading'),
             'retrace_to_flat':('若已引反向机制足以回吐基准涨跌','if the cited opposing mechanisms retrace the observed baseline move'),
             'reverse':('若已引反向机制足以反转已知基准','if the cited opposing mechanisms reverse the known baseline'),
             'insufficient_anchor':('缺少合法比较锚点，未给相对前日的收盘方向','the comparison anchor is missing; no close direction relative to that anchor is given'),
             'uncertain_conflict':('已引来源存在相反传导，模型未能确定其净作用','cited sources imply opposing transmission whose net effect remains unresolved'),
             'no_directional_signal':('合法前日锚点已知，但缺少盘中基准且所给新闻未提供足够方向信号','the previous-day anchor is valid, but no intraday baseline or sufficient directional news signal is available')}
    reason=reasons[case][0 if zh else 1]
    conclusion=(f'{refs}{reason}，收盘相对前日{closeword}。' if zh else f'{refs} {reason}; no directional close forecast is supplied.' if close_direction is None else f'{refs} {reason}; the close relative to the previous-day reference may {closeword}.')
    history_cards={row['id']:row for row in catalog.prepared.get('historical_events',[]) if row.get('id')}
    history=[];history_detail=[]
    for use in plan['history']:
        row=history_cards[use['event_id']];title=str(row.get('title') or row.get('event') or use['event_id']);stamp=str(row.get('time') or row.get('date') or row.get('event_date') or 'time not supplied')
        if not zh and any('\u4e00'<=ch<='\u9fff' for ch in title):title='the cited historical event'
        status=str(row.get('status') or (row.get('operational') or {}).get('event_status') or 'unspecified')
        observations=[]
        for horizon,value in (row.get('outcomes') or {}).items():
            if not isinstance(value,dict):continue
            available=value.get('available_at') or row.get('outcome_available_at')
            known=_moment(available)
            if not known or known>_moment(catalog.prepared['as_of']):continue
            supplied={key:value[key]for key in ('change','price_type','end_date','contract')if key in value}
            observations.append(f"{horizon}: "+', '.join(str(k)+'='+str(v)for k,v in supplied.items())+f", available_at={available}")
        difference=('当前公告状态按各N的当前/未来区分；历史已撤销不证明当前已恢复。' if zh else 'Current and future notice states remain separate; historical withdrawal is not proof of current restoration.')
        prefix=(f"[{use['event_id']}]{stamp} {title}；状态{status}；{use['relation']}。" if zh else f"[{use['event_id']}] {stamp} {title}; state={status}; {use['relation']}.")
        short_prefix=f"[{use['event_id']}] {stamp}; state={status}; {use['relation']}. "
        note=('已知参考变化：' if zh else 'Known reference observations: ')+(' | '.join(observations[:1]) if observations else ('未提供截至as_of可用的观察' if zh else 'none supplied as known by as_of'))
        history.append(short_prefix+note)
        history_detail.append(prefix+(' | '.join(observations) if observations else note)+('；' if zh else '; ')+difference)
    condition_map={v['id']:v for v in catalog.conditions}
    def condition(cid):
        cond=condition_map[cid]
        if cond['kind']=='baseline_persists':return '若已知同合约基准延续且无新增有源反向变化，则条件预判维持。' if zh else 'If the known same-contract baseline persists without a new sourced opposing change, the conditional outlook persists.'
        m=mechanisms[cond['mechanism_id']];sid=m['source_id']
        if cond['kind']=='regional_link_required':return f'若来源证据支持[{sid}]事项与欧洲或全球LNG供需的关联，且相应机制传导增强，才可能形成所述TTF价格压力。' if zh else f'Only if source evidence supports a European or global-LNG supply-demand link for [{sid}] and its transmission strengthens could the stated TTF pressure arise.'
        if cond['kind']=='notice_cancelled_or_shortened':return f'若[{sid}]公告约束被撤销或进一步缩短，则相应供给风险可能缓解；仍须核实实际流量。' if zh else f'If [{sid}] is withdrawn or shortened further, the corresponding supply risk may ease; actual flow still requires verification.'
        return f"若[{sid}]的{m[description]}传导增强，则可能强化相应价格方向。" if zh else f"If the transmission of [{sid}] {m[description]} strengthens, the corresponding price pressure may strengthen."
    scenarios={key:'；'.join(condition(cid)for cid in plan['scenarios'][key]) or ('若没有新的有源条件成立，则不把未观测变化当作事实。' if zh else 'If no new sourced condition is established, unobserved changes are not treated as facts.')for key in ('bullish','base','bearish')}
    ids=list(dict.fromkeys([*[f['source_id']for f in catalog.facts],*[row['event_id']for row in plan['history']]]));cards=_cards(catalog.prepared)
    source_index={row.get('alias'):row for row in catalog.prepared.get('source_index',[])}
    references=[]
    for sid in ids:
        row=source_index.get(sid,{})
        projection=row.get('source_identity_projection') or {}
        equivalent=projection.get('window_alias') if projection.get('generated_by_model') is False else None
        equivalent=equivalent or cards.get(sid,{}).get('same_as_window_news_id')
        donor=source_index.get(equivalent,{})
        references.append({'id':sid,'url':row.get('url') or donor.get('url') or cards.get(sid,{}).get('url',''),
                           'title':row.get('title') or donor.get('title') or cards.get(sid,{}).get('title','')})
    pricing='未提供公告前后可比反应，计价未知不等于尚未计价或零反应。' if zh else 'Pricing is unknown without comparable announcement-response evidence; unknown is neither unpriced nor zero reaction.'
    reaction=bool((catalog.prepared.get('evidence_ledger') or {}).get('pricing_observation',{}).get('announcement_reaction_evidence_available'))
    if reaction:pricing='输入存在公告反应资料，需按对应来源解释；不能仅据单个报价判断已计价。' if zh else 'Announcement-response evidence is supplied and must be interpreted against its sources; one quote alone does not establish pricing.'
    missing=[] if reaction else ['announcement_price_response']
    if any(f['kind']=='gassco_notice' for f in catalog.facts):missing.append('independent_actual_flow')
    result={'news_impact':{'direction':direction,'contextual_news_score':score,'confidence':derived['news_confidence'],'signal_strength':'strong' if abs(score)>=2.5 else 'moderate' if abs(score)>=1 else 'weak','summary':news_summary,'bullish_evidence':bull,'bearish_evidence':bear,'neutralizing_evidence':[]},
            'close_forecast':{'direction':close_direction,'confidence':plan['closing']['confidence'],'summary':conclusion,'basis':price+conclusion,'extreme_move_risk':'若实际传导或基准发生反转，应重新评估；未知数据不证明风险为零。' if zh else 'Reassess if transmission or the baseline reverses; missing observations do not establish zero risk.','evidence_status':derived['evidence_status']},
            'analysis':{'summary':news_summary,'evidence':evidence,'negation_or_reversal':reversals,'priced_in_comment':pricing,'matched_terms':[],'historical_comparisons':history},
            'risk_flags':[pricing],'references':references,'missing_fields':missing}
    if detail:result['detail']={'new_facts':[_fact_text(f,locale)+((' 原文：' if zh else ' Original source: ')+f['quote'] if f.get('quote') and (zh or not any('\u4e00'<=ch<='\u9fff' for ch in f['quote'])) else '')+_prior_revision_quotes(f,locale) for f in catalog.facts],'transmission':all_evidence,'daily_balance':[news_summary,*[group_text(row)+(f'；同组净压力贡献{row["value"]:g}。' if zh else f'; grouped pressure contribution {row["value"]:g}.')for row in groups if row['mechanism_id'] in individual],price],'historical_comparisons':history_detail or (['未选取已给的历史类比；不据此补造因果或价格反应。'] if zh else ['No supplied historical analogue was selected; no causal or price-response evidence is invented.']),'invalidation_conditions':[condition(v['id'])for v in catalog.conditions if v['kind']=='notice_cancelled_or_shortened'] or (['若独立来源改变所选事实或机制，应重新评估本次条件预判。'] if zh else ['If an independent source changes the selected facts or mechanisms, reassess this conditional outlook.']),'priced_in_assessment':pricing,'scenarios':scenarios,'limitations':[pricing],'references':references}
    result['_meta']={'workflow_version':VERSION,'renderer_locale':locale,'catalog_sha256':catalog.versions['catalog_sha256'],'typed_plan_sha256':digest(original),
                     'source_fact_provenance':[{'fact_id':f['id'],'origin':f['origin'],'semantic_entailment_programmatically_verified':f.get('semantic_entailment_programmatically_verified',f['origin']=='canonical_operator_fields')}for f in catalog.facts],
                     'source_quotes':[{key:f[key]for key in ('id','source_id','source_title','quote','reported_at','evidence_id','original_source_id','source_version','source_identity_sha256','quote_sha256','evidence_source_field')if key in f}for f in catalog.facts],
                     'derived':derived,'model_generated_final_prose':False,'classification_is_semantic_ground_truth':False}
    return result
