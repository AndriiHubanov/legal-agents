"""
Мультиагентна система аналізу судової практики України
Головний CLI-інтерфейс та пайплайн агентів
"""
import asyncio
import json
import sys
from datetime import date, datetime
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

# Налаштовуємо шлях до модулів проєкту
sys.path.insert(0, str(Path(__file__).parent))

console = Console()


def _load_settings():
    """Завантаження налаштувань з обробкою помилок"""
    from shared.config import settings
    settings.ensure_dirs()
    return settings


# ===========================================================================
# CLI група команд
# ===========================================================================

@click.group()
@click.version_option("1.0.0", prog_name="legal-agents")
def cli():
    """
    \b
    Мультиагентна система аналізу судової практики України
    =========================================================
    Агент 1: Збір рішень з reyestr.court.gov.ua
    Агент 2: Аналіз практики під конкретну справу
    Агент 3: Генерація процесуальних документів
    """


# ===========================================================================
# Команда: collect
# ===========================================================================

@cli.command()
@click.option("--category", "-c", required=True,
              type=click.Choice(["civil", "admin", "commercial", "criminal", "labor"]),
              help="Категорія справ")
@click.option("--date-from", "-df", required=True, type=click.DateTime(formats=["%Y-%m-%d"]),
              help="Дата від (YYYY-MM-DD)")
@click.option("--date-to", "-dt", default=None, type=click.DateTime(formats=["%Y-%m-%d"]),
              help="Дата до (YYYY-MM-DD), за замовчуванням — сьогодні")
@click.option("--keywords", "-k", default=None,
              help="Ключові слова через кому: 'оренда,земля,розірвання'")
@click.option("--court-level", default=None,
              type=click.Choice(["first", "appeal", "cassation"]),
              help="Рівень суду")
@click.option("--region", default=None, help="Регіон (наприклад: 'Київська')")
@click.option("--max", "max_results", default=50, show_default=True,
              help="Максимальна кількість рішень")
@click.option("--no-claude", is_flag=True, default=False,
              help="Не використовувати Claude для витягу правових позицій")
def collect(category, date_from, date_to, keywords, court_level, region, max_results, no_claude):
    """
    Агент 1: Збір судових рішень з reyestr.court.gov.ua

    \b
    Приклад:
      python main.py collect --category civil --date-from 2023-01-01 --keywords "оренда,земля"
    """
    asyncio.run(_collect_async(
        category=category,
        date_from=date_from.date(),
        date_to=(date_to.date() if date_to else date.today()),
        keywords=keywords.split(",") if keywords else None,
        court_level=court_level,
        region=region,
        max_results=max_results,
        use_claude=not no_claude,
    ))


async def _collect_async(
    category: str,
    date_from: date,
    date_to: date,
    keywords: list[str] | None,
    court_level: str | None,
    region: str | None,
    max_results: int,
    use_claude: bool,
):
    from agent1_collector.filters import SearchFilters
    from agent1_collector.scraper import CourtScraper
    from agent1_collector.parser import parse_decision_page, extract_legal_positions, detect_decision_result, normalize_court_name
    from agent1_collector.storage import DecisionStorage
    from shared.models import CourtDecision
    import uuid

    settings = _load_settings()

    filters = SearchFilters(
        category=category,
        date_from=date_from,
        date_to=date_to,
        court_level=court_level,
        region=region,
        keywords=keywords,
        max_results=max_results,
    )

    console.print(Panel(
        f"Категорія: [cyan]{category}[/cyan]\n"
        f"Період: [cyan]{date_from}[/cyan] — [cyan]{date_to}[/cyan]\n"
        f"Ключові слова: [cyan]{keywords or '—'}[/cyan]\n"
        f"Максимум рішень: [cyan]{max_results}[/cyan]",
        title="[bold blue]Агент 1: Збір рішень",
    ))

    storage = DecisionStorage()
    claude_client = None
    if use_claude:
        from shared.claude_client import ClaudeClient
        claude_client = ClaudeClient()

    async with CourtScraper() as scraper:
        raw_decisions = await scraper.scrape_batch(filters)

    console.print(f"\n[green]Зібрано {len(raw_decisions)} сирих рішень[/green]")

    saved_count = 0
    for raw in raw_decisions:
        try:
            parsed = parse_decision_page(raw.get("full_text", ""))
            legal_positions = []
            if claude_client and raw.get("full_text"):
                legal_positions = extract_legal_positions(raw["full_text"], claude_client)

            decision = CourtDecision(
                id=raw.get("id", str(uuid.uuid4())),
                registry_number=parsed.get("registry_number") or raw.get("registry_number", ""),
                court_name=parsed.get("court_name") or "",
                judge_name=parsed.get("judge_name"),
                decision_date=parsed.get("decision_date", date.today()),
                category=category,
                subject=parsed.get("subject") or raw.get("registry_number", ""),
                result=parsed.get("result", "невідомо"),
                full_text=raw.get("full_text", ""),
                legal_positions=legal_positions,
                url=raw.get("url", ""),
            )
            storage.save_decision(decision)
            saved_count += 1
        except Exception as e:
            console.print(f"[red]Помилка обробки {raw.get('id', '?')}: {e}[/red]")

    console.print(f"\n[bold green]Збережено {saved_count} рішень у ChromaDB та JSON[/bold green]")


