当前验收状态：未就绪（第12次流程已终态失败，结束于 2026-10-21T07:45:24.893495+00:00）。

12次完整生产流程全部失败、0次完整成功（2 well、4 EPCM、6 Gassco）。第12次手动简报两次真实输出未通过：混淆公告不可用容量与实际减供/停产，且证据格式未满足要求（原文存在完整事实句，“TFF机制”拼写及缺少箭头触发既有格式检查）；后续手动详析与英文未交付。current live虽为模型完成状态，独立全文复核已确认两项实质错误：未来增供放入TTF看涨栏，以及把已开始/非计划的Gassco公告一概写作未来计划且无当前损失。root已正常追加失效复核元数据，英文忠实继承同错并随源排除；原输入、结果和完成状态不改。前端尚未发布，8类已接受错误仍open。

最终源码7811276/d054、20条简报及1条诊断详析完整路径绑定不变：保留原15条安全输出、5条真实失败和原31+2次调用，不默认填flat，不声称20/20或市场准确率。根测试1523项加375子测试、公开副本1517项通过加6项既有跳过及371子测试、前端26项通过均维持原证据。PID86618及164分析、5冻结快照、基线保全引用保持；测试或基础设施通过不代表生产验收完成。

当前证据：[第12次终态失败](repair_2026-10-20/production_twelfth_full_workflow_v2_3.314a57800db9ea11.json)、[21条源码绑定](repair_2026-10-20/current_operator_prose_placement_final_twenty_plus_detail_manifest_v2_3.json)、[完整路径](repair_2026-10-20/full_path_equivalence_v2_3.b5b1ec7e667795db.json)、[单测绑定](repair_2026-10-20/operator_prose_full_unit_binding_rerun_v2_3.json)。本次更新前的运行中报告已按SHA归档，原11次历史、所有失败及旧模型记录均保留。 原“证据为空”的错误描述版本也已SHA归档；[current独立全文复核](repair_2026-10-20/independent_production_twelfth_current_live_review_v2_3.json)及[追加失效manifest](repair_2026-10-20/production_twelfth_current_live_pending_review_v2_3.json)与当前状态绑定。

---

# TTF 新闻分析逻辑修复验证

生成时间：2026-10-21T07:43:31.548513+00:00。当前版本：`ttf-news-standalone-2.3`。

**结论：尚需处理／等待剩余证据，不能据此宣布全部修复验收通过。**

本报告离线复检已有产物，没有调用模型或写数据库。只检查场景机制、方向契约、重试与记录保全；行情仍为 reference，不统计官方结算准确率。

旧迁移日价的 fetched_at 历史以既有时钟为准；新抓取以 HTTP 返回时刻记录；无法重建旧数据更精确的实际到达时间。


## 真实运行已接受错误与闭合状态

**尚未闭合 8 项。隔离场景或结构流程通过不能替代对应真实错误的修复与端到端证明。**

| 错误 | 原分析 | 闭合状态 | 独立证明 |
|---|---|---|---|
| v2_0_missing_previous_trading_day_anchor | 6601a466-eecc-48f0-82e5-68785d8ac5be | 未闭合 | [证据](repair_2026-10-20/independent_live_anchor_review_v2_0.json) |
| v2_1_future_abandonment_asserted_completed | b1e9c166-8851-4f7c-9803-b9c72329f6a9 | 未闭合 | [证据](repair_2026-10-20/independent_live_prose_review_v2_1_initial.json) |
| v2_1_field_misidentified_as_pipeline | d2ef1250-6ed3-4a68-9396-e6ed4addbfb3 | 未闭合 | [证据](repair_2026-10-20/independent_live_prose_review_v2_1_pre_fact_gate.json) |
| v2_2_deterministic_source_coverage_false_warning | 9fa53da2-74ec-4fab-9443-bd501f3ba59e | 未闭合 | [证据](repair_2026-10-20/live_9fa_coverage_false_warning_review.json) |
| v2_3_isolated_price_observation_or_baseline_errors | weak_exploration_no_quote, weak_news_observed_market_up, supply_restore_40_with_flat_baseline, anchor_legal_prevday_weak_market_up, future_planned_notice, supply_loss_40_with_flat_baseline, anchor_real_manual_missing_prevday, dismissed_notice_not_verified_recovery | 未闭合 | [证据](repair_2026-10-20/initial_cohort_semantic_defects_v2_3.json) |
| v2_3_isolated_epcm_award_roles_reversed | anchor_real_manual_missing_prevday | 未闭合 | [证据](repair_2026-10-20/initial_cohort_semantic_defects_v2_3.json) |
| v2_3_supply_risk_object_scope_and_evidence_polarity | dismissed_notice_not_verified_recovery, real_eni | 未闭合 | [证据](repair_2026-10-20/independent_source_feedback_dismissed_supply_polarity_observation_v2_3.json) |
| v2_3_ttf_price_energy_unit_changed_to_gas_volume | 341746e5-d870-42ad-8034-97eba9f389d2 | 未闭合 | [证据](repair_2026-10-20/independent_production_fifth_current_live_and_english_review_v2_3.json) |

