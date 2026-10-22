import asyncio

import httpx

from ttf_news.ingest import canonical_url, extract_article, news_item, parse_feed, parse_timestamp
from ttf_news import ingest
from ttf_news.store import Store


def test_missing_clock_is_not_fabricated():
    assert parse_timestamp("2026-10-14") == {"published_at": None, "published_date": "2026-10-14", "timestamp_precision": "date"}
    assert parse_timestamp("2026-10-14T11:30:00")["published_at"] is None
    assert parse_timestamp(None)["timestamp_precision"] == "unknown"
    assert parse_timestamp("2026-08-24T13:18:00+0200")["published_at"] == "2026-08-24T11:18:00+00:00"


def test_rss_timezone_and_bounded_source_summary():
    rss = '<rss><channel><item><title>Norway natural gas outage</title><link>https://example.org/news?utm_source=x</link><pubDate>Wed, 14 Oct 2026 08:30:00 -0400</pubDate><description><![CDATA[<p>Supply interruption</p>]]></description></item></channel></rss>'
    result = parse_feed(rss, {"id": "test", "feed_url": "https://example.org/feed"})[0]
    assert result["published_at"] == "2026-10-14T12:30:00+00:00"
    assert result["summary"] == "Supply interruption"
    assert result["url"] == "https://example.org/news"
    assert result["timestamp_source"] == "rss.pubDate"
    assert result["updated_at"] is None


def test_article_metadata_and_jsonld():
    html = '<meta property="og:title" content="New LNG production"><meta name="description" content="Verified source excerpt"><script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2026-10-14T08:15:00Z"}</script>'
    item = extract_article(html, "https://example.org/article", {"id": "test"}, historical=True)
    assert item["timestamp_precision"] == "exact"
    assert item["published_at"] == "2026-10-14T08:15:00+00:00"
    assert item["ingestion_kind"] == "backfill"
    assert item["summary"] == "Verified source excerpt"


def test_revision_hash_and_no_discovery_timestamp_substitution():
    original = news_item("gdelt", "https://example.org/a", "LNG supply", "", None, historical=True, timestamp_source="unknown")
    revision = news_item("gdelt", "https://example.org/a", "LNG supply revised", "", None, historical=True, timestamp_source="unknown")
    assert original["content_hash"] != revision["content_hash"]
    assert original["published_at"] is None
    assert original["first_seen_at"]


def test_canonical_url_preserves_identity_query():
    assert canonical_url("HTTPS://EXAMPLE.ORG/detail.php?id=12&utm_campaign=x#footer") == "https://example.org/detail.php?id=12"


def test_poll_failure_is_distinct_from_no_news_and_restart_deduplicates(tmp_path, monkeypatch):
    rss = '<rss><channel><item><title>Natural gas storage falls</title><link>https://example.org/a</link><pubDate>Wed, 14 Oct 2026 08:30:00 +0000</pubDate><description>Inventories lower.</description></item></channel></rss>'
    def request_handler(request):
        if request.url.path == "/eia":
            return httpx.Response(200, text=rss)
        return httpx.Response(503, text="Source unavailable")
    original_client = httpx.AsyncClient
    monkeypatch.setattr(ingest.httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(request_handler)))
    monkeypatch.setattr(ingest, "load_sources", lambda: [{"id": "eia", "name": "EIA", "feed_url": "https://example.org/eia"},
                                                        {"id": "offshore_energy", "name": "Offshore", "feed_url": "https://example.org/failed"}])
    store = Store(tmp_path / "test.db")
    result = asyncio.run(ingest.poll_sources(store))
    assert result["total_created"] == 1
    assert store.get_source_state("offshore_energy")["status"] == "failed"
    assert store.get_source_state("offshore_energy")["last_error"]
    result = asyncio.run(ingest.poll_sources(store))
    assert result["total_created"] == 0
    assert len(store.news()) == 1


