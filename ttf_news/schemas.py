"""Validated, display-ready output contract for the standalone news analyst."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Direction = Literal["extreme_up", "up", "flat", "down", "extreme_down"]
SignalStrength = Literal["weak", "moderate", "strong"]


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
    summary: str = Field(min_length=1)
    bullish_evidence: list[str]
    bearish_evidence: list[str]
    neutralizing_evidence: list[str]

    @model_validator(mode="after")
    def validate_score_direction(self):
        if self.direction in {"up", "extreme_up"} and self.contextual_news_score <= 0:
            raise ValueError("看涨新闻方向必须对应正的 contextual_news_score")
        if self.direction in {"down", "extreme_down"} and self.contextual_news_score >= 0:
            raise ValueError("看跌新闻方向必须对应负的 contextual_news_score")
        return self


class CloseForecast(StrictModel):
    direction: Direction
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(min_length=1)
    basis: str = Field(min_length=1)
    extreme_move_risk: str | None = None


class Analysis(StrictModel):
    summary: str = Field(min_length=1)
    evidence: list[str]
    negation_or_reversal: list[str]
    priced_in_comment: str = Field(min_length=1)
    matched_terms: list[str]
    historical_comparisons: list[str]


class Scenarios(StrictModel):
    bullish: str = Field(min_length=1)
    base: str = Field(min_length=1)
    bearish: str = Field(min_length=1)


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


LABELS: dict[str, str] = {
    "extreme_up": "异常暴涨",
    "up": "涨",
    "flat": "基本维持",
    "down": "跌",
    "extreme_down": "异常暴跌",
}
