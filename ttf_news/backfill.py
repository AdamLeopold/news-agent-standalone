"""Resumable history collection and auditable coverage export.

Run: .venv/bin/python -m ttf_news.backfill --sources eia,eu_energy,equinor,cheniere
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

from .ingest import Fetcher, USER_AGENT, extract_article, load_sources, poll_sources
from .prices import refresh_prices
from .store import Store


def coverage_report(store: Store, destination: str | Path | None = None, *, start_date: str = "2024-05-15") -> dict:
    destination = Path(destination or Path(__file__).resolve().parents[1] / "reports")
    destination.mkdir(parents=True, exist_ok=True)
    with store.connection() as connection:
        rows = [json.loads(row["data"]) for row in connection.execute("SELECT data FROM news")]
    counts = Counter((r.get("source"), (r.get("published_date") or (r.get("published_at") or "")[:10] or "unknown")[:7],
                      r.get("timestamp_precision", "unknown")) for r in rows)
    month_rows = [{"source": key[0], "month": key[1], "timestamp_precision": key[2], "count": value}
                  for key, value in sorted(counts.items())]
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "requested_start": start_date,
              "requested_through": datetime.now(timezone.utc).date().isoformat(),
              "total_articles": len(rows), "source_states": [store.get_source_state(s["id"]) for s in load_sources()],
              "monthly_counts": month_rows,
              "summary_coverage": [{"source": source["id"],
                                    "records": sum(r.get("source") == source["id"] for r in rows),
                                    "with_source_summary": sum(r.get("source") == source["id"] and bool(r.get("summary")) for r in rows)}
                                   for source in load_sources()],
              "limitations": ["Coverage means accessible selected-source news, not all global natural-gas news.",
                               "Date-only and unknown publication times cannot be precisely assigned across the 17:15 settlement boundary.",
                               "A zero month is not proof of no news. Consult source status and archive cursors.",
                               "GDELT history is limited to 90 recent days and capped discovery intervals are flagged.",
                               "DBnomics ICE observations remain reference prices until official settlement field is verified."]}
    months = []
    cursor = date.fromisoformat(start_date).replace(day=1)
    through = datetime.now(timezone.utc).date().replace(day=1)
    while cursor <= through:
        months.append(cursor.isoformat()[:7])
        cursor = date(cursor.year + (cursor.month == 12), (cursor.month % 12) + 1, 1)
    report["unobserved_source_months"] = []
    report['observed_ranges'] = []
    for source in load_sources():
        source_rows = [r for r in rows if r.get("source") == source["id"]]
        observed=sorted(r.get('published_date') or (r.get('published_at') or '')[:10] for r in source_rows if r.get('published_date') or r.get('published_at'))
        report['observed_ranges'].append({'source':source['id'],'count':len(source_rows),'earliest':observed[0] if observed else None,'latest':observed[-1] if observed else None,
            'exact':sum(r.get('timestamp_precision')=='exact' for r in source_rows),'date_only':sum(r.get('timestamp_precision')=='date' for r in source_rows)})
        present = {(r.get("published_date") or (r.get("published_at") or "")[:10] or "unknown")[:7] for r in source_rows}
        missing = [month for month in months if month not in present]
        report["unobserved_source_months"].append({"source": source["id"], "months": missing,
                                                   "meaning": "No dated record observed; not proof of no news or full source coverage."})
        month_rows.extend({"source": source["id"], "month": month, "timestamp_precision": "none_observed", "count": 0} for month in missing)
    month_rows.sort(key=lambda row: (row["source"], row["month"], row["timestamp_precision"]))
    (destination / "coverage.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with (destination / "coverage_monthly.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source", "month", "timestamp_precision", "count"])
        writer.writeheader()
        writer.writerows(month_rows)
    markdown = ["# 新闻历史覆盖报告", "", f"生成时间：{report['generated_at']}",
                f"请求区间：{start_date} 至 {report['requested_through']}；已入库逐条新闻：{len(rows)}。", "",
                "| 来源 | 状态 | 最后成功 | 历史游标 | 错误 |", "|---|---|---|---|---|"]
    for state in report["source_states"]:
        error=str(state.get('last_error') or '').replace('|','/').replace('\n',' ')[:220]
        markdown.append(f"| {state.get('name', state.get('source', ''))} | {state.get('status', 'not_attempted')} | {state.get('last_success_at', '')} | {state.get('archive_cursor', '')} | {error} |")
    markdown += ['', '| 来源 | 记录数 | 实际观测首日 | 实际观测末日 | 精确时刻 | 仅日期 |', '|---|---:|---|---|---:|---:|']
    for item in report['observed_ranges']:
        markdown.append(f"| {item['source']} | {item['count']} | {item['earliest'] or '无记录'} | {item['latest'] or '无记录'} | {item['exact']} | {item['date_only']} |")
    gassco_range = next(item for item in report['observed_ranges'] if item['source']=='gassco')
    gassco_state = store.get_source_state('gassco')
    gdelt_state = store.get_source_state('gdelt')
    gaps = gassco_state.get('empty_archive_intervals', [])
    markdown += ['', f"Gassco 公开档案实际可观测首日：{gassco_range['earliest'] or '未观测到记录'}；历史覆盖状态：{gassco_state.get('historical_status', '未检查')}。"]
    if gaps:
        markdown.append('未观测到记录的请求区间：'+ '；'.join(f'{start} 至 {end}（不含终点）' for start,end in gaps)+'。这些区间不视为已确认无新闻。')
    if gdelt_state.get('last_error'):
        markdown.append('GDELT 当前抓取错误：'+str(gdelt_state['last_error']).replace('\n',' ')[:220]+'；其他来源的成功与此错误分别记录。')
    markdown.append('抓取时间以UTC保存；观测首末日不证明范围内所有新闻均已收齐。')
    markdown += ["", "逐源逐月数量与时间精度见 coverage_monthly.csv；请求失败与截断区间见 coverage.json。", "",
                 "日期精度不足的记录保留真实来源日期，并单独标记，未合成新闻发布时间或交易日。"]
    (destination / "coverage.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
    return report


async def repair_missing_summaries(store: Store, *, source_ids: list[str] | None = None) -> dict:
    """Retry public-page excerpts for records whose initial detail fetch failed."""
    selected = [source for source in load_sources() if source["id"] in (source_ids or ["eia", "equinor", "cheniere"])
                and source["id"] in {"eia", "equinor", "cheniere"}]
    reports = []
    for source in selected:
        with store.connection() as connection:
            rows = [json.loads(row["data"]) for row in connection.execute("SELECT data FROM news WHERE source=?", (source["id"],))]
        missing = [row for row in rows if not row.get("summary")]
        errors, repaired = [], 0
        state_id = source["id"] + "_summary_repair"
        state = {"source": state_id, "status": "running", "attempted": len(missing), "started_at": datetime.now(timezone.utc).isoformat()}
        store.set_source_state(state_id, state)
        async with httpx.AsyncClient(timeout=35, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
            fetcher = Fetcher(client, source, {}, store, True, errors)
            semaphore = asyncio.Semaphore(4)
            async def repair(row):
                nonlocal repaired
                async with semaphore:
                    try:
                        item = extract_article((await fetcher.get(row["url"])).text, row["url"], source, historical=True,
                                               fallback_title=row["title"], fallback_date=row.get("published_date"))
                        if item.get("summary"):
                            if row.get("published_at") and not item.get("published_at"):
                                item.update(published_at=row["published_at"], timestamp_precision=row.get("timestamp_precision", "exact"),
                                            timestamp_source=row.get("timestamp_source"))
                            store.upsert_news(item)
                            repaired += 1
                    except Exception:
                        pass
            await asyncio.gather(*(repair(row) for row in missing))
        state.update(status="partial" if errors or repaired < len(missing) else "ok", repaired=repaired,
                     failures=errors, finished_at=datetime.now(timezone.utc).isoformat())
        store.set_source_state(state_id, state)
        reports.append(state)
    return {"sources": reports, "repaired": sum(state["repaired"] for state in reports)}


async def main_async(args):
    store = Store(args.db)
    source_ids = args.sources.split(",") if args.sources else None
    if not args.repair_only:
        result = await poll_sources(store, historical=True, start_date=args.start, source_ids=source_ids)
        # Print compact counts; source failures are preserved rather than swallowed.
        print(json.dumps({k: v for k, v in result.items() if k not in {"created", "revised"}}, ensure_ascii=False, indent=2))
    if args.repair_only or args.repair_summaries:
        print(json.dumps(await repair_missing_summaries(store, source_ids=source_ids), ensure_ascii=False, indent=2))
    if args.prices:
        print(json.dumps(await refresh_prices(store), ensure_ascii=False, indent=2))
    report = coverage_report(store, args.reports, start_date=args.start)
    print(f"Coverage saved; {report['total_articles']} total public-source news records.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2024-05-15")
    parser.add_argument("--sources", default="")
    parser.add_argument("--db", default=None)
    parser.add_argument("--reports", default=None)
    parser.add_argument("--prices", action="store_true")
    parser.add_argument("--repair-only", action="store_true", help="Retry missing source excerpts without repeating archive traversal")
    parser.add_argument("--repair-summaries", action="store_true", help="Retry missing source excerpts after archive collection")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