## 原记录保全

- 保全检查：通过；原有 116 条分析、229 个非空输入/简报/详析字段逐字节 SHA-256 比对。
- 原有 4 个冻结结算快照：逐项不变；允许新增记录，目前新增分析 53 条、冻结快照 1 个。
- 原验收基线：metric 210，2026-10-14T05:51:48.181167+00:00；保持不变。
- 原数据库备份与原源码归档 SHA-256：全部一致。

## 测试与隔离对照

- 单元测试：1898 项，失败 0，错误 0，跳过 0；状态 `passed`。
- 必需候选场景 15 项，已有 15 项，当前方向/状态契约、逻辑及生产策略兼容性通过 12 项。
- 原版对照已有 13／13 项；新增候选专用控制不扩增原版必需场景。后续最终验证覆盖 1 项；早期失败试跑仍保留在各阶段统计。

这15项所选回答的实际生成版本分布为 `{"ttf-news-standalone-2.3": 15}`。本报告按 `ttf-news-standalone-2.3` 源码离线复检，并保留原生成版本、实际请求和SHA。仅当新阶段的实际请求与源码全部匹配，才计入下方当前版本二十项新实测；旧阶段另列历史。

运行失败或当前语义／契约未通过时，原始 raw 方向只用于调试，不是产品发布预测，不计入有效方向分布；表格明确标注无有效输出。独立全文复核由AI agent执行，失败不算正确预测。

| 场景 | 原版新闻方向 | 候选新闻方向 | 候选收盘方向／状态 | 当前逻辑、契约与策略 | 调用／重试 | 耗时秒 |
|---|---|---|---|---|---|---|
| [欧洲实际供气减少40](repair_2026-10-20/candidate-bounded-output-validation/supply_loss_40.json) | up | up | None / insufficient_evidence | 通过 | 1 / 0 | 49.088 |
| [欧洲实际供气恢复增加40](repair_2026-10-20/candidate-bounded-output-validation/supply_restore_40.json) | up | down | None / insufficient_evidence | 通过 | 2 / 1 | 132.283 |
| [弱勘探新闻／无报价](repair_2026-10-20/candidate-evidence-slot-validation/weak_exploration_no_quote.json) | flat | flat | None / insufficient_evidence | 通过 | 2 / 1 | 46.607 |
| [温和天气降低采暖需求](repair_2026-10-20/candidate-bounded-output-validation/warm_heating_demand.json) | flat | down | None / insufficient_evidence | 通过 | 1 / 0 | 89.282 |
| [寒冷天气增加采暖需求](repair_2026-10-20/candidate-final-output-scope-validation/cold_heating_demand.json) | flat | up | None / insufficient_evidence | 通过 | 1 / 0 | 63.283 |
| [否定停产消息](repair_2026-10-20/candidate-bounded-output-validation/negated_outage.json) | flat | flat | None / insufficient_evidence | 通过 | 1 / 0 | 125.576 |
| [累计冲击后新增弱新闻](repair_2026-10-20/candidate-bounded-output-validation/cumulative_shock_then_weak.json) | down | up | None / insufficient_evidence | 通过 | 1 / 0 | 49.937 |
| [新闻弱／已观察行情上涨](repair_2026-10-20/candidate-source-feedback-validation/weak_news_observed_market_up.json) | flat | 无有效输出 | 无有效输出 | 需处理 | 2 / 1 | — |
| [输入明确供需冲突](repair_2026-10-20/candidate-bounded-output-validation/supplied_conflicting_mechanisms.json) | flat | down | None / insufficient_evidence | 通过 | 1 / 0 | 54.166 |
| [未来计划维护](repair_2026-10-20/candidate-price-zero-consistency-validation/future_planned_notice.json) | flat | flat | None / insufficient_evidence | 通过 | 2 / 1 | 60.761 |
| [撤销公告／流量恢复未核实](repair_2026-10-20/candidate-risk-polarity-validation/dismissed_notice_not_verified_recovery.json) | flat | flat | None / insufficient_evidence | 通过 | 2 / 1 | 164.193 |
| [真实Nyhamna公告](repair_2026-10-20/candidate-physical-unit-validation/real_nyhamna.json) | flat | up | up / forecast | 通过 | 1 / 0 | 58.064 |
| [真实Eni新闻](repair_2026-10-20/candidate-pressure-predicate-validation/real_eni.json) | flat | 无有效输出（raw: flat） | 无有效输出（raw: flat / forecast） | 需处理 | 2 / 1 | — |
| [实际供气减少40／参考基准持平](repair_2026-10-20/candidate-source-feedback-validation/supply_loss_40_with_flat_baseline.json) | 待运行 | 无有效输出（raw: up） | 无有效输出 | 需处理 | 2 / 1 | — |
| [实际供气恢复40／参考基准持平](repair_2026-10-20/candidate-source-feedback-validation/supply_restore_40_with_flat_baseline.json) | 待运行 | down | down / forecast | 通过 | 2 / 1 | 180.715 |

