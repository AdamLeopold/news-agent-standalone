"""Public source excerpts, bounded incremental enrichment and immutable history."""
import copy
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest

from ttf_news import ingest
from ttf_news.ingest import Fetcher, attach_fact_excerpt, bounded_fact_excerpt, extract_article, news_item, parse_feed
from ttf_news.store import Store


UTC=timezone.utc


def base_item(**updates):
    item=news_item("eia","https://publisher.test/article","Natural gas update","Short source metadata summary.",
                   (datetime.now(UTC)-timedelta(hours=1)).isoformat(),historical=False,timestamp_source="rss.pubDate")
    item.update(updates)
    return item


def source(**updates):
    return {"id":"eia","feed_url":"https://publisher.test/feed","archive_url":"https://publisher.test/archive",**updates}


def html(paragraph, *, premium=False):
    flag='<script type="application/ld+json">{"@type":"NewsArticle","isAccessibleForFree":false}</script>' if premium else ""
    return '<meta property="og:title" content="Natural gas update"><meta name="description" content="Short source metadata summary.">'+flag+'<article><p>'+paragraph+'</p></article>'


def test_complete_negation_and_quantity_paragraphs_survive_bounded_selection():
    facts="Norway gas exports to Europe decreased by 20 mcm/d. Actual flows have not restarted and the notice is not a verified production recovery."
    qualifier="However, the operator says this is a capacity restriction, not a confirmed loss of physical gas flows."
    excerpt,selection=bounded_fact_excerpt(["Corporate background. "*200,facts,qualifier])
    assert facts in excerpt and qualifier in excerpt
    assert len(excerpt)<=1600
    assert "…" not in excerpt
    assert selection["oversized_paragraph_count"]==1
    assert selection["paragraph_indices"]==[2,3]


def test_public_html_retains_fact_provenance_without_replacing_meta_summary():
    facts="European gas flows increased by 40 mcm/d, but full restoration is not confirmed and further outages remain possible."
    item=extract_article(html(facts),"https://publisher.test/article",source(),historical=True)
    assert item["summary"]=="Short source metadata summary."
    assert item["fact_excerpt"]==facts
    assert item["fact_excerpt_provenance"]["method"]=="public_article_paragraphs"
    assert item["fact_excerpt_provenance"]["access"]=="public"
    assert item["fact_excerpt_provenance"]["url"]==item["url"]
    assert item["relevance_metadata"]["scope"]=="direct"


def test_premium_html_keeps_only_metadata_and_never_body_even_without_description():
    private="SECRET PREMIUM BODY: gas flows increased 99 mcm/d in Europe."
    content=html(private,premium=True).replace('<meta name="description" content="Short source metadata summary.">',"")
    item=extract_article(content,"https://publisher.test/premium",source(),historical=True)
    assert item["is_premium"] is True
    assert item["fact_excerpt_status"]=="premium_metadata_only"
    assert not item.get("fact_excerpt") and not item["summary"]
    assert private not in json.dumps(item)


def test_public_rss_content_can_add_paragraphs_without_article_fetch():
    rss='''<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><item><title>Natural gas update</title><link>https://publisher.test/article</link><description>Short metadata</description><content:encoded><![CDATA[<p>Norway pipeline flows to Europe fell by 20 mcm/d; restart is not confirmed.</p>]]></content:encoded></item></channel></rss>'''
    item=parse_feed(rss,source())[0]
    assert "restart is not confirmed" in item["fact_excerpt"]
    assert item["fact_excerpt_provenance"]["method"]=="public_rss_content"


def test_premium_rss_content_is_not_persisted():
    rss='''<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><item><title>Natural gas update</title><link>https://publisher.test/article</link><category>Premium</category><description>Public preview</description><content:encoded><![CDATA[<p>PRIVATE BODY: natural gas flows increased by 77 mcm/d.</p>]]></content:encoded></item></channel></rss>'''
    item=parse_feed(rss,source())[0]
    assert item["is_premium"] is True
    assert not item.get("fact_excerpt")
    assert "PRIVATE BODY" not in json.dumps(item)


