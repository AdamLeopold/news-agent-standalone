"""Incremental public-source ingestion. Never invent a publication time.

All source timestamps and short excerpts retain their provenance. Publication
dates without a clock stay date-only; Store decides their uncertain day bucket.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import feedparser
import httpx
from bs4 import BeautifulSoup

from .store import Store, _same_publisher_metadata

LOG = logging.getLogger(__name__)
UTC = timezone.utc
USER_AGENT = "TTFNewsResearch/1.0 (public news metadata and short summaries; local research)"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "data" / "sources.json"
GAS_PATTERN = re.compile(r"\b(natural gas|liquefied natural gas|lng|ttf|gas|gassco|gullfaks|troll|snohvit|snøhvit|hammerfest|henry hub|freeport|sabine pass|corpus christi|regasification|fsru|methane emissions)\b", re.I)
FACT_EXCERPT_LIMIT = 1600
_FACT_TERMS = re.compile(r"\b(?:gas|LNG|TTF|supply|flows?|outage|restart|restor\w*|demand|storage|inventor\w*|pipeline|export\w*|import\w*|weather|capacity)\b|供气|天然气|供应|流量|需求|库存|复产|中断",re.I)
_EUROPE_TERMS = re.compile(r"\b(?:Europe\w*|TTF|Dutch|Netherlands|Germany|Norway|Norwegian|Britain|UK|North Sea|Mediterranean)\b|欧洲|荷兰|挪威|德国|英国",re.I)
_QUALIFIER_TERMS = re.compile(r"\b(?:not|no|without|unlikely|however|but|although|despite|until|only|expected|planned)\b|未|不|没有|但|预计|计划",re.I)


def relevance_metadata(title: str, summary: str, excerpt: str = "") -> dict:
    """Source-term scope, never a prediction or a reason to discard a news item."""
    text=" ".join((title,summary,excerpt))
    europe=sorted({match.group(0) for match in _EUROPE_TERMS.finditer(text)})
    channels=sorted({match.group(0) for match in _FACT_TERMS.finditer(text)})
    denied_channel=bool(re.search(r"\bno\s+(?:confirmed\s+)?European\s+(?:supply|delivery|export)\b|"
                                 r"\b(?:does|do|will)\s+not\s+(?:supply|export|deliver).{0,25}\bEurope\b|"
                                 r"\bnot\s+(?:destined|bound)\s+for\s+Europe\b|没有.{0,15}(?:欧洲供气|向欧洲供气)|不向欧洲供气",text,re.I))
    scope="direct" if europe and channels and not denied_channel else "indirect" if re.search(r"\bLNG\b|regas|liquef|pipeline|export|import|supply|液化天然气|供气|供应",text,re.I) and not denied_channel else "background"
    return {"scope":scope,"basis":"explicit_source_terms_not_price_direction",
            "evidence_terms":(europe+channels)[:20],"european_channel_explicitly_denied":denied_channel,
            "scope_is_not_verified_price_impact":True}


def bounded_fact_excerpt(paragraphs: list[str], limit: int = FACT_EXCERPT_LIMIT) -> tuple[str,dict]:
    """Choose complete source paragraphs; never truncate away a negation tail."""
    candidates=[]
    seen=set()
    oversized=0
    for index,raw in enumerate(paragraphs):
        paragraph=clean_text(raw,limit=1_000_000)
        if not paragraph or paragraph in seen or re.match(r"^(?:Read also|Related|Subscribe|Sign up|Advertisement)\b",paragraph,re.I):
            continue
        seen.add(paragraph)
        if len(paragraph)>limit:
            oversized+=1
            continue
        if len(paragraph)<25:
            continue
        score=min(8,len(_FACT_TERMS.findall(paragraph)))+5*bool(_EUROPE_TERMS.search(paragraph))+3*bool(_QUALIFIER_TERMS.search(paragraph))+2*bool(re.search(r"\d",paragraph))
        candidates.append((index,paragraph,score))
    selected=[];used=0
    for index,paragraph,score in sorted(candidates,key=lambda p:(-p[2],p[0])):
        if len(selected)>=5:
            break
        required=len(paragraph)+(2 if selected else 0)
        if used+required<=limit:
            selected.append((index,paragraph));used+=required
    selected.sort()
    text="\n\n".join(paragraph for _,paragraph in selected)
    return text,{"paragraph_indices":[index+1 for index,_ in selected],"selection":"bounded_complete_source_paragraphs",
                 "omitted_paragraph_count":len(candidates)+oversized-len(selected),
                 "oversized_paragraph_count":oversized,"character_limit":limit}


def _refresh_fact_identity(item: dict) -> None:
    stamp={key:item.get(key) for key in ("published_at","published_date","timestamp_precision")}
    base=item["title"]+"\n"+item["summary"]+"\n"+json.dumps(stamp,sort_keys=True)
    if item.get("fact_excerpt"):
        base+="\n"+item["fact_excerpt"]
    digest=hashlib.sha256(base.encode()).hexdigest()
    item.update(content_hash=digest,source_revision=digest[:16])


def attach_fact_excerpt(item: dict, paragraphs: list[str], *, method: str, is_premium: bool = False) -> dict:
    if is_premium:
        for field in ("fact_excerpt","fact_excerpt_created_at","fact_excerpt_provenance"):
            item.pop(field,None)
        item.update(is_premium=True,fact_excerpt_status="premium_metadata_only")
        item["category"],item["entities"]=classify(item["title"],item["summary"])
        item["relevance_metadata"]=relevance_metadata(item["title"],item["summary"])
        _refresh_fact_identity(item)
        return item
    excerpt,selection=bounded_fact_excerpt(paragraphs)
    if excerpt:
        observed=now_iso()
        item.update(fact_excerpt=excerpt,fact_excerpt_created_at=observed,fact_excerpt_status="public_excerpt",
                    fact_excerpt_provenance={"url":item["url"],"method":method,"retrieved_at":observed,"access":"public",**selection})
        item["category"],item["entities"]=classify(item["title"],item["summary"]+" "+excerpt)
        _refresh_fact_identity(item)
    item["relevance_metadata"]=relevance_metadata(item["title"],item["summary"],excerpt)
    return item


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def clean_text(value: str | None, limit: int = 1800) -> str:
    """A bounded source excerpt, not an LLM-generated or full-text mirror."""
    soup = BeautifulSoup(value or "", "html.parser")
    for el in soup(["script", "style", "noscript"]):
        el.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def canonical_url(url: str) -> str:
    p = urlsplit(url.strip())
    pairs = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), urlencode(pairs), ""))


def parse_timestamp(value: str | None, *, default_zone: str | None = None) -> dict:
    """Return exact only for a supplied clock AND a known zone; date otherwise."""
    if not value:
        return {"published_at": None, "published_date": None, "timestamp_precision": "unknown"}
    value = value.strip()
    original_value = value
    if re.match(r"^\d{4}-\d{2}-\d{2}[T ]", value):
        value = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return {"published_at": None, "published_date": value, "timestamp_precision": "date"}
    parsed = None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(original_value)
        except (TypeError, ValueError, IndexError):
            for fmt in ("%B %d, %Y", "%d %B %Y", "%b %d, %Y"):
                try:
                    day = datetime.strptime(value, fmt).date().isoformat()
                    return {"published_at": None, "published_date": day, "timestamp_precision": "date"}
                except ValueError:
                    pass
    if parsed is None:
        return {"published_at": None, "published_date": None, "timestamp_precision": "unknown"}
    original_date = parsed.date().isoformat()
    if parsed.tzinfo is None:
        if default_zone:
            parsed = parsed.replace(tzinfo=ZoneInfo(default_zone))
        else:
            return {"published_at": None, "published_date": original_date, "timestamp_precision": "date"}
    return {"published_at": parsed.astimezone(UTC).isoformat(), "published_date": original_date,
            "timestamp_precision": "exact"}


def relevant(title: str, summary: str = "") -> bool:
    text = title + " " + summary
    if re.search(r"greenhouse gas|exhaust gas", text, re.I) and not re.search(r"natural gas|\bLNG\b|\bTTF\b|methane", text, re.I):
        return False
    return bool(GAS_PATTERN.search(text))


def classify(title: str, summary: str = "") -> tuple[str, list[str]]:
    text = title + " " + summary
    category = "other"
    for cat, pattern in [("supply_outage", r"outage|shut.?in|shutdown|interrupt|maintenance|strike|leak"),
                         ("storage", r"storage|inventor"), ("weather", r"weather|hurricane|cold|heatwave|winter"),
                         ("policy", r"regulat|sanction|commission|methane|legislat"),
                         ("lng", r"\bLNG\b|liquefied|regasification|FSRU"),
                         ("supply", r"production|suppl|gas discovery|pipeline|export"),
                         ("demand", r"demand|consumption|power|heating")]:
        if re.search(pattern, text, re.I):
            category = cat
            break
    names = ["Gassco", "Equinor", "Cheniere", "Freeport", "QatarEnergy", "Gazprom", "Shell",
             "Troll", "Gullfaks", "Hammerfest", "Sabine Pass", "Corpus Christi", "Norway", "Russia", "Ukraine", "Europe", "China"]
    return category, [n for n in names if re.search(r"\b" + re.escape(n) + r"\b", text, re.I)]


def news_item(source: str, url: str, title: str, summary: str, timestamp: str | None,
              *, historical: bool, timestamp_source: str, default_zone: str | None = None,
              updated_at: str | None = None, discovered_via: str | None = None) -> dict:
    title, summary = clean_text(title, 500), clean_text(summary)
    category, entities = classify(title, summary)
    stamp = parse_timestamp(timestamp, default_zone=default_zone)
    content_hash = hashlib.sha256((title + "\n" + summary + "\n" + json.dumps(stamp, sort_keys=True)).encode()).hexdigest()
    return {"source": source, "url": canonical_url(url), "title": title, "summary": summary,
            **stamp, "timestamp_source": timestamp_source,
            "updated_at": parse_timestamp(updated_at, default_zone=default_zone)["published_at"],
            "first_seen_at": now_iso(), "summary_created_at": now_iso(), "category": category,
            "entities": entities, "source_revision": content_hash[:16], "content_hash": content_hash,
            "discovered_via": discovered_via or url, "ingestion_kind": "backfill" if historical else "live",
            "is_relevant": relevant(title, summary),"relevance_metadata":relevance_metadata(title,summary)}


def parse_feed(content: str, source: dict, *, historical: bool = False) -> list[dict]:
    parsed = feedparser.parse(content)
    if not parsed.entries:
        raise ValueError("RSS/Atom response contains no entries")
    items = []
    for entry in parsed.entries:
        raw_entry = dict(entry)  # Avoid feedparser's synthetic updated->published fallback.
        item = news_item(source["id"], entry.get("link", ""), entry.get("title", ""),
                         entry.get("summary", entry.get("description", "")), entry.get("published"),
                         historical=historical, timestamp_source="rss.pubDate",
                         default_zone=source.get("timezone"), updated_at=raw_entry.get("updated"),
                         discovered_via=source.get("feed_url"))
        if source["id"] == "eu_energy" and item["published_date"]:
            item.update(published_at=None, timestamp_precision="date", timestamp_source="RSS publication date (CMS clock unverified)")
        premium=(any(str(raw_entry.get(key,"")).lower() in {"true","1","yes"} for key in ("ispremium","is_premium","premium")) or
                 str(raw_entry.get("isaccessibleforfree","")).lower() in {"false","0","no"} or
                 any(str(tag.get("term","")).lower() in {"premium","subscriber-only","paid"} for tag in raw_entry.get("tags",[]) if isinstance(tag,dict)))
        if premium:
            item.update(is_premium=True,fact_excerpt_status="premium_metadata_only")
        else:
            content_values=[block.get("value","") for block in raw_entry.get("content",[]) if isinstance(block,dict)]
            if content_values:
                paragraphs=[]
                for value in content_values:
                    document=BeautifulSoup(value,"html.parser")
                    blocks=document.find_all("p")
                    if blocks:
                        paragraphs.extend(block.get_text(" ",strip=True) for block in blocks)
                    else:
                        paragraphs.append(document.get_text(" ",strip=True))
                attach_fact_excerpt(item,paragraphs,method="public_rss_content")
        if item["url"] and item["title"] and item["is_relevant"]:
            items.append(item)
    return items


@dataclass
class Fetcher:
    client: httpx.AsyncClient
    source: dict
    state: dict
    store: Store
    historical: bool
    failures: list[dict]
    request_count: int = 0
    last_request: float = 0.0
    created_items: list[dict] = field(default_factory=list)
    revised_items: list[dict] = field(default_factory=list)
    request_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    live_incremental: bool = False
    completed_api_at: str | None = None

    async def get(self, url: str, **kwargs) -> httpx.Response:
        # At most one request per second to each source, including detail fetches.
        async with self.request_lock:
            interval = max(1.05, float(self.source.get("request_interval_seconds", 1.05)))
            await asyncio.sleep(max(0.0, interval - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
        self.request_count += 1
        try:
            response = await self.client.get(url, **kwargs)
            if response.status_code == 429:
                retry = response.headers.get("Retry-After", "")
                seconds = max(900, int(retry)) if retry.isdigit() else 900
                self.checkpoint(next_poll_at=(datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(),
                                rate_limited_at=now_iso(), rate_limit_backoff_seconds=seconds)
            response.raise_for_status()
            if "Unknown domain" in response.text[:300]:
                raise ValueError("Source host is not configured by its publisher")
            return response
        except Exception as exc:
            self.failures.append({"url": url, "at": now_iso(), "error": (str(exc) or type(exc).__name__)[:500]})
            raise

    def checkpoint(self, **fields: Any) -> None:
        self.state.update(fields)
        self.state["last_attempt_at"] = now_iso()
        self.store.set_source_state(self.source["id"], self.state)

    def save(self, item: dict) -> dict:
        saved = self.store.upsert_news(item)
        if saved.get("_created"):
            self.created_items.append(saved)
        elif saved.get("_revised"):
            self.revised_items.append(saved)
        return saved


def _article_paragraphs(soup: BeautifulSoup) -> list[str]:
    """Read publisher body containers, excluding structural presentation UI."""
    selectors = ('[itemprop="articleBody"]', '.article-content', '.entry-content',
                 '.article-body', '.payload-richtext', '.nwd_html', '.wysiwyg')
    roots = next((nodes for selector in selectors if (nodes := soup.select(selector))), None)
    if roots is None:
        roots = soup.select('article, .article, .main-content, #article, .tie-article, main')
    paragraphs, seen = [], set()
    for root in roots:
        for paragraph in root.find_all('p'):
            if id(paragraph) in seen or paragraph.find('p'):
                continue
            seen.add(id(paragraph))
            ancestors = []
            for ancestor in paragraph.parents:
                ancestors.append(ancestor)
                if ancestor is root:
                    break
            if any(node.name in {'header', 'footer', 'nav', 'aside', 'figcaption'} or
                   node.get('itemprop') in {'author', 'datePublished', 'dateModified', 'caption'} or
                   node.get('rel') == ['author'] for node in ancestors):
                continue
            paragraphs.append(paragraph.get_text(' ', strip=True))
    return paragraphs


def _publisher_api_record(item: dict) -> bool:
    return (item.get('source') == 'offshore_energy' and
            item.get('timestamp_source') == 'publisher API publishedAt')


def _preserve_publisher_authority(item: dict, previous: dict | None) -> dict:
    """A lower-fidelity transport cannot retract an already observed API body."""
    if not previous or not _publisher_api_record(previous) or item.get('url') != previous.get('url'):
        return item
    if not _publisher_api_record(item):
        return {key: copy.deepcopy(value) for key, value in previous.items() if not key.startswith('_')}
    item = copy.deepcopy(item)
    for field in ('updated_at', 'published_at', 'published_date'):
        if not item.get(field) and previous.get(field):
            item[field] = previous[field]
    if not item.get('summary'):
        item['summary'] = previous.get('summary', '')
    if not item.get('fact_excerpt') and not item.get('is_premium'):
        for field in ('fact_excerpt', 'fact_excerpt_created_at', 'fact_excerpt_status', 'fact_excerpt_provenance'):
            if field in previous:
                item[field] = copy.deepcopy(previous[field])
    item['category'], item['entities'] = classify(item['title'], item['summary']+' '+item.get('fact_excerpt', ''))
    item['relevance_metadata'] = relevance_metadata(item['title'], item['summary'], item.get('fact_excerpt', ''))
    _refresh_fact_identity(item)
    return item


def extract_article(content: str, url: str, source: dict, *, historical: bool,
                    fallback_title: str = "", fallback_date: str | None = None) -> dict:
    soup = BeautifulSoup(content, "html.parser")
    def meta(*names):
        for name in names:
            tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
            if tag and tag.get("content"):
                return tag["content"]
        return None
    title = meta("og:title") or (soup.h1.get_text(" ", strip=True) if soup.h1 else fallback_title)
    summary = meta("description", "og:description") or ""
    timestamp = meta("article:published_time", "datePublished", "date")
    updated = meta("article:modified_time", "dateModified")
    premium=(str(meta("isPremium") or "").lower() in {"true","1","yes"} or
             str(meta("isAccessibleForFree") or "").lower() in {"false","0","no"})
    premium=premium or bool(soup.select_one('.paywall, .subscription-wall, [data-paywall="true"]'))
    timestamp_source = "article.metadata" if timestamp else "archive.date"
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            blocks = json.loads(script.string or script.get_text())
            blocks = blocks if isinstance(blocks, list) else [blocks]
            for block in blocks:
                if not isinstance(block,dict):
                    continue
                if "@graph" in block:
                    blocks.extend(block["@graph"])
                if str(block.get("isAccessibleForFree","")).lower() in {"false","0","no"} or str(block.get("isPremium","")).lower() in {"true","1","yes"}:
                    premium=True
                if block.get("datePublished"):
                    timestamp = block["datePublished"]
                    timestamp_source = "jsonld.datePublished"
                    updated = block.get("dateModified", updated)
                    if not summary:
                        summary = block.get("description", "")
        except (ValueError, TypeError, AttributeError):
            continue
    paragraphs = [] if premium else _article_paragraphs(soup)
    if not summary and not premium:
        summary = next((paragraph for paragraph in paragraphs if len(paragraph) > 80
                        and not paragraph.startswith(("Data source:", "Source:", "Note:"))), "")
    item=news_item(source["id"], url, title or fallback_title, summary,
                     timestamp or fallback_date, historical=historical,
                     timestamp_source=timestamp_source, default_zone=source.get("timezone"),
                     updated_at=updated, discovered_via=source.get("archive_url"))
    return attach_fact_excerpt(item,paragraphs,method="public_article_paragraphs",is_premium=premium)


async def ingest_eia(fetch: Fetcher, start: date) -> list[dict]:
    if not fetch.historical:
        return parse_feed((await fetch.get(fetch.source["feed_url"])).text, fetch.source)
    items = []
    completed = set(fetch.state.get("completed_years", []))
    for year in range(start.year, datetime.now(UTC).year + 1):
        if str(year) in completed and year != datetime.now(UTC).year:
            continue
        url = fetch.source["archive_url"] + "?my=" + str(year)
        soup = BeautifulSoup((await fetch.get(url)).text, "html.parser")
        candidates = []
        for row in soup.select(".accordion-content li"):
            anchor = row.select_one('a[href*="detail.php?id="]')
            date_el = row.select_one(".date")
            if not anchor or not date_el:
                continue
            stamp = parse_timestamp(date_el.get_text(strip=True))
            day = stamp["published_date"]
            title = anchor.get_text(" ", strip=True)
            if not day or day < start.isoformat() or not relevant(title):
                continue
            article_url = urljoin(url, anchor["href"])
            candidates.append((article_url, title, day))
        semaphore = asyncio.Semaphore(4)
        async def collect(candidate):
            article_url, title, day = candidate
            async with semaphore:
                try:
                    article = extract_article((await fetch.get(article_url)).text, article_url, fetch.source,
                                              historical=True, fallback_title=title, fallback_date=day)
                except Exception:
                    # Preserve verified archive metadata if detail page failed.
                    article = news_item(fetch.source["id"], article_url, title, "", day,
                                        historical=True, timestamp_source="archive.date", discovered_via=url)
                items.append(article)
                fetch.save(article)
        await asyncio.gather(*(collect(candidate) for candidate in candidates))
        completed.add(str(year))
        fetch.checkpoint(completed_years=sorted(completed), archive_cursor=str(year))
    return items


async def ingest_eu(fetch: Fetcher, start: date) -> list[dict]:
    if not fetch.historical:
        return parse_feed((await fetch.get(fetch.source["feed_url"])).text, fetch.source)
    items = []
    cursor = int(fetch.state.get("archive_cursor", 0)) if not fetch.state.get("backfill_completed_at") else 0
    for page in range(cursor, 180):
        url = fetch.source["archive_url"] + "?page=" + str(page)
        soup = BeautifulSoup((await fetch.get(url)).text, "html.parser")
        rows = soup.select("article.ecl-content-item")
        if not rows:
            raise ValueError("EU archive layout has no news cards")
        dates = []
        for row in rows:
            anchor = row.select_one(".ecl-content-block__title a")
            clock = row.select_one("time")
            excerpt = row.select_one(".ecl-content-block__description")
            if not anchor or not clock:
                continue
            # Drupal listing uses synthetic 12:00 for date-only publications.
            # Its visible value is a publication date, so preserve date precision.
            day = (clock.get("datetime") or "")[:10]
            dates.append(day)
            if day < start.isoformat():
                continue
            item = news_item(fetch.source["id"], urljoin(url, anchor["href"]), anchor.get_text(" ", strip=True),
                             excerpt.get_text(" ", strip=True) if excerpt else "", day,
                             historical=True, timestamp_source="archive.visible_publication_date", discovered_via=url)
            if item["is_relevant"]:
                items.append(item)
                fetch.save(item)
        fetch.checkpoint(archive_cursor=page + 1)
        if dates and min(dates) < start.isoformat():
            break
    return items


async def ingest_equinor(fetch: Fetcher, start: date) -> list[dict]:
    urls: list[tuple[str, str]] = []
    if fetch.historical:
        response = await fetch.get(fetch.source["archive_url"])
        tree = ET.fromstring(response.content)
        for node in tree.iter():
            if not node.tag.endswith("loc") or not node.text:
                continue
            matched = re.search(r"/news/(\d{8})[-/]", node.text)
            if matched:
                try:
                    day = datetime.strptime(matched[1], "%Y%m%d").date().isoformat()
                except ValueError:
                    continue  # Publisher slug is not a valid calendar date.
                if day >= start.isoformat():
                    urls.append((node.text, day))
        urls.sort(key=lambda x: x[1])
    else:
        response = await fetch.get(fetch.source["live_url"])
        soup = BeautifulSoup(response.text, "html.parser")
        for anchor in soup.select('a[href*="/news/"]'):
            url = urljoin(fetch.source["live_url"], anchor["href"])
            matched = re.search(r"/news/(\d{8})-", url)
            if matched:
                urls.append((url, datetime.strptime(matched[1], "%Y%m%d").date().isoformat()))
    done = set(fetch.state.get("completed_urls", []))
    items = []
    for url, day in dict.fromkeys(urls):
        if fetch.historical and url in done:
            continue
        # Relevant keyword slugs let us avoid irrelevant investor/renewables pages.
        if not relevant(url.replace("-", " ")):
            continue
        try:
            item = extract_article((await fetch.get(url)).text, url, fetch.source,
                                   historical=fetch.historical, fallback_date=day)
            if item["is_relevant"]:
                items.append(item)
                if fetch.historical:
                    fetch.save(item)
            done.add(url)
            fetch.checkpoint(completed_urls=sorted(done), archive_cursor=day)
        except Exception:
            continue
    return items


async def ingest_feed_source(fetch: Fetcher, start: date) -> list[dict]:
    response = await fetch.get(fetch.source["feed_url"])
    items = parse_feed(response.text, fetch.source, historical=fetch.historical)
    return [i for i in items if not fetch.historical or not i["published_date"] or i["published_date"] >= start.isoformat()]


def _feed_fingerprint(item: dict) -> str:
    values={key:item.get(key) for key in ("title","summary","published_at","published_date","updated_at","timestamp_precision")}
    return hashlib.sha256(json.dumps(values,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def _publisher_article_url(url: str, source: dict) -> bool:
    host=(urlsplit(url).hostname or "").lower().removeprefix("www.")
    configured={(urlsplit(str(source.get(key,""))).hostname or "").lower().removeprefix("www.") for key in ("feed_url","archive_url","live_url","api_url")}
    return urlsplit(url).scheme in {"https","http"} and bool(host) and host in configured


async def enrich_live_facts(fetch: Fetcher, items: list[dict]) -> list[dict]:
    """Enrich a bounded recent RSS delta and carry known excerpts forward.

    All detail requests share Fetcher's per-source rate limiter. Cache entries
    hold fingerprints/timers only; source text lives in immutable news versions.
    A failed detail request never discards the feed headline or invents facts.
    """
    if fetch.historical or fetch.source["id"] not in {"offshore_energy","eia","eu_energy","equinor","cheniere"} or not items:
        return items
    urls=[item["url"] for item in items if item.get("url")]
    with fetch.store.connection() as conn:
        placeholders=",".join("?" for _ in urls)
        previous={row["url"]:json.loads(row["data"]) for row in conn.execute(f"SELECT url,data FROM news WHERE url IN ({placeholders})",urls)} if urls else {}
    cache=dict(fetch.state.get("fact_excerpt_cache") or {})
    limit=max(0,min(8,int(fetch.source.get("fact_excerpt_max_articles_per_poll",4))))
    attempts=0
    result=list(items)
    now=datetime.now(UTC)
    order=sorted(range(len(items)),key=lambda i:str(items[i].get("published_at") or items[i].get("published_date") or ""),reverse=True)
    for index in order:
        item=copy.deepcopy(items[index]);url=item.get("url","");old=previous.get(url)
        item = _preserve_publisher_authority(item, old)
        result[index] = item
        # Payload API is authoritative for this publisher. Do not alternate its
        # body/precise clocks with an RSS description or a different HTML view.
        if _publisher_api_record(item):
            continue
        fingerprint=_feed_fingerprint(item)
        key=hashlib.sha256(url.encode()).hexdigest()[:24]
        entry=cache.get(key,{})
        unchanged=old is not None and _same_publisher_metadata(item,old)
        cache_valid=entry.get("feed_digest")==fingerprint or bool(unchanged and old.get("fact_excerpt"))
        carried=copy.deepcopy(old) if unchanged else item
        if unchanged:
            for field in ("updated_at","published_at","published_date"):
                if not item.get(field) and old.get(field):
                    item[field]=old[field]
        if item.get("fact_excerpt") or item.get("is_premium"):
            if unchanged and item.get("fact_excerpt")==old.get("fact_excerpt") and bool(item.get("is_premium"))==bool(old.get("is_premium")):
                result[index]=carried
            else:
                item["fact_excerpt_feed_digest"]=fingerprint
                result[index]=item
            cache[key]={"feed_digest":fingerprint,"checked_at":now_iso(),"status":"premium" if item.get("is_premium") else "enriched",
                        "next_check_at":None if item.get("is_premium") else (now+timedelta(hours=6)).isoformat()}
            continue
        if unchanged and old.get("fact_excerpt"):
            result[index]=carried
        if unchanged and (old.get("is_premium") or cache_valid and entry.get("status")=="premium"):
            result[index]=carried
            continue
        next_check=entry.get("next_check_at")
        if unchanged and cache_valid and next_check:
            try:
                if datetime.fromisoformat(next_check.replace("Z","+00:00"))>now:
                    continue
            except (ValueError,TypeError):
                pass
        publication=item.get("published_at") or item.get("published_date")
        recent=True
        if publication:
            try:
                recent=date.fromisoformat(str(publication)[:10])>=(now-timedelta(days=3)).date()
            except ValueError:
                pass
        if attempts>=limit or not recent or not _publisher_article_url(url,fetch.source):
            continue
        attempts+=1
        status="no_public_facts"
        try:
            response=await fetch.get(url,timeout=httpx.Timeout(12,connect=6))
            article=extract_article(response.text,url,fetch.source,historical=False,
                                    fallback_title=item["title"],fallback_date=item.get("published_date"))
            if article.get("is_premium"):
                item.update(is_premium=True,fact_excerpt_status="premium_metadata_only")
                status="premium"
            elif article.get("fact_excerpt"):
                for field in ("fact_excerpt","fact_excerpt_created_at","fact_excerpt_status","fact_excerpt_provenance"):
                    item[field]=copy.deepcopy(article[field])
                item["fact_excerpt_provenance"]["publisher_updated_at"]=article.get("updated_at")
                item["category"],item["entities"]=classify(item["title"],item["summary"]+" "+item["fact_excerpt"])
                item["relevance_metadata"]=relevance_metadata(item["title"],item["summary"],item["fact_excerpt"])
                _refresh_fact_identity(item)
                status="enriched"
            item["fact_excerpt_feed_digest"]=fingerprint
            # Re-reading identical source text changes neither version nor its
            # original discovery time, even when the HTML observation is later.
            result[index]=carried if unchanged and old.get("fact_excerpt")==item.get("fact_excerpt") and bool(old.get("is_premium"))==bool(item.get("is_premium")) else item
        except Exception as exc:
            status="failed"
            fetch.state["last_fact_excerpt_error"]=(str(exc) or type(exc).__name__)[:400]
            result[index]=carried if unchanged else item
        cache[key]={"feed_digest":fingerprint,"checked_at":now_iso(),"status":status,
                    "next_check_at":None if status=="premium" else (now+timedelta(hours=6 if status=="enriched" else 1)).isoformat()}
        if fetch.state.get("next_poll_at"):
            break
    fetch.state["fact_excerpt_cache"]={key:value for key,value in sorted(cache.items(),key=lambda p:p[1].get("checked_at",""),reverse=True)[:64]}
    fetch.state["last_fact_excerpt_requests"]=attempts
    return result


def lexical_text(node: Any) -> str:
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        return " ".join(lexical_text(v) for k, v in node.items() if k in {"root", "children", "content"})
    if isinstance(node, list):
        return " ".join(lexical_text(v) for v in node)
    return ""


def lexical_paragraphs(node: Any) -> list[str]:
    if isinstance(node,dict):
        if node.get("type")=="paragraph":
            return [lexical_text(node)]
        return [paragraph for key,value in node.items() if key in {"root","children","content"}
                for paragraph in lexical_paragraphs(value)]
    if isinstance(node,list):
        return [paragraph for value in node for paragraph in lexical_paragraphs(value)]
    return []


async def ingest_offshore(fetch: Fetcher, start: date) -> list[dict]:
    if not fetch.historical:
        now = datetime.now(UTC)
        # Source last_success_at also advances after partial RSS discovery.
        # Only a fully fetched AND persisted API slice may advance this cursor.
        def known_clock(value):
            try:
                clock = datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
                return clock if clock and clock.tzinfo and clock <= now else None
            except (ValueError, TypeError):
                return None
        complete = known_clock(fetch.state.get("last_complete_api_success_at"))
        if complete:
            begin = (complete-timedelta(days=1)).date()
        elif fetch.state.get("api_recovery_from"):
            begin = date.fromisoformat(fetch.state["api_recovery_from"])
        else:
            # One-time migration can reuse a recorded successful legacy API
            # recovery. General RSS success is only a conservative lower bound.
            legacy_api = known_clock(fetch.state.get("last_gap_recovery_at")) or known_clock(fetch.state.get("last_api_fallback_at"))
            legacy_success = known_clock(fetch.state.get("last_success_at"))
            begin = (legacy_api-timedelta(days=1)).date() if legacy_api else min((now-timedelta(days=2)).date(), (legacy_success-timedelta(days=1)).date()) if legacy_success else (now-timedelta(days=2)).date()
        fetch.state["api_recovery_from"] = begin.isoformat()
        saved_state = {key: fetch.state.get(key) for key in ("archive_cursor", "historical_pages", "archive_candidate_count", "historical_limited")}
        old_incremental = fetch.live_incremental
        primary = []
        api_succeeded = False
        try:
            fetch.historical, fetch.live_incremental = True, True
            fetch.state["archive_cursor"] = 1
            primary = await ingest_offshore(fetch, begin)
            api_succeeded = True
            fetch.completed_api_at = now_iso()
            fetch.state.update(live_transport="api_primary", last_gap_recovery_at=now_iso(),
                               gap_recovered_from=begin.isoformat(), gap_recovered_count=len(primary))
        except Exception as exc:
            fetch.state.update(live_transport="rss_fallback", last_api_error=(str(exc) or type(exc).__name__)[:800])
            # Fetcher preserves HTTP failures; JSON/parse failures also remain
            # visible even if RSS can still discover genuinely new URLs.
            if not fetch.failures or fetch.failures[-1].get("url") != fetch.source.get("api_url"):
                fetch.failures.append({"url": fetch.source.get("api_url"), "at": now_iso(),
                                       "error": fetch.state["last_api_error"], "operation": "publisher_api"})
        finally:
            fetch.historical, fetch.live_incremental = False, old_incremental
            fetch.state.update(saved_state)
        # RSS remains a discovery channel, never a same-article body authority.
        failures_before_rss = len(fetch.failures)
        try:
            discovered = await ingest_feed_source(fetch, start)
        except Exception as exc:
            fetch.state["last_rss_error"] = (str(exc) or type(exc).__name__)[:800]
            if len(fetch.failures) == failures_before_rss:
                fetch.failures.append({"url": fetch.source["feed_url"], "at": now_iso(),
                                       "error": fetch.state["last_rss_error"], "operation": "rss_parse"})
            if not api_succeeded:
                raise
            discovered = []
        merged = {item["url"]: item for item in discovered}
        merged.update({item["url"]: item for item in primary})
        return list(merged.values())
    # The public Payload articles endpoint and its date/OR-title filtering were
    # verified with a two-item request: 2,718 candidates since 2024-05-15.
    # Collect only this bounded natural-gas/LNG slice, never all 417k articles.
    params = {"limit": 100, "depth": 0, "sort": "publishedAt",
              "where[and][0][publishedAt][greater_than_equal]": start.isoformat() + "T00:00:00.000Z",
              "where[and][1][or][0][title][like]": "gas",
              "where[and][1][or][1][title][like]": "LNG",
              "where[and][1][or][2][title][like]": "FSRU"}
    if fetch.live_incremental:
        # Publisher supports this bounded OR filter (verified limit-2 probe).
        boundary = params.pop("where[and][0][publishedAt][greater_than_equal]")
        params["where[and][0][or][0][publishedAt][greater_than_equal]"] = boundary
        params["where[and][0][or][1][updatedAt][greater_than_equal]"] = boundary
    for field in ["title", "slug", "content", "meta", "publishedAt", "updatedAt", "isPremium", "_status"]:
        params[f"select[{field}]"] = "true"
    page = int(fetch.state.get("archive_cursor", 1)) if not fetch.state.get("backfill_completed_at") else 1
    items = []
    while page <= 500:
        params["page"] = page
        response = await fetch.get(fetch.source["api_url"], params=params)
        payload = response.json()
        docs = payload.get("docs")
        if not isinstance(docs, list):
            raise ValueError("Offshore public article endpoint did not return docs")
        for article in docs:
            if article.get("_status") != "published":
                continue
            published = article.get("publishedAt")
            stamp = parse_timestamp(published)
            updated_stamp = parse_timestamp(article.get("updatedAt"))
            recent_publication = stamp["published_date"] and stamp["published_date"] >= start.isoformat()
            recent_update = (fetch.live_incremental and updated_stamp["published_at"] and
                             updated_stamp["published_date"] >= start.isoformat())
            if not stamp["published_date"] or not (recent_publication or recent_update):
                raise ValueError("Publisher publication/update date filter is not respected; refusing unbounded import")
            day = stamp["published_date"]
            article_url = "https://offshore-energy.biz/" + day.replace("-", "/") + "/" + article["slug"]
            summary = (article.get("meta") or {}).get("description") or ""
            if not summary and not article.get("isPremium"):
                # Retain a limited paragraph excerpt, never the returned body.
                blocks = article.get("content") or []
                for block in blocks:
                    children = ((block.get("content") or {}).get("root") or {}).get("children") or []
                    first = next((x for x in children if x.get("type") == "paragraph"), None)
                    if first:
                        summary = lexical_text(first)
                        if summary:
                            break
            item = news_item("offshore_energy", article_url, article.get("title", ""), summary, published,
                             historical=not fetch.live_incremental, timestamp_source="publisher API publishedAt",
                             updated_at=article.get("updatedAt"), discovered_via=str(response.url))
            item["publisher_article_id"] = article.get("id")
            item["is_premium"] = bool(article.get("isPremium"))
            attach_fact_excerpt(item,[] if item["is_premium"] else lexical_paragraphs(article.get("content") or []),
                                method="public_publisher_payload_paragraphs",is_premium=item["is_premium"])
            if item["is_relevant"]:
                items.append(item)
                if not fetch.live_incremental:
                    fetch.save(item)
        fetch.checkpoint(archive_cursor=page + 1, historical_pages=page,
                         archive_candidate_count=payload.get("totalDocs"), historical_limited=False)
        if not payload.get("hasNextPage"):
            break
        if fetch.live_incremental and page >= 500:
            raise ValueError("Publisher API slice incomplete at bounded page limit; cursor not advanced")
        page = int(payload.get("nextPage") or page + 1)
    return items


async def ingest_cheniere(fetch: Fetcher, start: date) -> list[dict]:
    if not fetch.historical:
        return await ingest_feed_source(fetch, start)
    items = []
    cursor = int(fetch.state.get("archive_cursor", 1)) if not fetch.state.get("backfill_completed_at") else 1
    for page in range(cursor, 40):
        url = fetch.source["archive_url"] + "?page=" + str(page)
        soup = BeautifulSoup((await fetch.get(url)).text, "html.parser")
        rows = soup.select("article.media")
        if not rows:
            raise ValueError("Cheniere archive contains no press release cards")
        days = []
        for row in rows:
            link = row.select_one('a[href*="/detail/"]')
            clock = row.select_one("time[datetime]")
            if not link or not clock:
                continue
            stamp = clock["datetime"]
            day = stamp[:10]
            days.append(day)
            title = link.get_text(" ", strip=True)
            if day < start.isoformat():
                continue
            # Omit administrative press releases unrelated to supply/markets.
            if re.search(r"quarterly dividend|board of directors|timing of.*earnings|appoints.*board", title, re.I):
                continue
            article_url = urljoin(url, link["href"])
            try:
                item = extract_article((await fetch.get(article_url)).text, article_url, fetch.source,
                                       historical=True, fallback_title=title, fallback_date=stamp)
                if item["is_relevant"] or relevant(title):
                    items.append(item)
                    fetch.save(item)
            except Exception:
                item = news_item("cheniere", article_url, title, "", stamp, historical=True,
                                 timestamp_source="archive.time (publisher Eastern Time)", default_zone="America/New_York")
                if item["is_relevant"]:
                    items.append(item)
                    fetch.save(item)
        fetch.checkpoint(archive_cursor=page + 1, historical_pages=page)
        if days and min(days) < start.isoformat():
            break
    return items


async def ingest_gassco(fetch: Fetcher, start: date) -> list[dict]:
    # Public site publishes the acceptance URL in a GET form; cookies remain in
    # this source's isolated client. No guessed private API or authentication.
    response = await fetch.get(fetch.source["live_url"])
    soup = BeautifulSoup(response.text, "html.parser")
    form = soup.select_one('form[action*="acceptDisclaimer"]')
    if form:
        await fetch.get(urljoin(str(response.url), form["action"]))
        response = await fetch.get(fetch.source["live_url"])
        soup = BeautifulSoup(response.text, "html.parser")
    items = []
    def parse_rows(content: str, page_url: str):
        document = BeautifulSoup(content, "html.parser")
        tables = document.select("table.msgTable")
        if not tables:
            raise ValueError("Gassco page has no verified UMM table")
        parsed = []
        # Archive rows have no id attribute; live rows do. The verified details
        # link is the record identity in both layouts.
        for row in document.select("table.msgTable tr"):
            cells = row.find_all("td", recursive=False)
            link = row.select_one('a[href*="/details?id="]')
            if len(cells) < 14 or not link:
                continue
            values = [cell.get_text(" ", strip=True) for cell in cells]
            title = f"Gassco {values[1]}: {values[3]} {values[4]} ({values[2]})"
            text = (f"Message ID: {values[0]}. Publication: {values[5]}. Event: {values[6]} to {values[7]}. "
                    f"Technical capacity: {values[9]} {values[8]}; available: {values[10]}; unavailable: {values[11]}. "
                    f"Reason: {values[12]}. Remarks: {values[13]}.")
            item = news_item("gassco", urljoin(page_url, link["href"]), title, text, values[5],
                             historical=fetch.historical, timestamp_source="UMM.Publication date/time", discovered_via=page_url)
            item["category"] = "supply_outage"
            item["entities"] = ["Gassco", values[1]]
            item["is_relevant"] = True
            item["event_status"] = values[2]
            item["event_start"] = values[6]
            item["event_end"] = values[7]
            item["source_message_id"] = values[0]
            parsed.append(item)
        return parsed
    if not fetch.historical:
        return parse_rows(response.text, str(response.url))
    # /archive and publicationFromDate/ToDate are exposed by the official GET
    # form, using month-sized queries to avoid an unbounded archive download.
    archive_link = soup.select_one('a[href="/archive"]')
    if not archive_link:
        raise ValueError("Publisher archive link not present")
    archive_url = urljoin(str(response.url), archive_link["href"])
    begin = date.fromisoformat(fetch.state.get("archive_cursor", start.isoformat()))
    through = datetime.now(UTC).date() + timedelta(days=1)
    reset = begin < start or begin >= through or fetch.state.get("backfill_completed_at") or fetch.state.get("gassco_archive_parser_version") != 2
    if reset:
        begin = start
    fetch.state["gassco_archive_parser_version"] = 2
    empty_intervals = [] if reset else list(fetch.state.get("empty_archive_intervals", []))
    limited_reasons = [] if reset else list(fetch.state.get("historical_limited_reasons", []))
    while begin < through:
        end = min((begin.replace(day=28) + timedelta(days=4)).replace(day=1), through)
        number, seen_pages = 1, set()
        interval_count = 0
        total = 0
        while number <= 100:
            page = await fetch.get(archive_url, params={"publicationFromDate": begin.isoformat() + "T00:00",
                                                        "publicationToDate": end.isoformat() + "T00:00", "page.page": number,
                                                        "page.sort": "publ_start", "page.sort.dir": "asc"})
            fetch.state["historical_pages"] = fetch.state.get("historical_pages", 0) + 1
            batch = parse_rows(page.text, str(page.url))
            fingerprint = tuple(item["url"] for item in batch)
            if fingerprint and fingerprint in seen_pages:
                limited_reasons.append(f"Archive pagination repeated page for {begin.isoformat()} at page {number}")
                break
            seen_pages.add(fingerprint)
            for item in batch:
                day = item["published_date"]
                if not day or not (begin.isoformat() <= day < end.isoformat()):
                    limited_reasons.append(f"Publication filter mismatch for {begin.isoformat()}: {day}")
                    continue
                items.append(item)
                fetch.save(item)
                interval_count += 1
            total_match = re.search(r"Total records:\s*(\d+)", BeautifulSoup(page.text, "html.parser").get_text(" ", strip=True))
            if total_match:
                total = int(total_match[1])
                if number * 20 >= total:
                    break
            else:
                limited_reasons.append(f"Archive total/pagination could not be verified for {begin.isoformat()}")
                break
            if not batch:
                limited_reasons.append(f"Archive page {number} has no parsed rows while more records reported")
                break
            number += 1
        if interval_count < total:
            limited_reasons.append(f"Archive returned {interval_count} parsed records versus {total} reported for {begin.isoformat()}")
        if not interval_count:
            empty_intervals.append([begin.isoformat(), end.isoformat()])
        fetch.checkpoint(archive_cursor=end.isoformat(), historical_intervals=fetch.state.get("historical_intervals", 0) + 1,
                         empty_archive_intervals=empty_intervals, historical_limited_reasons=list(dict.fromkeys(limited_reasons)))
        begin = end
    # Active/future events can be omitted from past archive; keep their currently
    # published versions too, preserving publisher times rather than event dates.
    items.extend(parse_rows(response.text, str(response.url)))
    if empty_intervals:
        limited_reasons.append("No records observed in requested intervals; full historical archive retention is not confirmed")
    fetch.state.update(historical_limited=bool(limited_reasons), historical_limited_reasons=list(dict.fromkeys(limited_reasons)),
                       empty_archive_intervals=empty_intervals)
    return items


async def ingest_gdelt(fetch: Fetcher, start: date) -> list[dict]:
    cutoff = max(start, (datetime.now(UTC) - timedelta(days=90)).date())
    # Monthly slices keep discovery bounded; this is not the global GDELT archive.
    slices = []
    if fetch.historical:
        day = cutoff
        while day <= datetime.now(UTC).date():
            end = min(day + timedelta(days=7), datetime.now(UTC).date() + timedelta(days=1))
            slices.append((day, end))
            day = end
    else:
        slices = [(max(cutoff, (datetime.now(UTC) - timedelta(days=2)).date()), datetime.now(UTC).date() + timedelta(days=1))]
    items = []
    for begin, end in slices:
        params = {"query": '("natural gas" OR "LNG" OR "TTF") sourcelang:english', "mode": "ArtList",
                  "format": "json", "maxrecords": "250", "sort": "DateDesc",
                  "startdatetime": begin.strftime("%Y%m%d") + "000000",
                  "enddatetime": end.strftime("%Y%m%d") + "000000"}
        response = await fetch.get(fetch.source["live_url"], params=params)
        payload = response.json()
        articles = payload.get("articles", [])
        if len(articles) >= 250:
            fetch.state.setdefault("truncated_intervals", []).append([begin.isoformat(), end.isoformat()])
        for article in articles:
            # GDELT seendate is discovery time, NOT publisher publication time.
            item = news_item("gdelt", article.get("url", ""), article.get("title", ""), "", None,
                             historical=fetch.historical, timestamp_source="unknown (GDELT seendate is discovery only)",
                             discovered_via=str(response.url))
            item["gdelt_seen_at"] = article.get("seendate")
            if item["url"] and item["is_relevant"]:
                items.append(item)
        fetch.checkpoint(archive_cursor=end.isoformat(), historical_start=cutoff.isoformat())
    return items


def load_sources() -> list[dict]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["sources"]


async def poll_sources(store: Store, *, historical: bool = False,
                       start_date: str = "2024-05-15", source_ids: list[str] | None = None) -> dict:
    """Poll sources independently. Failed sources never look like no-news days."""
    start = date.fromisoformat(start_date)
    sources = [s for s in load_sources() if s.get("enabled", True) and (not source_ids or s["id"] in source_ids)]
    adapters = {"eia": ingest_eia, "eu_energy": ingest_eu, "equinor": ingest_equinor,
                "cheniere": ingest_cheniere, "offshore_energy": ingest_offshore,
                "gassco": ingest_gassco, "gdelt": ingest_gdelt}
    async def run(source: dict):
        state = dict(store.get_source_state(source["id"]) or {})
        next_poll = state.get("next_poll_at")
        if next_poll and not historical:
            try:
                blocked_until = datetime.fromisoformat(next_poll.replace("Z", "+00:00"))
                if blocked_until > datetime.now(UTC):
                    return {"source": source["id"], "status": "backoff", "seen": 0, "created": 0, "revised": 0,
                            "errors": [], "last_error": state.get("last_error"), "next_poll_at": next_poll,
                            "_created_items": [], "_revised_items": []}
            except (ValueError, TypeError):
                pass
        state.update({"source": source["id"], "name": source["name"], "last_attempt_at": now_iso(),
                      "status": "running", "historical_requested_start": start_date if historical else state.get("historical_requested_start")})
        if historical:
            state.update({"historical_status": "running", "historical_from": start_date,
                          "historical_through": datetime.now(UTC).date().isoformat()})
        store.set_source_state(source["id"], state)
        failures = []
        created, revised = [], []
        async with httpx.AsyncClient(timeout=httpx.Timeout(35, connect=15), follow_redirects=True,
                                     headers={"User-Agent": USER_AGENT}, trust_env=True) as client:
            fetch = Fetcher(client, source, state, store, historical, failures)
            items = []
            try:
                items = await adapters[source["id"]](fetch, start)
                if not historical:
                    items=await enrich_live_facts(fetch,items)
                for item in items:
                    saved = store.upsert_news(item)
                    if saved.get("_created"):
                        created.append(saved)
                    elif saved.get("_revised"):
                        revised.append(saved)
                created.extend(fetch.created_items)
                revised.extend(fetch.revised_items)
                if fetch.completed_api_at:
                    state.update(last_complete_api_success_at=fetch.completed_api_at, api_recovery_from=None)
                limited = historical and bool(state.get("historical_limited"))
                state.update({"status": "partial" if failures or limited else "ok", "last_success_at": now_iso(),
                              "last_count": len(items), "last_created": len(created), "last_revised": len(revised),
                              "last_error": failures[-1]["error"] if failures else None})
                if state.get("next_poll_at"):
                    try:
                        if datetime.fromisoformat(state["next_poll_at"].replace("Z","+00:00"))<=datetime.now(UTC):
                            state.pop("next_poll_at",None)
                    except (ValueError,TypeError):
                        state.pop("next_poll_at",None)
                if historical and not failures and not limited:
                    state["backfill_completed_at"] = now_iso()
                elif historical:
                    # Store merges checkpoints, so omission would retain an old
                    # successful marker from before a partial archive audit.
                    state["backfill_completed_at"] = None
            except Exception as exc:
                state.update({"status": "failed", "last_error": (str(exc) or type(exc).__name__)[:800], "last_count": len(items)})
                LOG.warning("Source %s failed: %s", source["id"], exc)
            state["request_count"] = state.get("request_count", 0) + fetch.request_count
            state["last_run_request_count"] = fetch.request_count
            if historical:
                state["historical_status"] = state["status"]
            state["failures"] = (state.get("failures", []) + failures)[-200:]
            state["last_finished_at"] = now_iso()
            store.set_source_state(source["id"], state)
        return {"source": source["id"], "status": state["status"], "seen": len(items),
                "created": len(created), "revised": len(revised), "errors": failures,
                "last_error": state.get("last_error"), "_created_items": created, "_revised_items": revised}
    results = await asyncio.gather(*(run(s) for s in sources))
    created = [item for r in results for item in r.pop("_created_items")]
    revised = [item for r in results for item in r.pop("_revised_items")]
    return {"sources": results, "created": created, "revised": revised, "total_created": len(created),
            "historical": historical, "finished_at": now_iso()}