## 独立价格锚点真实模型对照

必需 4 项，已有 4 项，当前逻辑与契约通过 3 项；确认由当前模型输入相关源码实际调用模型 4 项。该四项与上表十五项机制分别统计；新阶段若运行，则一并归入独立二十项 cohort。

缺上一交易日锚点时保留绝对报价和新闻供需方向；不拿旧日期价差当昨收，不用flat填未知。有源方向新闻允许有依据的条件预测或明确缺锚点，无需强行复制新闻方向。

| 场景 | 实际生成版本 | 新闻方向 | 收盘方向／状态 | 当前检查 | 调用／重试 |
|---|---|---|---|---|---|
| [真实手动新闻／缺上一交易日价格锚点](repair_2026-10-20/candidate-evidence-slot-validation/anchor_real_manual_missing_prevday.json) | ttf-news-standalone-2.3 | 无有效输出（raw: flat） | 无有效输出 | 需处理 | 2 / 1 |
| [缺价格锚点／当期实际供气减少40](repair_2026-10-20/candidate-physical-unit-validation/anchor_gap_supply_loss_40.json) | ttf-news-standalone-2.3 | up | None / insufficient_evidence | 通过 | 1 / 0 |
| [缺价格锚点／当期实际供气恢复40](repair_2026-10-20/candidate-physical-unit-validation/anchor_gap_supply_restore_40.json) | ttf-news-standalone-2.3 | down | None / insufficient_evidence | 通过 | 1 / 0 |
| [合法前交易日锚点／弱新闻与上涨基准](repair_2026-10-20/candidate-price-zero-consistency-validation/anchor_legal_prevday_weak_market_up.json) | ttf-news-standalone-2.3 | flat | up / forecast | 通过 | 2 / 1 |

## 当前版本二十项全新实际回归

必需 20 项，新阶段实际文件 20 项，严格契约通过 15 项，可交付但真实标签未验证的分歧 0 项，无可交付输出 5 项；当前输入与真实请求匹配 20 项，最后源码直接执行 0 项，同版本实际样本经最终完整路径等价验证 20 项，完整详析（直接执行或完整等价）通过 1 条。

本次同版本二十项实际样本：原十五项机制、四项价格锚点与一项真实井源。最后源码直接执行与原实际执行后完整路径等价分别列数；后者不是最后源码新模型调用。受影响案例须后续已注册阶段的新实际终态，最新失败不回退；全部初次产物另行保留。不是行情准确率。