# ===========================================================================
# Команда: analyze
# ===========================================================================

@cli.command()
@click.option("--case-file", "-f", required=True, type=click.Path(exists=True),
              help="Шлях до JSON файлу з описом справи")
@click.option("--top-k", default=20, show_default=True,
              help="Кількість релевантних рішень для аналізу")
@click.option("--output", "-o", default=None,
              help="Шлях для збереження звіту (за замовчуванням — автоматично)")
def analyze(case_file, top_k, output):
    """
    Агент 2: Аналіз судової практики під конкретну справу

    \b
    Приклад:
      python main.py analyze --case-file case.json
    """
    _analyze_cmd(case_file, top_k, output)


def _analyze_cmd(case_file: str, top_k: int, output: str | None) -> str:
    """Повертає шлях до збереженого звіту"""
    from shared.models import CaseDescription
    from shared.claude_client import ClaudeClient
    from agent1_collector.storage import DecisionStorage
    from agent2_analyst.retriever import PracticeRetriever
    from agent2_analyst.analyzer import PracticeAnalyzer
    from agent2_analyst.report import save_report, print_summary

    settings = _load_settings()

    # Завантажити опис справи
    case_data = json.loads(Path(case_file).read_text(encoding="utf-8"))
    case = CaseDescription.model_validate(case_data)

    console.print(Panel(
        f"Справа: [cyan]{case.subject[:80]}[/cyan]\n"
        f"Категорія: [cyan]{case.category}[/cyan]",
        title="[bold blue]Агент 2: Аналіз практики",
    ))

    storage = DecisionStorage()
    retriever = PracticeRetriever(storage)
    claude_client = ClaudeClient()
    analyzer = PracticeAnalyzer(claude_client)

    # Пошук та аналіз
    relevant = retriever.find_relevant(case, top_k=top_k)
    console.print(f"Знайдено [green]{len(relevant)}[/green] релевантних рішень")

    report = analyzer.analyze(case, relevant)
    print_summary(report)

    # Зберегти
    report_path = save_report(report, filename=output)
    console.print(f"\n[bold]Звіт збережено:[/bold] {report_path}")
    return report_path


# ===========================================================================
# Команда: generate
# ===========================================================================

@cli.command()
@click.option("--analysis-file", "-a", required=True, type=click.Path(exists=True),
              help="Шлях до JSON файлу зі звітом аналізу")
@click.option("--doc-type", "-t", required=True,
              type=click.Choice(["appeal", "cassation", "objection",
                                 "motion_security", "motion_restore_deadline",
                                 "motion_evidence", "motion_expert", "motion_adjournment"]),
              help="Тип документа")
@click.option("--case-number", "-n", required=True, help="Номер справи")
@click.option("--plaintiff", default="", help="Позивач/Апелянт")
@click.option("--defendant", default="", help="Відповідач")
@click.option("--court", default="", help="Назва суду")
@click.option("--lawyer", default="", help="ПІБ адвоката/представника")
@click.option("--output", "-o", default=None, help="Шлях для вихідного .docx файлу")
def generate(analysis_file, doc_type, case_number, plaintiff, defendant, court, lawyer, output):
    """
    Агент 3: Генерація процесуального документа (.docx)

    \b
    Приклад:
      python main.py generate --analysis-file report.json --doc-type appeal --case-number 1-123/2024
    """
    from shared.models import AnalysisReport, DocumentRequest
    from shared.claude_client import ClaudeClient
    from agent3_writer.generator import DocumentGenerator
    from agent3_writer.docx_builder import DocxBuilder

    settings = _load_settings()

    analysis_data = json.loads(Path(analysis_file).read_text(encoding="utf-8"))
    analysis_report = AnalysisReport.model_validate(analysis_data)

    request = DocumentRequest(
        document_type=doc_type,
        analysis_report=analysis_report,
        case_parties={
            "plaintiff": plaintiff,
            "defendant": defendant,
            "court": court,
        },
        case_number=case_number,
        lawyer_name=lawyer or None,
    )

    console.print(Panel(
        f"Тип: [cyan]{doc_type}[/cyan]\n"
        f"Справа: [cyan]{case_number}[/cyan]\n"
        f"Суд: [cyan]{court or '—'}[/cyan]",
        title="[bold blue]Агент 3: Генерація документа",
    ))

    claude_client = ClaudeClient()
    generator = DocumentGenerator(claude_client)
    builder = DocxBuilder()

    console.print("Генерую текст документа через Claude...")
    text = generator.generate(request)

    console.print("Збираю .docx файл...")
    docx_path = builder.build(text, request)

    if output:
        import shutil
        shutil.copy(docx_path, output)
        docx_path = output

    console.print(f"\n[bold green]Документ готовий:[/bold green] {docx_path}")


# ===========================================================================
# Команда: pipeline (повний пайплайн)
# ===========================================================================

