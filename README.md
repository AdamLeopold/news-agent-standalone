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

顶部支持中文 / English 切换，并保存语言选择。结构引擎的中英文由同一份模型决策分别渲染：事实、机制权重、方向、分数、报价和引用共用，切换语言不会重新预测。详情也展开原简报的同一决策并缓存，不另判方向。旧版本保留原决策及对应渲染方式，原新闻保留原文。

2.4.5 将生成分为三步：模型逐篇从完整已保存来源中选择证据编号，识别供需类型、区域联系与时域，程序直接取回对应原文；随后评估全部机制的重要性，程序按固定经济方向和事件分组计算净作用；最后模型读取已计算净值及同合约行情，选择收盘情景。Gassco 公告容量、起止时间和修订直接来自结构化来源。重复公告不按条数放大同一风险，零权重也保存原因并展示其潜在传导。未来供给、实际复产、技术研究、商业合同和当前容量约束分别处理；进口设施本身不等于新增需求，欧洲接收瓶颈缓解与增量采购竞争使用不同机制。地域元信息与潜在传导分别处理：地域未明确不会自动抹掉未来供给的条件预期渠道；只有来源明确建立的欧洲或全球 LNG 联系才按该渠道解释，区域未来项目仍需评估市场联系。重要性允许为零，由模型结合全窗口判断。普通新闻的事实归类和重要性仍由模型判断。

行情面板提供 ICE 公开延迟盘中报价与分时曲线（来源标注至少延迟15分钟，每60秒检查），并显示同一月合约与前一完整交易日参考价的差额。报价保存成交时间和本机首次获取时间；只有分析当时已经获取的同合约报价进入新输入快照。

日 K 使用 Capital.com 公开产品页确认合约身份后获取的 **CFD ASK D1 OHLC**，按来源UTC日线绘制，每10分钟刷新，支持20/60/90根窗口和PNG导出。当前月合约历史不足时只显示实际取得的K线；当日标为未收盘。CFD与ICE期货是不同报价口径，不用于官方结算评估，不把每日单一参考价拼成OHLC。来源失败时保留缓存并标明状态。换月必须重新确认该交割月的公开产品页和instrument ID；无法确认时留空并报告失败，不借用其他月份。

- **实时新闻**：结算倒计时、当前窗口新闻、新闻压力与日结算方向、历史相关事件、来源健康，以及右侧最近新闻滚动栏。发现新闻后先显示卡片，模型完成后更新；源站延迟、轮询及推理分别计时。历史回补与未分析新闻明确标记，可主动请求分析。
- **手动分析**：输入正文，可填写来源和发布时间；结合当前窗口、前五日摘要和历史事件分析。手动数据不进入正式新闻集或事件库。
- **详细分析**：按需生成并缓存，展示事实、TTF传导、当日新闻冲突、历史类比、已计价情况、三个情景和结论失效条件。依据和来源可复查。模型失败会保留失败状态，不自动判涨或判平。

`news_impact` 是新闻造成的价格压力判断；`close_forecast` 是新闻证据驱动的日结算方向预判。两者各有五档标签。把握程度是模型自报值，不能解释为已经校准的成功率。没有价格、精确时间或历史类比时应明确说明缺失。

当前结构修复的实际对照、API 流程和原记录保全见 [2.4.5 验证报告](reports/structured_v2_4/validation_2026-10-23.md)。

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

收盘冻结只选择该交易日截止前已完成的 **live** 预测；手动情景、历史单条分析及晚完成结果不会覆盖冻结记录。SQLite保留新闻/事件版本；结构引擎的详情直接复用原简报的事实与决策，不混入点击后新增新闻。手动拷贝的标题和完整正文唯一匹配已知正式来源时，推理视图复用该版本及修订记录；保存的手动原文仍独立保留。

## API、导出和验证

交互式接口文档：`http://127.0.0.1:8765/docs`。