最终当前样本仅由完整 bounded-output 二十项、final-output-scope、source-feedback、risk-polarity、physical-unit、evidence-slot、price-zero-consistency 与最后 pressure-predicate 的受影响案例新实际终态依次合并；后续失败或未完不回退旧成功。缺项保持 missing，semantic-regeneration、price-observation、final-tense 与 detail-grounding 不补位，全部旧记录仍保留在各阶段中。阶段注册不代表已执行。本轮 pressure-predicate 仅 real_eni 因第二轮最终诊断与终态错误文案变化需新实际终态；其余20条（19简报与1详析）完整路径等价，保留原生成来源，不称新生成。校验源或纠错请求变化后的未受影响样本必须有逐轮完整请求／校验／草稿／终稿显式等价，复验不算新模型调用。原真实EPCM窄范围标签分歧仅适用已固定旧record的SHA与两份AI agent独立全文复核，不豁免任何无源价格结果句；新结果不自动豁免原flat契约。失败 raw 不是有效预测，行情准确率仍未计算。

| 场景 | 实际生成版本 | 新闻方向 | 收盘方向／状态 | 当前检查 | 调用／重试 |
|---|---|---|---|---|---|
| [欧洲实际供气减少40](repair_2026-10-20/candidate-bounded-output-validation/supply_loss_40.json) | ttf-news-standalone-2.3 | up | None / insufficient_evidence | 通过 | 1 / 0 |
| [欧洲实际供气恢复增加40](repair_2026-10-20/candidate-bounded-output-validation/supply_restore_40.json) | ttf-news-standalone-2.3 | down | None / insufficient_evidence | 通过 | 2 / 1 |
| [弱勘探新闻／无报价](repair_2026-10-20/candidate-evidence-slot-validation/weak_exploration_no_quote.json) | ttf-news-standalone-2.3 | flat | None / insufficient_evidence | 通过 | 2 / 1 |
| [温和天气降低采暖需求](repair_2026-10-20/candidate-bounded-output-validation/warm_heating_demand.json) | ttf-news-standalone-2.3 | down | None / insufficient_evidence | 通过 | 1 / 0 |
| [寒冷天气增加采暖需求](repair_2026-10-20/candidate-final-output-scope-validation/cold_heating_demand.json) | ttf-news-standalone-2.3 | up | None / insufficient_evidence | 通过 | 1 / 0 |
| [否定停产消息](repair_2026-10-20/candidate-bounded-output-validation/negated_outage.json) | ttf-news-standalone-2.3 | flat | None / insufficient_evidence | 通过 | 1 / 0 |
| [累计冲击后新增弱新闻](repair_2026-10-20/candidate-bounded-output-validation/cumulative_shock_then_weak.json) | ttf-news-standalone-2.3 | up | None / insufficient_evidence | 通过 | 1 / 0 |
| [新闻弱／已观察行情上涨](repair_2026-10-20/candidate-source-feedback-validation/weak_news_observed_market_up.json) | ttf-news-standalone-2.3 | 无有效输出 | 无有效输出 | 需处理 | 2 / 1 |
| [输入明确供需冲突](repair_2026-10-20/candidate-bounded-output-validation/supplied_conflicting_mechanisms.json) | ttf-news-standalone-2.3 | down | None / insufficient_evidence | 通过 | 1 / 0 |
| [未来计划维护](repair_2026-10-20/candidate-price-zero-consistency-validation/future_planned_notice.json) | ttf-news-standalone-2.3 | flat | None / insufficient_evidence | 通过 | 2 / 1 |
| [撤销公告／流量恢复未核实](repair_2026-10-20/candidate-risk-polarity-validation/dismissed_notice_not_verified_recovery.json) | ttf-news-standalone-2.3 | flat | None / insufficient_evidence | 通过 | 2 / 1 |
| [真实Nyhamna公告](repair_2026-10-20/candidate-physical-unit-validation/real_nyhamna.json) | ttf-news-standalone-2.3 | up | up / forecast | 通过 | 1 / 0 |
| [真实Eni新闻](repair_2026-10-20/candidate-pressure-predicate-validation/real_eni.json) | ttf-news-standalone-2.3 | 无有效输出（raw: flat） | 无有效输出（raw: flat / forecast） | 需处理 | 2 / 1 |
| [实际供气减少40／参考基准持平](repair_2026-10-20/candidate-source-feedback-validation/supply_loss_40_with_flat_baseline.json) | ttf-news-standalone-2.3 | 无有效输出（raw: up） | 无有效输出 | 需处理 | 2 / 1 |
| [实际供气恢复40／参考基准持平](repair_2026-10-20/candidate-source-feedback-validation/supply_restore_40_with_flat_baseline.json) | ttf-news-standalone-2.3 | down | down / forecast | 通过 | 2 / 1 |
| [真实井源／将封堵弃置的事实时态](repair_2026-10-20/candidate-evidence-slot-validation/real_future_well_b1.json) | ttf-news-standalone-2.3 | 无有效输出（raw: flat） | 无有效输出 | 需处理 | 2 / 1 |
| [真实手动新闻／缺上一交易日价格锚点](repair_2026-10-20/candidate-evidence-slot-validation/anchor_real_manual_missing_prevday.json) | ttf-news-standalone-2.3 | 无有效输出（raw: flat） | 无有效输出 | 需处理 | 2 / 1 |
| [缺价格锚点／当期实际供气减少40](repair_2026-10-20/candidate-physical-unit-validation/anchor_gap_supply_loss_40.json) | ttf-news-standalone-2.3 | up | None / insufficient_evidence | 通过 | 1 / 0 |
| [缺价格锚点／当期实际供气恢复40](repair_2026-10-20/candidate-physical-unit-validation/anchor_gap_supply_restore_40.json) | ttf-news-standalone-2.3 | down | None / insufficient_evidence | 通过 | 1 / 0 |
| [合法前交易日锚点／弱新闻与上涨基准](repair_2026-10-20/candidate-price-zero-consistency-validation/anchor_legal_prevday_weak_market_up.json) | ttf-news-standalone-2.3 | flat | up / forecast | 通过 | 2 / 1 |

