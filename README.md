# TTF 新闻分析工作台

独立运行的本地产品：React/TypeScript、FastAPI、SQLite、Ollama `gpt-oss:20b`。界面与分析默认中文，保留来源原文及链接，不自动交易，不进行模型微调。

GitHub 网站：[TTF News](https://adamleopold.github.io/news-agent-standalone/)。源码、发布快照及运行方式见[部署说明](deployment/README.md)。公网实时 API 未连接时明确显示快照状态；新的模型分析需要本机服务及已授权的公网入口在线。

## 运行

首次安装（已有本地 Ollama 及 `gpt-oss:20b`）：

```sh
cd /Users/adam/TTF/news-agent-standalone
./scripts/install.sh
./scripts/ttf-news import-legacy
./scripts/ttf-news backfill
./scripts/ttf-news prices
./scripts/ttf-news start
```

浏览器访问 **http://127.0.0.1:8765**。后台服务关闭终端后继续运行；运行期间通过进程级 `caffeinate` 保持系统不因闲置休眠，屏幕可关闭，不改永久系统设置。主动休眠、合盖或关机期间无法采集，恢复后从检查点补抓。开机后再次执行 `start`。程序只绑定本机地址；不需要云端 API、账号或订阅。

本次交付的数据位于 [data/exports](data/exports/)，实现范围与真实验证状态见 [验收记录](reports/ACCEPTANCE.md)，逐源缺口见 [覆盖报告](reports/coverage.md)。[交付清单](reports/delivery_manifest.json)记录文件 SHA-256、数据数量、模型版本及运行状态；七天结果以实际监测记录为准。

```sh
./scripts/ttf-news status
./scripts/ttf-news stop
./scripts/ttf-news stop --with-model  # 仅停止本项目自行启动并记录的 Ollama
./scripts/ttf-news backup
./scripts/ttf-news export
./scripts/ttf-news report --output reports/current_status.json
```

配置环境变量：`TTF_NEWS_PORT`（8765）、`TTF_NEWS_POLL_SECONDS`（60）、`TTF_NEWS_MODEL`、`TTF_NEWS_OLLAMA_URL`、`TTF_NEWS_DATA_DIR`。日志位于 `.runtime/app.log`。SQLite 数据库位于 `data/ttf_news.sqlite3`，备份使用 SQLite 在线备份接口。

## 界面和分析

顶部支持中文 / English 切换，并保存语言选择。英文页的简报、风险、情景和完整详析由同一 `gpt-oss:20b` 翻译并缓存；原预测方向、分数、把握程度、数值和引用保持不变，不因切换语言重新预测。英文尚未生成或失败时显示等待/重试状态。来源新闻保留原文。

行情面板提供 ICE 公开延迟盘中报价与分时曲线（来源标注至少延迟15分钟，每60秒检查），并显示同一月合约与前一完整交易日参考价的差额。报价保存成交时间和本机首次获取时间；只有分析当时已经获取的同合约报价进入新输入快照。

日 K 使用 Capital.com 公开产品页确认合约身份后获取的 **CFD ASK D1 OHLC**，按来源UTC日线绘制，每10分钟刷新，支持20/60/90根窗口和PNG导出。当前月合约历史不足时只显示实际取得的K线；当日标为未收盘。CFD与ICE期货是不同报价口径，不用于官方结算评估，不把每日单一参考价拼成OHLC。来源失败时保留缓存并标明状态。换月必须重新确认该交割月的公开产品页和instrument ID；无法确认时留空并报告失败，不借用其他月份。

- **实时新闻**：结算倒计时、当前窗口新闻、新闻压力与日结算方向、历史相关事件、来源健康，以及右侧最近新闻滚动栏。发现新闻后先显示卡片，模型完成后更新；源站延迟、轮询及推理分别计时。历史回补与未分析新闻明确标记，可主动请求分析。
- **手动分析**：输入正文，可填写来源和发布时间；结合当前窗口、前五日摘要和历史事件分析。手动数据不进入正式新闻集或事件库。
- **详细分析**：按需生成并缓存，展示事实、TTF传导、当日新闻冲突、历史类比、已计价情况、三个情景和结论失效条件。依据和来源可复查。模型失败会保留失败状态，不自动判涨或判平。

`news_impact` 是新闻造成的价格压力判断；`close_forecast` 是新闻证据驱动的日结算方向预判。两者各有五档标签。把握程度是模型自报值，不能解释为已经校准的成功率。没有价格、精确时间或历史类比时应明确说明缺失。

## 数据来源和完整性

来源配置在 `data/sources.json`：Offshore Energy、EIA、EU DG Energy、Equinor、Cheniere、Gassco，以及 GDELT 近期补充发现。优先 RSS/API，其余使用公开档案，每源限制请求频率。保存短摘要或事实摘要及来源链接，premium 内容不绕过访问限制。

历史从 **2024-05-15** 重叠开始，逐条保存标题、摘要、来源、发布时间、修订时间、实际首次发现和摘要生成时间。没有明确时区/时刻的历史稿件保持 `date` 或 `unknown`，不会补造分钟时间。GDELT 发现时刻也不会冒充发布时间；其滚动查询存在历史深度与结果上限，429 记为失败。

原数据以 `legacy_daily` 保存：3456 个日记录和275个历史大事件。旧新闻为每日多条聚合，没有单条 URL 或真实盘中时间，不能拆成单条新闻。旧人工 Explanation 与事后标签保留为档案，禁止进入预测输入。新事件有版本、来源、状态和实际结果可用时间；同期价格变化不当作因果效应。

`/api/coverage` 与导出的 `coverage.json` 提供逐源抓取状态、每月数量、时间精度和失败清单。**没有记录的月份不等于已确认无新闻**，需结合该源历史覆盖状态。日期不精确的记录不会强行归入17:15窗口，因此精确日汇总并不包含所有日期不明确的稿件；逐条导出保留它们。

实时首次发现的日期不精确新闻会正常触发简报，并作为本窗口已观察到的补充背景。它们仍保持 `trade_date=null`，输入快照注明按发现时间提供分析、无法确认原始发布窗口；历史回补的日期不精确记录不使用这一实时替代。

## 日结算与价格

采用 `Europe/Amsterdam` 正常 **17:15** 日结算，窗口为 `[前一交易日结算, 本日结算)`；边界新闻进入下一窗口。周末、休市进入下一交易日，12月24/31日按14:00早结算。2026/2027按官方交易日历；较早年份采用历史规则代理并标记，不能把它宣传为完整历史日历验证。

- 官方规则：https://www.ice.com/publicdocs/endex/circulars/E26004.pdf
- 官方日历：https://www.ice.com/publicdocs/ICE_Endex_Trading_Schedule.pdf

价格使用 DBnomics 的 ICE 合约级日值，保存交割月和来源。**当前来源字段尚未验证为官方日结算，因此标记为 `reference`，不参与正式结算评分**。不使用 Yahoo NYMEX 数据冒充 ICE。价格前值、60日波动率均使用同一个交割月；缺失前一交易日价格时不正式评分。

五档实际结算标签：`|ΔC|≤0.30 EUR/MWh` 为基本维持；异常门槛为 `max(0.30,2σ60)`，`σ60` 使用预测前60个完整价差的样本标准差。其他按正负分涨、跌。缺少60个完整价差时不强制区分普通与异常涨跌。门槛和价格快照在预测时冻结。

收盘冻结只选择该交易日截止前已完成的 **live** 预测；手动情景、历史单条分析及晚完成结果不会覆盖冻结记录。SQLite保留新闻/事件版本；详情复用原简报的输入快照，不混入点击后新增新闻。

## API、导出和验证

交互式接口文档：`http://127.0.0.1:8765/docs`。

主要接口：`/api/health`、`/api/dashboard`、`/api/market`、`/api/news?scope=recent|all|live`、`/api/manual/analyze`、`/api/analyses/{id}`、`/api/analyses/{id}/detail`、`/api/analyses/{id}/translation?part=brief|detail`（GET读缓存，POST入队）、`/api/translations/{id}`、`/api/events`、`/api/coverage`、`/api/monitoring`、`/api/daily/{date}`、`/api/stream`。英文任务与原分析共用单并发队列，实时简报优先；翻译指标单独记录，不混入预测准确率和原推理耗时。

CSV/JSONL 导出位于 `data/exports/`；包含逐条新闻、连续交易日精确窗口汇总、大事件库、合约参考价、旧日数据、分析输入与输出、覆盖报告。空窗口也保留记录，零条不等于源站已确认无新闻。`/api/export/{filename}` 同样支持浏览器下载。SSE 提供序号重放，后台任务重启后恢复。

```sh
.venv/bin/python -m pytest -q
cd frontend
npm test
npm run build
```

实时心跳、模型耗时、采集失败与冻结评估记录在数据库中。`/api/monitoring` 只有实际经过七天、具有心跳记录且无超过180秒的心跳缺口，才标为七天连续运行通过。该标记还必须与覆盖/模型失败报告共同审阅；不能以启动成功替代七天验收。

应用内另设“TTF 新闻产品七天验收”跟进，每6小时核查本机状态，正常时保持安静；满七天后输出验收报告并暂停验收跟进。持续采集由后台服务运行，不依赖这项跟进。电脑关机或主动睡眠会留下真实缺口；不会自动删除这些记录或重新开始计时来掩盖中断。
