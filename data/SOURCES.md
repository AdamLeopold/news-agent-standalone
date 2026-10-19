# 新闻来源与时间证据

采集开始于 2026-10-14，新加坡时间。数据范围为 2024-05-15 至采集时可公开访问的新闻；并非全球新闻全集。来源状态、失败请求、分页游标和逐月覆盖由 `reports/coverage.json`、`reports/coverage_monthly.csv` 与 `reports/coverage.md` 输出。

| 来源 | 已核实公开入口 | 历史方法 | 时间规则 |
|---|---|---|---|
| EIA Today in Energy | https://www.eia.gov/rss/todayinenergy.xml | 官网 `archive.php?my=2024`、2025、2026，相关标题的原文页摘要 | RSS 有时区则保留；档案只有日期则 `date`；损坏 pubDate 不补猜年份 |
| 欧盟 DG Energy | https://energy.ec.europa.eu/node/2/rss_en | `news_en?page=N`，截至开始日期停止 | 档案显示只有日期；Drupal 默认正午值不作为真实时刻 |
| Equinor | https://www.equinor.com/news | 官网 English sitemap `sitemap.xml?lang=en`，相关 gas/LNG 文章 | 优先 article:published_time / JSON-LD 时区时刻；URL日期仅作后备并标记来源 |
| Cheniere | https://lngir.cheniere.com/news-events/press-releases/rss | 官网 press-releases `?page=N` | RSS 有时区；列表时刻采用页面明示 Eastern Time |
| Offshore Energy | https://offshore-energy.biz/feed | 公开 `/api/articles` 已验证日期过滤和分页，回补 gas/LNG/FSRU 标题候选 | 使用 publisher API publishedAt；www 域feed误配，裸域feed正常 |
| Gassco UMM | https://umm.gassco.no/ | 官网页面 `/archive` 和 GET 表单 publicationFromDate/ToDate，按月请求 | 表格的 Publication date/time 含 UTC offset；事件开始/结束不是发布时间 |
| GDELT | https://api.gdeltproject.org/api/v2/doc/doc | 最多最近 90 天、周切片、每次最多250项 | seendate 只作发现记录，不能伪装为原文发布时间；结果截断被记录 |

每个源每秒至多一次请求，源之间独立抓取。断点存在 SQLite source_states；已导入新闻按规范化URL去重，摘要/标题修订保留版本。只保存来源短摘要或原文首段摘录，未建立付费全文镜像。

Gassco 档案行没有 live 表的 `tr[id]`，采集按公开详情链接解析；每页20条，使用官网脚本中的 `page.page` 翻页并核对 `Total records`。本轮可观测记录从2024-09-19开始，2024-05至08未获取到公告，`historical_status=partial` 且保留限度理由。成功请求不能代表档案完整。

HTTP 429 后至少15分钟退避，`Retry-After` 更长时遵从其值；GDELT各请求另以6秒间隔限速。Offshore Energy若上次成功距今超过12小时，会通过已验证API从上次成功前一日有限回补，发现时间仍为当前时间，冻结预测不被回写。

`published_at` 仅保存有钟点和可确定时区的真实 UTC 时间；`published_date` 保留来源日历日期。没有钟点时 `published_at=null`、`timestamp_precision=date`；没有日期时 `unknown`。`first_seen_at` 是本系统第一次发现时间，历史回补仍使用真实采集时间。`summary_created_at` 是本次来源摘要清洗时间，不冒充出版时间。

日期精度不足的记录不能精确划入17:15结算窗口，因此保留在不确定时间组。历史记录 `ingestion_kind=backfill` 不批量触发实时推理。

## ICE 合约日价

API：https://api.db.nomics.world/v22/series/ICE/DUTCH_TTF_GAS_FUTURES 。数据集metadata指向ICE官方产品，导入单月合约并保存contract code、delivery month、provider index time和来源链接。排除季度、季节和年条带。

DBnomics返回metadata没有直接确认该字段是官方结算价，因此 `price_type=reference`、`verified=false`；不能用于官方结算预测成绩。只有核实为结算价后才能进入结算评分。

2026-10-14 又核查了 DBnomics 官方 ICE fetcher 固定提交 `3eb9e399588113f014e8341dd016f22feaf220bd`：转换器仅转存 ICE 公共 chart 的 `bars[date,value]`，没有结算字段名。因此类型仍为 reference。价格服务额外查询该采集器明示的 ICE 官方公开 chart API，补最近3个单月合约，避免 DBnomics 一天左右的快照滞后；不使用 intraday lastPrice 冒充日价。

波动率工具只使用指定的同一合约、预测日前已经完成的61个价位形成60个价差，冻结标准差与门槛。61项不足或价格类型未验证时异常分类不评分，换月差不会混入。

## 更新与回补

```sh
.venv/bin/python -m ttf_news.backfill --sources eia,eu_energy,equinor,cheniere,gassco --prices
```

来源失败和零条新闻分别显示；重跑根据来源游标续抓。覆盖报告是观测的可获取范围，不是数据全集或模型准确率证明。

实时 Offshore RSS 可能被源站重定向至失效的 www/feed。RSS失败时使用同源公开 API 从上次成功前一日有限补抓，未有有效断点时仅查询近两日；RSS错误仍保留。API增量记录使用真实发现时间及 live 标签，区别于最初历史回补。
