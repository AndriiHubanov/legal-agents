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
    cited_laws: list[str] = Field(default_factory=list)        # ["ст.22 ЦК України", "ст.156 ЗК України"]
    damage_amount: Optional[float] = None                       # сума збитків у грн
    evidence_types: list[str] = Field(default_factory=list)    # типи доказів з рішення
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
    cited_laws: list[str] = Field(default_factory=list)              # зведені норми права
    damage_calculation_method: str = ""                               # методологія розрахунку збитків
    required_evidence: list[str] = Field(default_factory=list)       # перелік необхідних доказів


class DocumentRequest(BaseModel):
    """Запит на генерацію документа"""
    document_type: str  # appeal / cassation / objection / motion
    analysis_report: AnalysisReport
    case_parties: dict  # {plaintiff, defendant, court}
    case_number: str
    deadline: Optional[date] = None
    lawyer_name: Optional[str] = None
    appendices: list[str] = Field(default_factory=list)


# ===========================================================================
# V2 Models — 5-агентна система з feedback loops
# ===========================================================================

class IntakeResult(BaseModel):
    """Результат роботи Агента 1 (Architect) — структурована правова позиція"""
    raw_situation: str
    case_description: CaseDescription
    identified_claims: list[str] = Field(default_factory=list)
    legal_basis_detected: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    case_type: str = "позов"           # позов / апеляція / заперечення / клопотання
    procedural_code: str = "ЦПК"      # ЦПК / КАС / ГПК / КПК
    recommended_doc_type: str = "appeal"
    plaintiff_type: str = "фізична особа"   # фізична особа / юридична особа
    confidence: float = 0.0
    intake_iteration: int = 0


class FeesCalculation(BaseModel):
    """Результат роботи Агента 2 (Fees) — розрахунок судових зборів"""
    claim_type: str                     # майнова / немайнова / змішана
    claim_amount: Optional[float] = None
    plaintiff_type: str = "фізична особа"
    fee_rate_description: str = ""
    fee_amount: float = 0.0
    fee_basis: str = ""                 # наприклад "ст.4 ч.1 п.1 ЗСЗ"
    exemptions_applicable: list[str] = Field(default_factory=list)
    court_jurisdiction: str = ""        # підсудність (суд першої інстанції)
    payment_requisites: str = ""
    notes: list[str] = Field(default_factory=list)


class CriticReview(BaseModel):
    """Результат роботи Агента 3 (Critic) — критична оцінка позиції"""
    status: str = "needs_revision"      # approved / needs_revision / critical_issues
    overall_score: float = 0.0          # 0–10
    objections: list[str] = Field(default_factory=list)
    legal_risks: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    questions_for_intake: list[str] = Field(default_factory=list)
    needs_fee_recalculation: bool = False
    critic_iteration: int = 0


class ComplianceResult(BaseModel):
    """Перевірка документа на відповідність процесуальному кодексу"""
    procedural_code: str = "ЦПК"
    required_elements: dict[str, bool] = Field(default_factory=dict)
    violations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    is_compliant: bool = False
    compliance_score: float = 0.0       # 0–10


class GeneratedDocumentV2(BaseModel):
    """Результат роботи Агента 4 (Generator) — документ + compliance"""
    content: str
    doc_type: str
    compliance: ComplianceResult
    fees_summary: str = ""
    docx_path: Optional[str] = None
    generation_iteration: int = 0


class ExpertReview(BaseModel):
    """Результат роботи Агента 5 (Expert) — фінальний аудит"""
    argumentation_score: float = 0.0    # 0–10
    compliance_score: float = 0.0
    evidence_score: float = 0.0
    persuasiveness_score: float = 0.0
    total_score: float = 0.0
    decision: str = "revise_critic"     # approved / revise_critic / revise_generator
    mandatory_fixes: list[str] = Field(default_factory=list)
    optional_improvements: list[str] = Field(default_factory=list)
    expert_opinion: str = ""
    expert_iteration: int = 0


class PipelineState(BaseModel):
    """Повний стан пайплайну між агентами"""
    session_id: str
    raw_situation: str
    case_parties: dict = Field(default_factory=dict)
    case_number: str = ""
    doc_type_hint: str = "appeal"
    supporting_docs: list[str] = Field(default_factory=list)  # витягнутий текст завантажених файлів

    # Результати кожного агента
    intake_result: Optional[IntakeResult] = None
    fees_calculation: Optional[FeesCalculation] = None
    analysis_report: Optional[AnalysisReport] = None
    critic_reviews: list[CriticReview] = Field(default_factory=list)
    generated_document: Optional[GeneratedDocumentV2] = None
    expert_reviews: list[ExpertReview] = Field(default_factory=list)

    # Управління ітераціями
    current_iteration: int = 0
    max_iterations: int = 3
    status: str = "pending"             # pending / running / completed / failed
    final_docx_path: Optional[str] = None
    error_message: Optional[str] = None
