"""
Обробка завантажених файлів — витяг тексту з PDF, DOCX, TXT.
Використовується сервером для підготовки supporting_docs перед передачею в Agent 1.
"""
import io
from pathlib import Path

MAX_FILE_TEXT_CHARS = 8000  # ~2000 токенів на файл — безпечна межа


def extract_text_from_bytes(filename: str, content: bytes) -> str:
    """
    Витягує текст з байтів файлу.
    Підтримувані формати: .pdf, .docx, .txt, .md
    Обрізає до MAX_FILE_TEXT_CHARS символів.
    """
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        text = _extract_pdf(content)
    elif ext == ".docx":
        text = _extract_docx(content)
    elif ext in (".txt", ".md"):
        text = _extract_txt(content)
    else:
        raise ValueError(f"Непідтримуваний тип файлу: {ext}. Дозволено: .pdf, .docx, .txt")

    text = text.strip()

    if len(text) > MAX_FILE_TEXT_CHARS:
        text = text[:MAX_FILE_TEXT_CHARS] + "\n[... текст документа скорочено ...]"

    return text


def _extract_pdf(content: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    pages = []
    for page in reader.pages:
        extracted = page.extract_text()
        if extracted:
            pages.append(extracted)
    return "\n\n".join(pages)


def _extract_docx(content: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(content))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def _extract_txt(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("cp1251", errors="replace")