## 保留的旧版本实际记录

以下为新 semantic-regeneration 阶段之前的选择。原始运行终态与当前离线检查分别记录；旧2.0／2.1结果不是新版本性能。完整记录文件与各阶段失败保留。

| 历史 cohort | 原实际有效输出／必需 | 实际生成版本 |
|---|---|---|
| previous_fifteen | 13 / 15 | `{"ttf-news-standalone-2.0": 15}` |
| previous_four_anchors | 4 / 4 | `{"ttf-news-standalone-2.1": 4}` |

### 显式评估合同修订

[evaluation_contract_revision_v1](repair_2026-10-20/evaluation_contract_revision_v1.json) 仅对两项新增持平基准控制对称修订：新闻供需方向仍严格；收盘须为有依据的条件 forecast，可同向或处于 ±0.30 欧元/MWh 的 flat 带。40 mcm/d 没有给出价格弹性，不能据此要求必然跨过该价格阈值。原 fixture SHA、原始回答及 recorded_contract_passed 全部保留；这不是实际准确率或幅度验证。

| 控制场景 | 原记录契约 | 原 fixture 复检 | 修订后契约 | 当前语义复检 |
|---|---|---|---|---|
| 实际供气减少40／参考基准持平 | 未通过 | 未通过 | 未通过 | 未通过 |
| 实际供气恢复40／参考基准持平 | 通过 | 通过 | 通过 | 通过 |

其中弱相关新闻仍可判 flat；供气恢复应呈下行机制；缺少收盘证据可明确标记 insufficient_evidence，而非填 flat。冲突、未来维护和真实新闻案例不预先强塞方向，只检查实际证据和逻辑。

## 详细分析验证

- 最终选择 2 条详析，通过 2 条，失败 0 条；至少一条完整详析通过且所选详析均无失败，才满足此项验收。
- 所选详析调用 3 次、重试 1 次；耗时中位数 124.826 秒、P95 208.191 秒。
- 同场景按已注册阶段优先级选择实际存在的最新详析（当前最高为 `candidate-pressure-predicate-validation-detail`）；失败不回退，尚无记录的阶段不计为完成。简报的 15 个场景独立统计。旧详析失败仍保留在下表。