主要接口：`/api/health`、`/api/dashboard`、`/api/market`、`/api/news?scope=recent|all|live`、`/api/manual/analyze`、`/api/analyses/{id}`、`/api/analyses/{id}/detail`、`/api/analyses/{id}/translation?part=brief|detail`（GET读缓存，POST入队）、`/api/translations/{id}`、`/api/events`、`/api/coverage`、`/api/monitoring`、`/api/daily/{date}`、`/api/stream`。英文任务与原分析共用单并发队列，实时简报优先；翻译指标单独记录，不混入预测准确率和原推理耗时。

CSV/JSONL 导出位于 `data/exports/`；包含逐条新闻、连续交易日精确窗口汇总、大事件库、合约参考价、旧日数据、分析输入与输出、覆盖报告。空窗口也保留记录，零条不等于源站已确认无新闻。`/api/export/{filename}` 同样支持浏览器下载。SSE 提供序号重放，后台任务重启后恢复。

```sh
export TIKTOKEN_CACHE_DIR="$PWD/data/cache/tokenizer"
.venv/bin/python -c "import tiktoken; tiktoken.get_encoding('o200k_harmony')"
.venv/bin/python -m pytest -q
cd frontend
npm test
npm run build
```

首次测试先预热标准 `o200k_harmony` tokenizer；没有缓存时，tiktoken 会下载官方词表。缓存是普通依赖，不含模型权重或新闻数据，不随 GitHub 发布复制。已通过环境变量指定缓存时，对照工具会保留该配置。后端测试自动隔离数据，手动分析接口按提交前后的正式新闻/事件数量核对，不依赖本机父目录的历史 Excel 文件。

实时心跳、模型耗时、采集失败与冻结评估记录在数据库中。`/api/monitoring` 只有实际经过七天、具有心跳记录且无超过180秒的心跳缺口，才标为七天连续运行通过。该标记还必须与覆盖/模型失败报告共同审阅；不能以启动成功替代七天验收。

原七天验收已按既有基线结束，结果为未通过，详见 [七天报告](reports/seven_day_validation.md)。验收自动跟进已暂停，持续采集仍由后台服务运行。后续修复及部署保留全部原预测、失败、冻结窗口和心跳记录，不重置验收起点。

### 历史分析引擎 2.3（2026-10-20，保留复现）

以下记录和对照命令属于旧 2.3 引擎，保留用于复查原预测及失败，不代表新结构引擎的验证结果。新引擎的源证据、实际调用和完整 API 流程另外保存在 `reports/structured_v2_4/`；两代结果分开统计。