@pytest.mark.asyncio
@pytest.mark.parametrize("premium",[False,True])
async def test_payload_content_is_used_when_metadata_exists_but_premium_content_is_skipped(tmp_path,premium):
    paragraph="European gas exports rose by 30 mcm/d, but the operator does not confirm full flow restoration."
    published=(datetime.now(UTC)-timedelta(days=1)).isoformat()
    payload={"docs":[{"id":5,"slug":"natural-gas-update","title":"Natural gas update","publishedAt":published,"_status":"published", "isPremium":premium,
                      "meta":{"description":"Short source metadata summary."},
                      "content":[{"content":{"root":{"children":[{"type":"paragraph","children":[{"type":"text","text":paragraph}]}]}}}]}],
             "hasNextPage":False,"totalDocs":1}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=payload))) as client:
        db=Store(tmp_path/"payload.sqlite3")
        fetch=Fetcher(client,source(id="offshore_energy",api_url="https://publisher.test/api/articles"),{},db,True,[])
        items=await ingest.ingest_offshore(fetch,(datetime.now(UTC)-timedelta(days=2)).date())
    assert items[0]["summary"]=="Short source metadata summary."
    if premium:
        assert not items[0].get("fact_excerpt")
        assert paragraph not in json.dumps(db.news())
    else:
        assert items[0]["fact_excerpt"]==paragraph
        assert items[0]["fact_excerpt_provenance"]["method"]=="public_publisher_payload_paragraphs"


@pytest.mark.asyncio
async def test_unchanged_rss_poll_keeps_enrichment_and_does_not_refetch_or_oscillate(tmp_path):
    calls=[]
    body="Norway actual gas flows to Europe decreased by 20 mcm/d; restart has not been confirmed."
    def handler(request):
        calls.append(str(request.url));return httpx.Response(200,text=html(body))
    db=Store(tmp_path/"poll.sqlite3");feed=base_item();state={}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetch=Fetcher(client,source(),state,db,False,[])
        first=(await ingest.enrich_live_facts(fetch,[copy.deepcopy(feed)]))[0]
        saved=db.upsert_news(first)
        second=(await ingest.enrich_live_facts(fetch,[copy.deepcopy(feed)]))[0]
        repeated=db.upsert_news(second)
    assert len(calls)==1
    assert repeated["fact_excerpt"]==body
    assert repeated["source_revision"]==saved["source_revision"]
    assert repeated["content_hash"]==saved["content_hash"]
    assert repeated["version"]==1 and repeated["_revised"] is False
    with db.connection() as conn:
        assert conn.execute("select count(*) from news_versions").fetchone()[0]==1


def test_store_metadata_only_writer_cannot_downgrade_an_identical_enriched_record(tmp_path):
    db=Store(tmp_path/"store-stable.sqlite3")
    bare=base_item();enriched=attach_fact_excerpt(copy.deepcopy(bare),["Actual gas flows to Europe increased by 40 mcm/d, but the operator has not confirmed full restart."],method="test_public_paragraphs")
    first=db.upsert_news(enriched)
    metadata_only=db.upsert_news(copy.deepcopy(bare))
    third=db.upsert_news(copy.deepcopy(enriched))
    assert metadata_only["version"]==third["version"]==first["version"]==1
    assert metadata_only["fact_excerpt"]==first["fact_excerpt"]
    assert metadata_only["fact_excerpt_provenance"]==first["fact_excerpt_provenance"]
    assert metadata_only["fact_excerpt_created_at"]==first["fact_excerpt_created_at"]
    assert metadata_only["source_revision"]==first["source_revision"]
    assert metadata_only["content_hash"]==first["content_hash"]