| 详析场景 | 选择阶段 | 当前契约与逻辑 | 调用／重试 | 耗时秒 |
|---|---|---|---|---|
| [欧洲实际供气恢复增加40](repair_2026-10-20/candidate-binding-validation-detail/supply_restore_40.json) | candidate-binding-validation-detail | 通过 | 1 / 0 | 41.46 |
| [实际供气恢复40／参考基准持平](repair_2026-10-20/candidate-risk-polarity-validation-detail/supply_restore_40_with_flat_baseline.json) | candidate-risk-polarity-validation-detail | 通过 | 2 / 1 | 208.191 |

## 各阶段完整记录

| 阶段 | 记录数 | 当前契约＋逻辑＋策略通过 | 原始运行失败 | 当前逻辑拒绝 | 策略不兼容 | 调用／重试 | 中位／P95秒 |
|---|---|---|---|---|---|---|---|
| candidate-pilot | 4 | 0 | 4 | 4 | 2 | 6 / 2 | 36.672 / 74.675 |
| candidate-required-status | 4 | 2 | 0 | 1 | 1 | 5 / 1 | 21.669 / 44.208 |
| candidate-mixed-pilot | 1 | 0 | 1 | 1 | 0 | 2 / 1 | 169.806 / 169.806 |
| candidate-comparison | 11 | 6 | 2 | 2 | 5 | 16 / 5 | 98.82 / 174.945 |
| original-comparison | 13 | 0 | 1 | 13 | 0 | 13 / 0 | 9.423 / 50.447 |
| candidate-finalvalidation | 0 | 0 | 0 | 0 | 0 | 0 / 0 | None / None |
| candidate-final-validation | 13 | 6 | 2 | 6 | 4 | 17 / 4 | 34.769 / 111.051 |
| candidate-release-validation | 4 | 1 | 0 | 3 | 2 | 6 / 2 | 66.797 / 98.283 |
| candidate-production-validation | 13 | 9 | 2 | 2 | 3 | 16 / 3 | 40.363 / 122.277 |
| candidate-verified-validation | 2 | 0 | 2 | 2 | 2 | 4 / 2 | 135.531 / 150.676 |
| candidate-grounded-validation | 2 | 0 | 2 | 2 | 0 | 4 / 2 | 152.87 / 152.947 |
| candidate-decoder-validation | 15 | 10 | 1 | 5 | 1 | 16 / 1 | 26.621 / 81.254 |
| candidate-factchecks-validation | 5 | 0 | 1 | 5 | 2 | 7 / 2 | 80.787 / 175.73 |
| candidate-integrity-validation | 4 | 0 | 3 | 4 | 4 | 8 / 4 | 127.362 / 162.442 |
| candidate-parser-validation | 2 | 0 | 0 | 2 | 2 | 4 / 2 | 172.369 / 213.176 |
| candidate-adaptive-validation | 2 | 0 | 2 | 2 | 0 | 4 / 2 | 215.526 / 218.393 |
| candidate-budget-validation | 10 | 5 | 1 | 2 | 4 | 14 / 4 | 67.959 / 403.11 |
| candidate-scoped-validation | 4 | 3 | 1 | 1 | 1 | 5 / 1 | 54.48 / 114.183 |
| candidate-symmetric-validation | 15 | 9 | 1 | 5 | 3 | 19 / 4 | 61.88 / 261.716 |
| candidate-scopeguard-validation | 5 | 1 | 2 | 4 | 2 | 7 / 2 | 91.252 / 356.716 |
| candidate-feedback-validation | 1 | 0 | 1 | 1 | 1 | 2 / 1 | 143.579 / 143.579 |
| candidate-contract-validation | 5 | 1 | 1 | 2 | 3 | 8 / 3 | 141.909 / 180.744 |
| candidate-binding-validation | 2 | 0 | 1 | 2 | 1 | 3 / 1 | 140.906 / 206.351 |
| candidate-source-validation | 1 | 0 | 1 | 0 | 1 | 2 / 1 | 207.032 / 207.032 |
| candidate-provenance-validation | 1 | 0 | 1 | 1 | 1 | 2 / 1 | 312.294 / 312.294 |
| candidate-revision-validation | 4 | 0 | 1 | 2 | 4 | 8 / 4 | 169.0 / 202.215 |
| candidate-fieldfeedback-validation | 2 | 0 | 1 | 2 | 1 | 3 / 1 | 120.389 / 209.5 |
| candidate-reaction-validation | 1 | 0 | 1 | 1 | 1 | 2 / 1 | 194.542 / 194.542 |
| candidate-anchor-validation | 4 | 2 | 1 | 2 | 1 | 5 / 1 | 36.072 / 124.65 |
| candidate-reviewwording-validation | 1 | 0 | 0 | 1 | 0 | 1 / 0 | 24.508 / 24.508 |
| candidate-semantic-regeneration-validation | 20 | 9 | 4 | 10 | 4 | 27 / 7 | 46.773 / 88.255 |
| candidate-price-observation-validation | 7 | 0 | 3 | 4 | 6 | 13 / 6 | 139.751 / 280.579 |
| candidate-final-tense-validation | 1 | 1 | 0 | 0 | 0 | 1 / 0 | 105.412 / 105.412 |
| candidate-bounded-output-validation | 20 | 8 | 7 | 10 | 7 | 30 / 10 | 70.338 / 147.985 |
| candidate-final-output-scope-validation | 8 | 3 | 3 | 5 | 3 | 11 / 3 | 90.745 / 266.794 |
| candidate-source-feedback-validation | 8 | 2 | 4 | 6 | 1 | 15 / 7 | 173.224 / 233.159 |
| candidate-risk-polarity-validation | 2 | 2 | 0 | 0 | 0 | 4 / 2 | 184.174 / 204.155 |
| candidate-physical-unit-validation | 6 | 3 | 1 | 3 | 0 | 7 / 1 | 44.598 / 70.9 |
| candidate-evidence-slot-validation | 4 | 1 | 3 | 3 | 0 | 8 / 4 | 68.808 / 129.977 |
| candidate-price-zero-consistency-validation | 3 | 2 | 1 | 1 | 0 | 6 / 3 | 64.511 / 126.077 |
| candidate-pressure-predicate-validation | 1 | 0 | 1 | 1 | 0 | 2 / 1 | 140.042 / 140.042 |
| candidate-final-validation-detail | 1 | 0 | 1 | 1 | 1 | 2 / 1 | 79.875 / 79.875 |
| candidate-release-validation-detail | 0 | 0 | 0 | 0 | 0 | 0 / 0 | None / None |
| candidate-production-validation-detail | 0 | 0 | 0 | 0 | 0 | 0 / 0 | None / None |
| candidate-verified-validation-detail | 0 | 0 | 0 | 0 | 0 | 0 / 0 | None / None |
| candidate-grounded-validation-detail | 1 | 0 | 1 | 1 | 0 | 2 / 1 | 131.11 / 131.11 |
| candidate-decoder-validation-detail | 1 | 0 | 1 | 1 | 1 | 2 / 1 | 163.153 / 163.153 |
| candidate-factchecks-validation-detail | 1 | 0 | 1 | 1 | 1 | 2 / 1 | 151.528 / 151.528 |
| candidate-integrity-validation-detail | 0 | 0 | 0 | 0 | 0 | 0 / 0 | None / None |
| candidate-parser-validation-detail | 1 | 0 | 0 | 1 | 1 | 2 / 1 | 141.003 / 141.003 |
| candidate-adaptive-validation-detail | 1 | 0 | 1 | 1 | 0 | 2 / 1 | 310.534 / 310.534 |
| candidate-budget-validation-detail | 0 | 0 | 0 | 0 | 0 | 0 / 0 | None / None |
| candidate-scoped-validation-detail | 0 | 0 | 0 | 0 | 0 | 0 / 0 | None / None |
| candidate-symmetric-validation-detail | 1 | 0 | 1 | 1 | 1 | 2 / 1 | 149.915 / 149.915 |
| candidate-scopeguard-validation-detail | 1 | 0 | 1 | 1 | 1 | 2 / 1 | 188.88 / 188.88 |
| candidate-feedback-validation-detail | 0 | 0 | 0 | 0 | 0 | 0 / 0 | None / None |
| candidate-contract-validation-detail | 1 | 0 | 0 | 1 | 1 | 2 / 1 | 173.421 / 173.421 |
| candidate-binding-validation-detail | 1 | 1 | 0 | 0 | 0 | 1 / 0 | 41.46 / 41.46 |
| candidate-source-validation-detail | 0 | 0 | 0 | 0 | 0 | 0 / 0 | None / None |
| candidate-provenance-validation-detail | 0 | 0 | 0 | 0 | 0 | 0 / 0 | None / None |
| candidate-revision-validation-detail | 0 | 0 | 0 | 0 | 0 | 0 / 0 | None / None |
| candidate-fieldfeedback-validation-detail | 0 | 0 | 0 | 0 | 0 | 0 / 0 | None / None |
| candidate-reaction-validation-detail | 0 | 0 | 0 | 0 | 0 | 0 / 0 | None / None |
| candidate-anchor-validation-detail | 0 | 0 | 0 | 0 | 0 | 0 / 0 | None / None |
| candidate-reviewwording-validation-detail | 0 | 0 | 0 | 0 | 0 | 0 / 0 | None / None |
| candidate-semantic-regeneration-validation-detail | 1 | 0 | 1 | 1 | 0 | 2 / 1 | 120.944 / 120.944 |
| candidate-price-observation-validation-detail | 1 | 0 | 1 | 1 | 0 | 2 / 1 | 165.21 / 165.21 |
| candidate-final-tense-validation-detail | 0 | 0 | 0 | 0 | 0 | 0 / 0 | None / None |
| candidate-detail-grounding-validation-detail | 0 | 0 | 0 | 0 | 0 | 0 / 0 | None / None |
| candidate-bounded-output-validation-detail | 0 | 0 | 0 | 0 | 0 | 0 / 0 | None / None |
| candidate-final-output-scope-validation-detail | 0 | 0 | 0 | 0 | 0 | 0 / 0 | None / None |
| candidate-source-feedback-validation-detail | 0 | 0 | 0 | 0 | 0 | 0 / 0 | None / None |
| candidate-risk-polarity-validation-detail | 1 | 1 | 0 | 0 | 0 | 2 / 1 | 208.191 / 208.191 |
| candidate-physical-unit-validation-detail | 0 | 0 | 0 | 0 | 0 | 0 / 0 | None / None |
| candidate-evidence-slot-validation-detail | 0 | 0 | 0 | 0 | 0 | 0 / 0 | None / None |
| candidate-price-zero-consistency-validation-detail | 0 | 0 | 0 | 0 | 0 | 0 / 0 | None / None |
| candidate-pressure-predicate-validation-detail | 0 | 0 | 0 | 0 | 0 | 0 / 0 | None / None |