def test_gassco_archive_rows_without_ids_and_real_pagination(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    month = datetime.now(timezone.utc).date().replace(day=1).isoformat()
    def table(indices):
        rows = []
        for index in indices:
            values = [f'2026-1____{index:03}', 'Troll', 'Inactive', 'Planned', 'Other unavailability',
                      month + 'T08:00:00+0200', month + 'T07:00:00+0200', month + 'T09:00:00+0200',
                      'mcm/d', '20', '15', '5', 'Maintenance', 'Finished']
            cells = '<td><a href="/details?id=archive-' + str(index) + '">' + values[0] + '</a></td>'
            cells += ''.join('<td>' + value + '</td>' for value in values[1:])
            rows.append('<tr>' + cells + '</tr>')  # Archive intentionally has no tr[id].
        return '<table class="msgTable">' + ''.join(rows) + '</table>'
    requested_pages = []
    def handler(request):
        if request.url.path == '/archive':
            number = int(request.url.params.get('page.page', '1'))
            requested_pages.append(number)
            return httpx.Response(200, text=table(range(1, 21) if number == 1 else [21]) + 'Total records: 21')
        return httpx.Response(200, text='<a href="/archive">PAST EVENTS</a>' + table([1]))
    original_client = httpx.AsyncClient
    monkeypatch.setattr(ingest.httpx, 'AsyncClient', lambda **kwargs: original_client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(ingest, 'load_sources', lambda: [{'id': 'gassco', 'name': 'Gassco', 'live_url': 'https://example.org/'}])
    store = Store(tmp_path / 'gassco.db')
    result = asyncio.run(ingest.poll_sources(store, historical=True, start_date=month))
    assert requested_pages == [1, 2]
    assert result['total_created'] == 21
    assert len(store.news(limit=100)) == 21
    assert all(item['timestamp_precision'] == 'exact' for item in store.news())


def test_rate_limited_source_waits_at_least_fifteen_minutes(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    calls = []
    def handler(request):
        calls.append(request.url)
        return httpx.Response(429, headers={'Retry-After': '1'}, text='Rate limited')
    original_client = httpx.AsyncClient
    monkeypatch.setattr(ingest.httpx, 'AsyncClient', lambda **kwargs: original_client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(ingest, 'load_sources', lambda: [{'id': 'eia', 'name': 'Rate limit fixture', 'feed_url': 'https://example.org/'}])
    store = Store(tmp_path / 'limited.db')
    first = asyncio.run(ingest.poll_sources(store))
    assert first['sources'][0]['status'] == 'failed'
    state = store.get_source_state('eia')
    assert state['rate_limit_backoff_seconds'] >= 900
    assert datetime.fromisoformat(state['next_poll_at']) > datetime.now(timezone.utc)
    second = asyncio.run(ingest.poll_sources(store))
    assert second['sources'][0]['status'] == 'backoff'
    assert len(calls) == 1


def test_offshore_recovers_sleep_gap_without_backdating_arrival(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime
    now = datetime.now(timezone.utc)
    rss = '<rss><channel><item><title>Current natural gas news</title><link>https://example.org/current</link><pubDate>' + format_datetime(now) + '</pubDate><description>Current source summary.</description></item></channel></rss>'
    missed = now - timedelta(hours=36)
    requested = []
    def handler(request):
        if request.url.path == '/api/articles':
            requested.append(request.url.params)
            return httpx.Response(200, json={'docs': [{'id': 1, 'title': 'Missed LNG supply report', 'slug': 'missed-lng-report',
                                                       'publishedAt': missed.isoformat(), 'updatedAt': None, '_status': 'published',
                                                       'meta': {'description': 'Source excerpt about an LNG supply interruption.'}}],
                                                   'hasNextPage': False, 'totalDocs': 1})
        return httpx.Response(200, text=rss)
    original_client = httpx.AsyncClient
    monkeypatch.setattr(ingest.httpx, 'AsyncClient', lambda **kwargs: original_client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(ingest, 'load_sources', lambda: [{'id': 'offshore_energy', 'name': 'Offshore',
                                                       'feed_url': 'https://example.org/feed', 'api_url': 'https://example.org/api/articles'}])
    store = Store(tmp_path / 'wake.db')
    store.set_source_state('offshore_energy', {'last_success_at': (now - timedelta(days=2)).isoformat(),
                                              'archive_cursor': 29, 'historical_pages': 28, 'backfill_completed_at': (now - timedelta(days=2)).isoformat()})
    result = asyncio.run(ingest.poll_sources(store))
    assert result['total_created'] == 2
    assert requested and requested[0]['page'] == '1'
    recovered = next(row for row in store.news() if row['title'] == 'Missed LNG supply report')
    assert recovered['published_at'] == missed.isoformat()
    assert datetime.fromisoformat(recovered['first_seen_at']) >= now
    assert recovered['ingestion_kind'] == 'live'
    assert store.get_source_state('offshore_energy')['archive_cursor'] == 29
    assert store.get_source_state('offshore_energy')['last_gap_recovery_at']


def test_partial_archive_clears_old_completed_marker_in_merged_checkpoint(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    month = datetime.now(timezone.utc).date().replace(day=1).isoformat()
    values = ['2026-001____001', 'Troll', 'Active', 'Planned', 'Other unavailability', month + 'T08:00:00+0200',
              month + 'T09:00:00+0200', month + 'T10:00:00+0200', 'mcm/d', '20', '15', '5', 'Maintenance', 'Published']
    cells = '<td><a href="/details?id=current">' + values[0] + '</a></td>' + ''.join('<td>' + value + '</td>' for value in values[1:])
    def handler(request):
        if request.url.path == '/archive':
            return httpx.Response(200, text='<table class="msgTable"></table>Total records: 0')
        return httpx.Response(200, text='<a href="/archive">PAST EVENTS</a><table class="msgTable"><tr>' + cells + '</tr></table>')
    original_client = httpx.AsyncClient
    monkeypatch.setattr(ingest.httpx, 'AsyncClient', lambda **kwargs: original_client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(ingest, 'load_sources', lambda: [{'id': 'gassco', 'name': 'Gassco', 'live_url': 'https://example.org/'}])
    store = Store(tmp_path / 'partial.db')
    store.set_source_state('gassco', {'backfill_completed_at': 'old_success', 'custom_field': 'preserve'})
    result = asyncio.run(ingest.poll_sources(store, historical=True, start_date=month))
    state = store.get_source_state('gassco')
    assert result['sources'][0]['status'] == 'partial'
    assert state['backfill_completed_at'] is None
    assert state['custom_field'] == 'preserve'


def test_offshore_broken_rss_redirect_uses_bounded_api_and_preserves_failure(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    previous_success = now - timedelta(hours=4)
    requests = []
    def handler(request):
        requests.append(request.url)
        if request.url.path == '/feed':
            if request.url.host == 'offshore.example.org':
                return httpx.Response(308, headers={'Location': 'https://www.offshore.example.org/feed'})
            return httpx.Response(404, text='Unknown domain')
        return httpx.Response(200, json={'docs': [{'id': 42, 'title': 'LNG supply update after RSS failure', 'slug': 'lng-after-feed-failure',
                                                   'publishedAt': now.isoformat(), 'updatedAt': None, '_status': 'published',
                                                   'meta': {'description': 'Source LNG supply summary.'}}],
                                               'hasNextPage': False, 'totalDocs': 1})
    original_client = httpx.AsyncClient
    monkeypatch.setattr(ingest.httpx, 'AsyncClient', lambda **kwargs: original_client(transport=httpx.MockTransport(handler), follow_redirects=True))
    monkeypatch.setattr(ingest, 'load_sources', lambda: [{'id': 'offshore_energy', 'name': 'Offshore',
                                                       'feed_url': 'https://offshore.example.org/feed',
                                                       'api_url': 'https://offshore.example.org/api/articles'}])
    store = Store(tmp_path / 'rss-fallback.db')
    store.set_source_state('offshore_energy', {'last_success_at': previous_success.isoformat(), 'last_complete_api_success_at': previous_success.isoformat(), 'archive_cursor': 29,
                                              'historical_pages': 28, 'historical_status': 'ok', 'backfill_completed_at': previous_success.isoformat()})
    result = asyncio.run(ingest.poll_sources(store))
    state = store.get_source_state('offshore_energy')
    assert result['total_created'] == 1 and result['sources'][0]['status'] == 'partial'
    assert state['live_transport'] == 'api_primary' and state['last_success_at']
    assert state['failures'] and 'www.offshore.example.org/feed' in state['failures'][-1]['error']
    api = next(url for url in requests if url.path == '/api/articles')
    expected_start = (previous_success - timedelta(days=1)).date().isoformat() + 'T00:00:00.000Z'
    assert api.params['where[and][0][or][0][publishedAt][greater_than_equal]'] == expected_start
    assert api.params['page'] == '1'
    assert state['archive_cursor'] == 29 and state['historical_status'] == 'ok'
    fresh = store.news()[0]
    assert fresh['ingestion_kind'] == 'live'
    assert datetime.fromisoformat(fresh['first_seen_at']) >= now


def test_offshore_first_start_rss_failure_only_queries_two_recent_days(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    api_calls = []
    def handler(request):
        if request.url.path == '/feed':
            return httpx.Response(503, text='Temporary publisher RSS error')
        api_calls.append(request.url)
        return httpx.Response(200, json={'docs': [], 'hasNextPage': False, 'totalDocs': 0})
    original_client = httpx.AsyncClient
    monkeypatch.setattr(ingest.httpx, 'AsyncClient', lambda **kwargs: original_client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(ingest, 'load_sources', lambda: [{'id': 'offshore_energy', 'name': 'Offshore',
                                                       'feed_url': 'https://example.org/feed', 'api_url': 'https://example.org/api/articles'}])
    store = Store(tmp_path / 'first-rss-failure.db')
    result = asyncio.run(ingest.poll_sources(store))
    assert result['sources'][0]['status'] == 'partial'
    assert len(api_calls) == 1
    assert api_calls[0].params['where[and][0][or][0][publishedAt][greater_than_equal]'] == (now - timedelta(days=2)).date().isoformat() + 'T00:00:00.000Z'
    assert store.get_source_state('offshore_energy')['live_transport'] == 'api_primary'