- 新闻逐条保留实体、原始因果句、否定、修订与公告时间/容量；完整窗口先保全，历史叙述后压缩。上下文按需使用8K或16K，gpt-oss:20b初次使用medium推理。
- 默认`semantic_high_retry=False`：首轮使用medium，校验失败后仍以medium携具体错误重新分析，总共最多两次。实测high多次耗尽输出额度仍无完整JSON；最近未来公告案例在12,288 token额度下仍失败，因此生产采用可直接利用校验反馈的默认方式。原失败原文、耗时和设置均保留，提示词、供需方向与时点校验不因运行失败而放松。
- 事实或机制校验失败的第二次请求只携完整来源与本地验证器的完整去重诊断，不再附上已判错的候选摘要、证据槽或结论。格式／截断修复仍使用原有界草稿；两类路径与原始失败分别记入审计，移除草稿不被当作已证明的错误因果解释。
- 只有第二次完整来源、完整错误反馈及该路径所需的有界草稿装不下原预算时，修复请求使用一次24K上下文；输出仍为3072（详析或截断修复4096），另留1024 tokens。事实／机制纠错不携候选草稿。首请求不变，不删新闻、不增加调用次数；超限仍明确失败，实际预算写入审计。
- 模型风险备注最多两项；完全相同的备注保留原文审计，在既有风险汇总阶段去重并记录数量，不因此丢弃正确正文。详析各事实段最多两项、每段280字，引用最多16项；重复正文仍须重新生成。
- 独立high仅作显式复现实验：`NewsAnalyst(semantic_high_retry=True)`默认限定当前Gassco运营公告；另设`semantic_high_scope='all'`可复现广泛high实验。high使用原始系统提示和完整事实，不附前次错误；16K上下文按输入／输出3072／12288、5120／10240、7168／8192 tokens选择首个完整容纳档位，另留1024 tokens。开启实验也不增加第三次调用，不减少新闻；实际路径和原始失败分别记录。
- 持续事件与独立历史类比分开；不以事件已入库推断已计价。日聚合历史仍保留在库中，不当作单条事件。
- 生成后校验供需方向、证据栏、已计价依据、时间状态与价格基准。错误需重新生成，失败保留失败状态；不会只改理由沿用结论，也不控制五类比例。
- 公告分析先复制并核对来源事实表，再生成判断；计划类型、原因、运行时段、不可用容量与实际流量分别处理。详析情景必须写条件，供需占比必须有原始比例或可核对的分母。
- 公告事实表保留设施原始类别及 UTC 起止时刻，并标出不重叠的时段。不同日期的 mcm/d 不能相加成同一时刻的供给损失；摘要、证据、风险及详析均使用相同的来源事实。
- 手动贴入的 Gassco 新闻只有与唯一已知来源的完整标题、原文、公告编号和事件区间精确一致时，才补齐来源时钟并合并重复输入；标题状态与结构化状态有冲突则不合并。原手动输入、来源记录及对应哈希保留。
- 容量列表按公告及当前／未来时段逐项绑定；已观察的同合约价差与预测幅度分开校验。不同公告数值串用、错容量字段、非重叠时段求和、虚构实际流量和已计价均拒绝。
- 来源中的供应风险降低／增加不当作实际供气减少／增加；风险原句保留供模型阅读，真实供气增减及数量另行提取。证据栏按风险缓和／加剧的边际压力核对，仍允许结合其它事实给净flat，不补流量恢复。
- 收盘目标相对上一交易日同合约价格；盘中已观察变化与新闻增量分开。当两类依据都不足时，`close_forecast.evidence_status=insufficient_evidence`且`direction=null`，界面显示“暂缺收盘判断”。这不属于flat。
- 价格锚点必须恰好属于目标日的前一交易日，合约一致且在分析时已可用。缺前一交易日日价时，更早的日价仍可展示，但不进入模型的昨收差额运算；盘中绝对报价保留。事实判断、新闻方向与收盘可判断性分别校验。
- 引用已观察报价时保留原单位，并区分价格水平、当前报价相对前一交易日的价差、前一交易日本身涨跌和未来目标幅度。未来检修尚未开始不代表期货价格不会提前反应；缺少反应观测时保留未知，弱传导或有来源的抵消仍可判为flat。
- 已有合法且新鲜的同合约价格基准时，新闻较弱或没有观察到逆转，不构成单独取消条件收盘预判的理由；若以相反新闻机制解释暂缺判断，须引用当时已知来源，并说明对该基准的实际冲突。该要求不把盘中变化当作最终收盘，也不强制复制行情或新闻方向。
- 行情有实质变化时，至少间隔15分钟生成新的条件预判。已有分析、已冻结结算窗口和验收起点保持原样；监控按prompt版本分别统计。
- 非付费文章可保存有来源和发现时刻的短事实节选。新增信息作为新版本保存，不能回写为历史时点已知。
- 日价使用只追加的`price_versions`记录本地可用时间；后续修订和回补不会进入旧时点输入。历史事件结果按结果实际可用时刻补记，原预测快照不变。

隔离对照入口：`python scripts/validate-news-logic.py --prepare`，再分别使用`--engine original`和`--engine candidate`。原版本源码与测试原始响应保存在本机`reports/repair_2026-10-20/`；对照只测试机制和接口，不等同于正式结算准确率。

单元测试自动使用临时数据目录；模型对照只读取冻结输入，不向生产数据库插入假设新闻或预测。