@cli.command()
@click.option("--case-file", "-f", required=True, type=click.Path(exists=True),
              help="JSON файл з описом справи")
@click.option("--doc-type", "-t", default="appeal",
              type=click.Choice(["appeal", "cassation", "objection",
                                 "motion_security", "motion_restore_deadline",
                                 "motion_evidence", "motion_expert"]),
              help="Тип вихідного документа")
@click.option("--case-number", "-n", default="", help="Номер справи")
@click.option("--plaintiff", default="", help="Позивач/Апелянт")
@click.option("--defendant", default="", help="Відповідач")
@click.option("--court", default="", help="Назва суду")
@click.option("--collect/--no-collect", "run_collect", default=False,
              help="Запустити збір нових рішень перед аналізом")
def pipeline(case_file, doc_type, case_number, plaintiff, defendant, court, run_collect):
    """
    Повний пайплайн: Аналіз → Генерація документа
    (Агент 1 опційно → Агент 2 → Агент 3)

    \b
    Приклад:
      python main.py pipeline --case-file case.json --doc-type appeal --case-number 1-123/2024
    """
    console.print(Panel(
        "[bold]Запуск повного пайплайну аналізу та генерації документа[/bold]",
        border_style="blue",
    ))

    case_data = json.loads(Path(case_file).read_text(encoding="utf-8"))

    # Крок 1 (опційно): збір рішень
    if run_collect:
        console.print("\n[bold]── Крок 1: Збір рішень ──[/bold]")
        asyncio.run(_collect_async(
            category=case_data.get("category", "civil"),
            date_from=date(2020, 1, 1),
            date_to=date.today(),
            keywords=None,
            court_level=case_data.get("court_level"),
            region=None,
            max_results=50,
            use_claude=True,
        ))

    # Крок 2: аналіз
    console.print("\n[bold]── Крок 2: Аналіз практики ──[/bold]")
    report_path = _analyze_cmd(case_file, top_k=20, output=None)

    # Крок 3: генерація документа
    console.print("\n[bold]── Крок 3: Генерація документа ──[/bold]")

    # Знаходимо JSON звіту (поряд з .md)
    json_report_path = report_path.replace(".md", ".json")
    if not Path(json_report_path).exists():
        console.print(f"[red]JSON звіту не знайдено: {json_report_path}[/red]")
        return

    from shared.models import AnalysisReport, DocumentRequest
    from shared.claude_client import ClaudeClient
    from agent3_writer.generator import DocumentGenerator
    from agent3_writer.docx_builder import DocxBuilder

    settings = _load_settings()
    analysis_data = json.loads(Path(json_report_path).read_text(encoding="utf-8"))
    analysis_report = AnalysisReport.model_validate(analysis_data)

    request = DocumentRequest(
        document_type=doc_type,
        analysis_report=analysis_report,
        case_parties={
            "plaintiff": plaintiff,
            "defendant": defendant,
            "court": court,
        },
        case_number=case_number or case_data.get("subject", "")[:20],
    )

    claude_client = ClaudeClient()
    text = DocumentGenerator(claude_client).generate(request)
    docx_path = DocxBuilder().build(text, request)

    console.print(f"\n[bold green]✓ Пайплайн завершено![/bold green]")
    console.print(f"  Звіт:     {report_path}")
    console.print(f"  Документ: {docx_path}")


# ===========================================================================
# Команда: stats
# ===========================================================================

@cli.command()
def stats():
    """
    Статистика бази даних рішень

    \b
    Приклад:
      python main.py stats
    """
    try:
        settings = _load_settings()
        from agent1_collector.storage import DecisionStorage
        storage = DecisionStorage()
        s = storage.get_stats()
    except Exception as e:
        console.print(f"[red]Помилка отримання статистики: {e}[/red]")
        console.print("[yellow]Переконайтесь, що .env файл існує та ANTHROPIC_API_KEY налаштовано[/yellow]")
        return

    console.print(Panel(
        f"Рішень у ChromaDB: [bold cyan]{s['total_in_chromadb']}[/bold cyan]\n"
        f"JSON-файлів на диску: [bold cyan]{s['total_json_files']}[/bold cyan]\n"
        f"Шлях до БД: {s['db_path']}",
        title="[bold blue]Статистика бази даних",
    ))

    if s["categories"]:
        table = Table(title="Розподіл по категоріях", box=box.SIMPLE)
        table.add_column("Категорія", style="cyan")
        table.add_column("Кількість", style="bold")
        for cat, count in sorted(s["categories"].items(), key=lambda x: -x[1]):
            table.add_row(cat, str(count))
        console.print(table)

    if s["results_distribution"]:
        table2 = Table(title="Розподіл за результатом", box=box.SIMPLE)
        table2.add_column("Результат", style="cyan")
        table2.add_column("Кількість", style="bold")
        for res, count in sorted(s["results_distribution"].items(), key=lambda x: -x[1]):
            table2.add_row(res, str(count))
        console.print(table2)


# ===========================================================================
# Точка входу
# ===========================================================================

if __name__ == "__main__":
    cli()
