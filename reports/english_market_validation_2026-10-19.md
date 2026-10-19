# English analysis and market chart validation — 2026-10-19

- Backend: 123 tests and 22 subtests passed. Frontend: 23 tests passed; local and Pages builds succeeded.
- Five real gpt-oss:20b English versions completed (four briefs and one detail). All translated text fields were checked; forecast directions, scores, confidence and references match their original records.
- English API elapsed times: 186.943s, 79.723s, 77.442s, 454.274s, 85.463s. These include waits in the shared Ollama service; they are not prediction accuracy measurements.
- Three frozen forecasts and the 2026-10-14 validation baseline are unchanged. Planned project restarts were recorded; shared Ollama was not stopped.
- ICE delayed last-trade data: Nov26, 56 timestamped intraday samples. Source labels its display as delayed at least 15 minutes.
- Capital.com CFD ASK D1: TRNLTTFMX2026, 22 genuine OHLC bars. Chart day boundary is UTC. Current bar is provisional. This feed is separate from ICE futures and excluded from official settlement scoring.
- Browser checks: English current outlook, English manual detail with historical comparisons/scenarios/references, intraday chart, daily candles and PNG preview. Source titles remain original.
- Snapshot captured 2026-10-19T09:21:56.421974+00:00; 329 files verified by SHA-256. SQLite release snapshot includes all tables, including translations and quote/candle versions.
- Seven-day validation is still in progress; official evaluation count remains zero.

## English records

- a166a65c-4773-4343-ac6c-e1205a810f7b / brief / 18 translated fields / 186.943s
- fa20f079-0cec-4e82-a4a2-30cb27e42e6e / brief / 14 translated fields / 79.723s
- ca8844dd-f4c0-4fd2-b4fd-8306ff877fff / brief / 14 translated fields / 77.442s
- 4ff43203-1b97-45aa-a57d-f547ae80d992 / brief / 16 translated fields / 454.274s
- a166a65c-4773-4343-ac6c-e1205a810f7b / detail / 26 translated fields / 85.463s
