from pydantic import BaseModel, Field
from typing import Optional
from datetime import date


class CourtDecision(BaseModel):
    """Модель судового рішення"""
    id: str
    registry_number: str
    court_name: str
    judge_name: Optional[str] = None
    decision_date: date
    category: str
    subject: str
    result: str
    full_text: str
    legal_positions: list[str] = Field(default_factory=list)
    url: str
    embedding_id: Optional[str] = None


class CaseDescription(BaseModel):
    """Опис справи користувача для пошуку практики"""
    category: str
    subject: str
    key_facts: str
    desired_outcome: str
    court_level: str
    opposing_arguments: Optional[str] = None


class AnalysisReport(BaseModel):
    """Звіт аналізу від Агента 2"""
    case_description: CaseDescription
    relevant_decisions: list[CourtDecision] = Field(default_factory=list)
    legal_arguments: list[str] = Field(default_factory=list)
    counter_arguments: list[str] = Field(default_factory=list)
    recommended_strategy: str = ""
    confidence_score: float = 0.0


class DocumentRequest(BaseModel):
    """Запит на генерацію документа"""
    document_type: str  # appeal / cassation / objection / motion
    analysis_report: AnalysisReport
    case_parties: dict  # {plaintiff, defendant, court}
    case_number: str
    deadline: Optional[date] = None
    lawyer_name: Optional[str] = None
    appendices: list[str] = Field(default_factory=list)