现存推理结果已按当前代码重新校验；生成时源码SHA差异逐项保留，不能视为所有案例均由同一次冻结版本生成。生产默认禁用独立high重生，采用两次medium及明确诊断反馈。未实际走独立high路径的旧结果可兼容，即使旧配置曾开启high；显式开启的独立high实验保留，但即使语义通过也须按生产默认重新实测，不计生产通过。

## 剩余事项

- 当前逻辑／方向契约／生产策略兼容性未通过：real_eni, supply_loss_40_with_flat_baseline, weak_news_observed_market_up
- 真实运行中已接受错误尚未闭合：v2_0_missing_previous_trading_day_anchor, v2_1_future_abandonment_asserted_completed, v2_1_field_misidentified_as_pipeline, v2_2_deterministic_source_coverage_false_warning, v2_3_isolated_price_observation_or_baseline_errors, v2_3_isolated_epcm_award_roles_reversed, v2_3_supply_risk_object_scope_and_evidence_polarity, v2_3_ttf_price_energy_unit_changed_to_gas_volume
- 独立价格锚点四项当前版本真实模型验证尚未全部通过；不由旧15项离线复检替代
- 当前版本二十项新实际生成及详析尚未全部通过；不回退旧阶段或把旧版本离线复验当新执行

## 部署与可复查材料

部署记录状态：`backend_updated_pages_pending`。没有部署记录时，本报告不会宣称网站已更新。

- [完整机器可读报告](logic_repair_validation_2026-10-20.json)
- [原记录保全清单](repair_2026-10-20/preservation_manifest.json)
- [单元测试记录](repair_2026-10-20/operator-prose-final-unit-tests-rerun.xml)

源码 SHA-256、每个场景的当前校验失败原因、输出状态、重试与时延均在 JSON 中；原始试跑不覆盖、不删改。