def test_changed_publisher_metadata_does_not_carry_stale_excerpt(tmp_path):
    db=Store(tmp_path/"store-changed.sqlite3");bare=base_item()
    first=db.upsert_news(attach_fact_excerpt(copy.deepcopy(bare),["Gas exports to Europe declined by 20 mcm/d; actual restart is not confirmed."],method="test_public"))
    changed=copy.deepcopy(bare);changed.update(title="Natural gas restart confirmed",updated_at=datetime.now(UTC).isoformat())
    later=db.upsert_news(changed)
    assert later["version"]==2 and later["_revised"] is True
    assert not later.get("fact_excerpt")
    with db.connection() as conn:
        old=json.loads(conn.execute("select data from news_versions where news_id=? and version=1",(first["id"],)).fetchone()[0])
    assert old["fact_excerpt"]==first["fact_excerpt"]


def test_missing_feed_updated_time_is_not_retraction_but_explicit_change_is_revision(tmp_path):
    db=Store(tmp_path/"clock-stable.sqlite3");bare=base_item()
    enriched=attach_fact_excerpt(copy.deepcopy(bare),["Gas supply to Europe increased by 40 mcm/d; full restart is not confirmed."],method="test_public")
    enriched["updated_at"]="2026-10-20T11:10:00+00:00"
    enriched["timestamp_source"]="publisher API publishedAt"
    first=db.upsert_news(enriched)
    metadata_only=copy.deepcopy(bare);metadata_only["updated_at"]=None
    repeated=db.upsert_news(metadata_only)
    assert repeated["version"]==1 and repeated["updated_at"]==first["updated_at"]
    assert repeated["fact_excerpt_provenance"]==first["fact_excerpt_provenance"]
    assert repeated["source_revision"]==first["source_revision"]
    inline=copy.deepcopy(enriched)
    inline.update(updated_at=None,timestamp_source="rss.pubDate")
    inline["fact_excerpt_provenance"]["method"]="public_rss_content"
    repeated_inline=db.upsert_news(inline)
    assert repeated_inline["version"]==1
    assert repeated_inline["fact_excerpt_provenance"]==first["fact_excerpt_provenance"]
    explicit=copy.deepcopy(bare);explicit["updated_at"]="2026-10-20T11:20:00+00:00"
    changed=db.upsert_news(explicit)
    assert changed["version"]==2 and not changed.get("fact_excerpt")


@pytest.mark.asyncio
async def test_rss_missing_updated_clock_reuses_api_enrichment_without_repeated_fetch(tmp_path):
    db=Store(tmp_path/"clock-cache.sqlite3");bare=base_item()
    enriched=attach_fact_excerpt(copy.deepcopy(bare),["Gas supply to Europe increased by 40 mcm/d; full restart is not confirmed."],method="public_publisher_payload_paragraphs")
    enriched["updated_at"]="2026-10-20T11:10:00+00:00"
    db.upsert_news(enriched)
    calls=[];state={}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:(calls.append(str(request.url)) or httpx.Response(200,text=html(enriched["fact_excerpt"]))))) as client:
        fetch=Fetcher(client,source(),state,db,False,[])
        for _ in range(3):
            saved=db.upsert_news((await ingest.enrich_live_facts(fetch,[copy.deepcopy(bare)]))[0])
    assert len(calls)==1
    assert saved["version"]==1 and saved["updated_at"]==enriched["updated_at"]


