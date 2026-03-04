"""
Управління станом пайплайну між агентами.
PipelineState зберігає повний контекст між усіма ітераціями.
"""
import json
import uuid
from pathlib import Path

from shared.config import settings
from shared.logger import get_logger
from shared.models import PipelineState

logger = get_logger(__name__)


def create_state(
    raw_situation: str,
    case_parties: dict,
    case_number: str = "",
    doc_type_hint: str = "appeal",
    max_iterations: int = 3,
    supporting_docs: list[str] | None = None,
) -> PipelineState:
    """Створює початковий стан пайплайну."""
    return PipelineState(
        session_id=str(uuid.uuid4())[:8],
        raw_situation=raw_situation,
        case_parties=case_parties,
        case_number=case_number,
        doc_type_hint=doc_type_hint,
        max_iterations=max_iterations,
        status="pending",
        supporting_docs=supporting_docs or [],
    )


def save_state(state: PipelineState) -> str:
    """Зберігає стан у JSON-файл для можливості відновлення."""
    output_dir = Path(settings.OUTPUT_PATH) / "pipeline_states"
    output_dir.mkdir(parents=True, exist_ok=True)

    path = output_dir / f"state_{state.session_id}.json"
    path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    logger.info(f"[State] Збережено: {path}")
    return str(path)


def load_state(session_id: str) -> PipelineState:
    """Завантажує збережений стан за session_id."""
    path = Path(settings.OUTPUT_PATH) / "pipeline_states" / f"state_{session_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return PipelineState.model_validate(data)
