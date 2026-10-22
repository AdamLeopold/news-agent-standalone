"""Validated, display-ready output contract for the standalone news analyst."""

from typing import Literal, Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


Direction = Literal["extreme_up", "up", "flat", "down", "extreme_down"]
SignalStrength = Literal["weak", "moderate", "strong"]
EvidenceText = Annotated[str, Field(min_length=3,max_length=450)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Reference(StrictModel):
    id: str = Field(min_length=1)
    url: str
    title: str | None = None


class NewsImpact(StrictModel):
    direction: Direction
    contextual_news_score: float = Field(ge=-3, le=3)
    confidence: float = Field(ge=0, le=1)
    signal_strength: SignalStrength
    summary: str = Field(min_length=1,max_length=350)
    bullish_evidence: list[EvidenceText] = Field(max_length=3)
    bearish_evidence: list[EvidenceText] = Field(max_length=3)
    neutralizing_evidence: list[EvidenceText] = Field(max_length=3)

    @model_validator(mode="after")
    def validate_score_direction(self):
        if self.direction in {"up", "extreme_up"} and self.contextual_news_score <= 0:
            raise ValueError("看涨新闻方向必须对应正的 contextual_news_score")
        if self.direction in {"down", "extreme_down"} and self.contextual_news_score >= 0:
            raise ValueError("看跌新闻方向必须对应负的 contextual_news_score")
        if self.direction in {"up", "extreme_up"} and not self.bullish_evidence:
            raise ValueError("看涨结论必须有具体看涨证据；不能只填方向")
        if self.direction in {"down", "extreme_down"} and not self.bearish_evidence:
            raise ValueError("看跌结论必须有具体看跌证据；不能只填方向")
        return self


class CloseForecast(StrictModel):
    direction: Direction | None
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(min_length=1,max_length=350)
    basis: str = Field(min_length=1,max_length=600)
    extreme_move_risk: str | None = Field(default=None,max_length=300)
    evidence_status: Literal["forecast", "insufficient_evidence"] = "forecast"

    @model_validator(mode="after")
    def validate_evidence_status(self):
        if self.evidence_status == "insufficient_evidence" and self.direction is not None:
            raise ValueError("收盘信息不足时direction必须为null，不得填flat")
        if self.evidence_status == "forecast" and self.direction is None:
            raise ValueError("有效收盘预判必须提供五档方向之一")
        return self


class Analysis(StrictModel):
    summary: str = Field(min_length=1,max_length=450)
    evidence: list[EvidenceText] = Field(min_length=1,max_length=3)
    negation_or_reversal: list[EvidenceText] = Field(max_length=3)
    priced_in_comment: str = Field(min_length=1,max_length=350)
    matched_terms: list[str]
    historical_comparisons: list[EvidenceText] = Field(max_length=3)


class Scenarios(StrictModel):
    bullish: str = Field(min_length=12,max_length=600)
    base: str = Field(min_length=12,max_length=600)
    bearish: str = Field(min_length=12,max_length=600)


class DetailAnalysis(StrictModel):
    new_facts: list[str] = Field(min_length=1)
    transmission: list[str] = Field(min_length=1)
    daily_balance: list[str] = Field(min_length=1)
    historical_comparisons: list[str] = Field(min_length=1)
    priced_in_assessment: str = Field(min_length=1)
    scenarios: Scenarios
    invalidation_conditions: list[str] = Field(min_length=1)
    limitations: list[str] = Field(min_length=1)
    references: list[Reference] = Field(min_length=1)


class AnalysisResult(StrictModel):
    news_impact: NewsImpact
    close_forecast: CloseForecast
    analysis: Analysis
    risk_flags: list[str]
    references: list[Reference]
    missing_fields: list[str]
    detail: DetailAnalysis | None = None
    # New generations may copy a source-normalization table; legacy records
    # have no such table. It is metadata, not model-generated price evidence.
    source_fact_checks: dict | None = None


LABELS: dict[str, str] = {
    "extreme_up": "异常暴涨",
    "up": "涨",
    "flat": "基本维持",
    "down": "跌",
    "extreme_down": "异常暴跌",
}