@pytest.mark.asyncio
async def test_same_metadata_excerpt_update_is_newly_known_not_retroactive(tmp_path):
    db=Store(tmp_path/"revisions.sqlite3");feed=base_item();state={};body=["Norway flows to Europe decreased by 20 mcm/d, and restart is not confirmed."]
    clock=datetime.now(UTC);t1=clock.isoformat();t2=(clock+timedelta(minutes=2)).isoformat()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,text=html(body[0])))) as client:
        fetch=Fetcher(client,source(),state,db,False,[])
        with patch("ttf_news.ingest.now_iso",return_value=t1),patch("ttf_news.store.iso",return_value=t1):
            first=db.upsert_news((await ingest.enrich_live_facts(fetch,[copy.deepcopy(feed)]))[0])
            analysis=db.create_analysis({"as_of":t1,"session":{"trade_date":first["trade_date"]},"current_news":copy.deepcopy(first)},kind="manual")
        body[0]="Norway actual flows to Europe increased by 40 mcm/d after a verified restart; the prior outage is over."
        for entry in state["fact_excerpt_cache"].values():
            entry["next_check_at"]=(clock-timedelta(seconds=1)).isoformat()
        with patch("ttf_news.ingest.now_iso",return_value=t2),patch("ttf_news.store.iso",return_value=t2):
            second=db.upsert_news((await ingest.enrich_live_facts(fetch,[copy.deepcopy(feed)]))[0])
    assert second["version"]==2 and second["version_seen_at"]==t2
    assert second["fact_excerpt_created_at"]==t2
    assert second["first_seen_at"]==first["first_seen_at"]
    assert first["fact_excerpt"]!=second["fact_excerpt"]
    assert db.window_news(first["trade_date"],t1)[0]["fact_excerpt"]==first["fact_excerpt"]
    assert db.window_news(second["trade_date"],t2)[0]["fact_excerpt"]==second["fact_excerpt"]
    assert db.analysis(analysis["id"],include_input=True)["input_snapshot"]["current_news"]["fact_excerpt"]==first["fact_excerpt"]


@pytest.mark.asyncio
async def test_detail_request_count_is_bounded_and_known_premium_never_fetched(tmp_path):
    calls=[]
    db=Store(tmp_path/"bounded.sqlite3")
    items=[base_item(url="https://publisher.test/article-"+str(index)) for index in range(6)]
    items[0]["is_premium"]=True
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:(calls.append(str(request.url)) or httpx.Response(200,text=html("Actual gas supply to Europe fell by 20 mcm/d and restart is not confirmed."))))) as client:
        fetch=Fetcher(client,source(fact_excerpt_max_articles_per_poll=2),{},db,False,[])
        result=await ingest.enrich_live_facts(fetch,items)
    assert len(calls)==2 and fetch.request_count==2
    assert all(not url.endswith("article-0") for url in calls)
    assert len(result)==6  # Broad/weak or deferred records remain in the dataset.
    assert not result[0].get("fact_excerpt")


@pytest.mark.asyncio
async def test_detail_rate_limit_preserves_backoff_and_metadata_news(tmp_path,monkeypatch):
    calls=[]
    rss='<rss><channel><item><title>Natural gas update</title><link>https://publisher.test/article</link><description>Public source summary.</description></item></channel></rss>'
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200,text=rss) if request.url.path=="/feed" else httpx.Response(429,headers={"Retry-After":"1"},text="Rate limited")
    original_client=httpx.AsyncClient
    monkeypatch.setattr(ingest.httpx,"AsyncClient",lambda **kwargs:original_client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(ingest,"load_sources",lambda:[source(name="Publisher")])
    db=Store(tmp_path/"rate-limit.sqlite3")
    first=await ingest.poll_sources(db)
    assert first["total_created"]==1
    state=db.get_source_state("eia")
    assert state["rate_limit_backoff_seconds"]>=900
    assert state["next_poll_at"]
    second=await ingest.poll_sources(db)
    assert second["sources"][0]["status"]=="backoff"
    assert len(calls)==2


def test_scope_metadata_is_source_faithful_and_does_not_assign_model_direction():
    direct=ingest.relevance_metadata("European gas pipeline","Exports to Europe are not confirmed.")
    indirect=ingest.relevance_metadata("Qatar LNG project","A future LNG export terminal.")
    background=ingest.relevance_metadata("Jamaica oil and gas license","Undrilled offshore acreage has no supply destination.")
    assert direct["scope"]=="direct"
    assert indirect["scope"]=="indirect"
    assert background["scope"] in {"indirect","background"}
    denied=ingest.relevance_metadata("New gas exploration project","There are no European supply arrangements and no production date.")
    assert denied["scope"]=="background"
    assert denied["european_channel_explicitly_denied"] is True
    assert "direction" not in json.dumps([direct,indirect,background]).replace("not_price_direction","")