正式回归沿用 `semantic_regeneration_cases_v2_2.json` 的独立二十项冻结输入：原十五项机制、四项价格锚点及真实井源 `real_future_well_b1`。文件名保留建立时版本，本轮实际调用版本为2.3。当前完整起点是 `candidate-bounded-output-validation/` 的二十条全新实际记录；只对完整路径变化的案例依次叠加 final-output-scope、source-feedback、risk-polarity、physical-unit、evidence-slot、price-zero-consistency 和 pressure-predicate 的 `candidate-…-validation/` 实测阶段。详析使用相应 `-detail/` 目录，独立统计。注册阶段不代表已经执行或通过，实际终态和独立正文复核见验证报告。更早的 semantic-regeneration、price-observation、final-tense、detail-grounding 及2.0／2.1／2.2阶段全部保留为历史，不补当前缺项。

以下复现命令会实际调用本地模型；先由操作者暂停本项目推理 worker，并确认当前 `/api/health` 的 `inference.enabled=false`，采集和行情继续运行。CLI 逐次检查本机健康状态并串行执行，不会修改旧夹具或生产数据库。每次复现使用独立时间戳 `run-id`，避免已有文件只返回 `EXISTS`。详析的控制为 `supply_restore_40_with_flat_baseline`，与简报分目录保存：

```sh
TTF_COMPARE_RUN="reproduce-$(date -u +%Y%m%dT%H%M%SZ)"
.venv/bin/python scripts/validate-news-logic.py --engine candidate --case-file reports/repair_2026-10-20/semantic_regeneration_cases_v2_2.json --allow-paused-live-queue --run-id "$TTF_COMPARE_RUN"
.venv/bin/python scripts/validate-news-logic.py --engine candidate --case-file reports/repair_2026-10-20/semantic_regeneration_cases_v2_2.json --allow-paused-live-queue --run-id "$TTF_COMPARE_RUN" --case supply_restore_40_with_flat_baseline --detail
```

当前二十项按上述完整起点及后续案例跟进的最新实际终态选择；最新失败不能回退旧成功，也不能将拒绝的raw方向当产品预测。来源纠错和风险／基准修复的逐轮影响清单分别保存在 `source_feedback_affected_cases_v2_3.json` 与 `risk_polarity_affected_cases_v2_3.json`，包含完整请求、诊断、草稿与终稿的零调用对照；未受影响的旧实际路径只有全部相等才可保留，不算最后源码的新调用。真实井源额外检查正文是否明确保留将封井的未来动作，不规定新闻方向。独立复现的时间戳目录不自动替换冻结验收阶段。

真实 EPCM 锚点案例的原 `expected_news=flat` 没有外部验证标签。历史上曾对固定旧回答单列标签分歧，但后续全文复核确认其中“未产生即时价格冲击”没有价格反应证据，原资格不能豁免该事实错误；原记录、初审与更正全部保留。任何新回答仍须来源角色、价格锚点、最终语义与完整实际路径通过；未经两份绑定该新原文SHA的 Codex subagent 独立全文复核，不沿用旧例外。标签分歧不计预测正确、严格通过或真人／交易员背书，也不适用于合成控制。

以下命令不调用模型、不访问数据库，仅把已保存的真实响应逐轮送入当前代码，比较系统提示、输入、JSON约束、校验错误、重试草稿、调用次数及完整终稿；同时绑定原文件SHA和全部后端源码SHA。证明写入 `full_path_equivalence_v2_3.json`，原证明按SHA归档。任一请求或校验路径变化都不能认定等价，必须有新实际终态；这也不意味着随机模型输出可确定性复现：

```sh
.venv/bin/python scripts/repair-validation-report.py --replay-paths
```

发布仓库保留冻结对照输入与脱敏后的逐次模型响应。若要在 GitHub 克隆目录复现原版，可先将提交 `5040a1e78b10dc1cae5c72f155a1888761570d73` 的 `ttf_news/` 导出到 `reports/repair_2026-10-20/original_source/`；原 SQLite 与验收记录从对应 Release 快照恢复。最终机制、详析和记录保全结果见 [修复验证报告](reports/logic_repair_validation_2026-10-20.md)。

