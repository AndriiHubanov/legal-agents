"""
FastAPI сервер для Legal Agents Web UI.

Запуск: python main.py server
  або:  uvicorn server:app --host 127.0.0.1 --port 8000

Маршрути:
  GET  /                        — веб-інтерфейс (process.html)
  POST /api/run                 — запустити пайплайн
  GET  /api/logs/{session_id}   — SSE-потік логів пайплайну
  GET  /api/status/{session_id} — стан сесії (polling)
  GET  /api/download/{session_id} — завантажити .docx
"""
import logging
import queue
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from shared.file_processor import extract_text_from_bytes

app = FastAPI(title="Legal Agents Web UI", version="2.0.0")

# ---------------------------------------------------------------------------
# Сховище сесій (в пам'яті, для одного користувача)
# ---------------------------------------------------------------------------

@dataclass
class SessionInfo:
    session_id: str
    status: str = "running"          # running / completed / failed
    log_queue: queue.Queue = field(default_factory=queue.Queue)
    final_docx_path: Optional[str] = None
    error_message: Optional[str] = None


_sessions: dict[str, SessionInfo] = {}


# ---------------------------------------------------------------------------
# Захоплення логів пайплайну
# ---------------------------------------------------------------------------

class _QueueLogHandler(logging.Handler):
    """Пересилає повідомлення логера у чергу SSE."""

    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.q.put(msg)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Фоновий запуск пайплайну
# ---------------------------------------------------------------------------

def _run_pipeline_thread(
    session: SessionInfo,
    situation: str,
    case_parties: dict,
    max_iterations: int,
    run_analysis: bool,
    supporting_docs: list[str],
) -> None:
    """Запускає пайплайн у фоновому потоці, захоплює логи у чергу."""
    handler = _QueueLogHandler(session.log_queue)
    handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    try:
        from orchestrator.pipeline_v2 import run_pipeline
        from orchestrator.state import create_state

        state = create_state(
            raw_situation=situation,
            case_parties=case_parties,
            max_iterations=max_iterations,
            supporting_docs=supporting_docs,
        )

        final_state = run_pipeline(state, run_analysis=run_analysis)

        session.status = final_state.status
        session.final_docx_path = final_state.final_docx_path
        session.error_message = final_state.error_message

    except Exception as exc:
        session.status = "failed"
        session.error_message = str(exc)
        session.log_queue.put(f"[ERROR] {exc}")

    finally:
        root_logger.removeHandler(handler)
        session.log_queue.put(None)  # sentinel — сигнал завершення SSE


# ---------------------------------------------------------------------------
# Маршрути
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def serve_ui() -> HTMLResponse:
    """Повертає process.html як головний інтерфейс."""
    html_path = Path(__file__).parent / "process.html"
    if not html_path.exists():
        raise HTTPException(500, "process.html не знайдено поруч із server.py")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.post("/api/run")
async def start_pipeline(
    situation: str = Form(...),
    plaintiff: str = Form(default=""),
    plaintiff_details: str = Form(default=""),
    defendant: str = Form(default=""),
    defendant_details: str = Form(default=""),
    court: str = Form(default=""),
    lawyer: str = Form(default=""),
    case_number: str = Form(default=""),
    max_iterations: int = Form(default=2),
    run_analysis: bool = Form(default=False),
    files: list[UploadFile] = File(default=[]),
) -> dict:
    """Отримує форму, обробляє файли та запускає пайплайн у фоні."""
    if not situation.strip():
        raise HTTPException(422, "Текст ситуації не може бути порожнім")

    # Витягуємо текст із завантажених файлів
    supporting_docs: list[str] = []
    for f in files:
        if not f.filename:
            continue
        content = await f.read()
        if not content:
            continue
        try:
            text = extract_text_from_bytes(f.filename, content)
            if text:
                supporting_docs.append(f"[{f.filename}]\n{text}")
        except ValueError as exc:
            # Непідтримуваний тип — пропускаємо, але повідомляємо агенту
            supporting_docs.append(f"[{f.filename}] — файл не вдалося прочитати: {exc}")

    case_parties = {
        "plaintiff": plaintiff,
        "plaintiff_details": plaintiff_details or None,
        "defendant": defendant,
        "defendant_details": defendant_details or None,
        "court": court,
        "lawyer": lawyer or None,
    }

    session_id = str(uuid.uuid4())[:8]
    session = SessionInfo(session_id=session_id)
    _sessions[session_id] = session

    thread = threading.Thread(
        target=_run_pipeline_thread,
        args=(session, situation.strip(), case_parties, max_iterations, run_analysis, supporting_docs),
        daemon=True,
    )
    thread.start()

    return {"session_id": session_id}


@app.get("/api/logs/{session_id}")
async def stream_logs(session_id: str) -> StreamingResponse:
    """SSE-потік логів пайплайну для конкретної сесії."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Сесію не знайдено")

    def generate():
        while True:
            try:
                line = session.log_queue.get(timeout=30)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue

            if line is None:
                yield "data: [DONE]\n\n"
                break

            safe = line.replace("\n", " ").strip()
            if safe:
                yield f"data: {safe}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/status/{session_id}")
async def get_status(session_id: str) -> dict:
    """Повертає поточний стан сесії."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Сесію не знайдено")
    return {
        "session_id": session_id,
        "status": session.status,
        "final_docx_path": session.final_docx_path,
        "error_message": session.error_message,
    }


@app.get("/api/download/{session_id}")
async def download_docx(session_id: str) -> FileResponse:
    """Повертає згенерований .docx файл для завантаження."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Сесію не знайдено")
    if session.status != "completed" or not session.final_docx_path:
        raise HTTPException(400, "Документ ще не готовий")
    path = Path(session.final_docx_path)
    if not path.exists():
        raise HTTPException(404, "Файл .docx не знайдено на диску")
    return FileResponse(
        path=str(path),
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