复查冻结对照、原记录哈希与验收基线，并重新生成总报告；`--strict` 会在必需证据缺失或检查失败时返回非零状态。数据库以只读方式检查：

```sh
.venv/bin/python scripts/repair-validation-report.py --strict --unit-tests reports/repair_2026-10-20/source-binding-final-unit-tests-rerun.xml --frontend-log reports/repair_2026-10-20/frontend-notice-scope-final-tests.log
```

严格全输出验收与独立运行发布判据分开。隔离详析或价格锚点案例两次真实失败时，其全部成功指标仍为 false；只有完整调用记录、最终路径、无结果／方向的失败状态及绑定原文SHA的独立全文复核均核实时，才作为“已结束但未交付”另列，不算有效输出或预测正确。生产中真实手动简报、详情、英文与当前预测的完整成功流程仍必须另行通过，旧成功不替代新失败。

本机后端启动并加载当前版本后，可运行真实流程验证。该命令会实际调用本地模型，使用一条已采集的真实新闻新增手动分析、详析和英文缓存；当前交易窗口尚无当前版本预测时，另追加一条真实新闻驱动的收盘预判。旧预测、冻结记录和验收基线保留。阶段耗时、错误及新增记录ID写入 `reports/repair_2026-10-20/live_smoke_test.json`。结构校验通过后，还需对照原始输入独立复核真实简报、详析和实时分析，才能判断逻辑是否通过：

```sh
.venv/bin/python scripts/validate-deployment.py
```

将最终源码和脱敏验证材料同步到既有 `.runtime/github-publish` 发布目录及网页下载目录，先预检再复制：

```sh
.venv/bin/python scripts/sync-public-evidence.py --dry-run
.venv/bin/python scripts/sync-public-evidence.py
```

同步工具采用显式文件清单，排除本机原版源码归档与备份。刷新后的数据快照及 SQLite 附件另行扫描；Git提交、推送、网站发布和服务重启均由操作者单独执行。

同步同时生成 `reports/publication_manifest.json`，分别保存原件与公开派生件的字节SHA。私有发布判据仍按原字节核对；在公开克隆中，可显式验证脱敏副本及其引用：

```sh
.venv/bin/python scripts/repair-validation-report.py --release-decision --public-copy --publication-manifest reports/publication_manifest.json
```

该命令写入独立的 `release_decision_public.json`，保留原始来源SHA与公开字节SHA，不覆盖私有 `release_decision.json`。映射缺失、路径不一致或公开字节与清单不符时验证失败；模型原始判定及严格验收结果保持原样。

已确认的分析错误使用只追加的 `analysis_review` 复核记录，分别绑定原始输入、简报或详析的哈希。原预测和冻结记录保留；页面按原因显示价格基准或来源事实复核状态，当前看板排除已确认无效的简报。新生成的详析不会自动清除旧简报的复核结论。收盘冻结仅考虑当时已知的复核信息；后来的复核不会回写原冻结记录。部署后可先只读核验清单，再由本机操作者追加复核：

```sh
.venv/bin/python scripts/apply-analysis-reviews.py --manifest reports/repair_2026-10-20/post_restart_analysis_review.json
.venv/bin/python scripts/apply-analysis-reviews.py --manifest reports/repair_2026-10-20/post_restart_analysis_review.json --apply
```

维护时使用 `TTF_NEWS_INFER=0 ./scripts/ttf-news start` 启动后端，可暂停本项目的推理 worker，同时继续采集、行情、维护和心跳。任务仍留在队列，`/api/health` 的 `inference.enabled` 表示本次启动状态。恢复时移除该变量再按项目工具核实并重启后端；无需停止 Ollama。

刷新快照或恢复 Release 数据后，可只读复查全部 JSON、JSONL、CSV 与 SQLite TEXT 中的凭据、本机路径及内部思维字段。以下示例在包含 `frontend/public/snapshot` 的项目目录运行；扫描不会修改快照或数据库：

```sh
python scripts/scan-public-snapshot.py --scan --snapshot frontend/public/snapshot --database /path/to/restored.sqlite3 --report reports/public_scan.json
```
