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
    from agent1_collector.parser import parse_decision_page, extract_structured_positions, detect_decision_result, normalize_court_name
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
            structured = {}
            if claude_client and raw.get("full_text"):
                structured = extract_structured_positions(raw["full_text"], claude_client)

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
                legal_positions=structured.get("legal_positions", []),
                cited_laws=structured.get("cited_laws", []),
                damage_amount=structured.get("damage_amount"),
                evidence_types=structured.get("evidence_types", []),
                url=raw.get("url", ""),
            )
            storage.save_decision(decision)
            saved_count += 1
        except Exception as e:
            console.print(f"[red]Помилка обробки {raw.get('id', '?')}: {e}[/red]")

    console.print(f"\n[bold green]Збережено {saved_count} рішень у ChromaDB та JSON[/bold green]")


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
# Команда: smart-pipeline (5-агентний пайплайн)
# ===========================================================================

@cli.command("smart-pipeline")
@click.option("--situation", "-s", default=None,
              help="Текст ситуації / позову (або --situation-file)")
@click.option("--situation-file", "-f", default=None, type=click.Path(exists=True),
              help="Шлях до текстового файлу з описом ситуації")
@click.option("--plaintiff", default="", help="Позивач (ПІБ або назва)")
@click.option("--plaintiff-details", default="",
              help="Деталі позивача: адреса, РНОКПП/ЄДРПОУ, пошта, тел.")
@click.option("--defendant", default="", help="Відповідач (ПІБ або назва)")
@click.option("--defendant-details", default="", help="Деталі відповідача: адреса, РНОКПП, пошта, тел.")
@click.option("--court", default="", help="Назва суду (якщо відома)")
@click.option("--lawyer", default="", help="ПІБ адвоката/представника")
@click.option("--case-number", "-n", default="", help="Номер справи")
@click.option("--max-iterations", default=3, show_default=True,
              help="Максимум ітерацій покращення (рекомендовано 2–3)")
@click.option("--analysis-file", "-a", default=None, type=click.Path(exists=True),
              help="Існуючий JSON-звіт аналізу практики (пропустити збір рішень)")
@click.option("--no-analysis", is_flag=True, default=False,
              help="Не запускати аналіз судової практики")
@click.option("--save-state", "save_state_flag", is_flag=True, default=False,
              help="Зберегти повний стан пайплайну у JSON після завершення")
def smart_pipeline(
    situation, situation_file, plaintiff, plaintiff_details,
    defendant, defendant_details, court, lawyer, case_number,
    max_iterations, analysis_file, no_analysis, save_state_flag,
):
    """
    5-агентний пайплайн: Архітектор - Збір - Критик - Генератор - Експерт

    \b
    Agent 1: Структурує ситуацію у правову позицію (архітектор)
    Agent 2: Розраховує судовий збір і підсудність
    Agent 3: Критично аналізує позицію (комунікує з Agent1/2)
    Agent 4: Генерує документ + перевірка відповідності ЦПК/КАС/ГПК
    Agent 5: Фінальний аудит - схвалює або відправляє на доопрацювання

    \b
    Приклади:
      python main.py smart-pipeline --situation "Орендар не платить 6 місяців..."
      python main.py smart-pipeline --situation-file situation.txt --plaintiff "Іванов І.І."
      python main.py smart-pipeline -s "..." --max-iterations 2 --no-analysis
    """
    from orchestrator.pipeline_v2 import run_pipeline
    from orchestrator.state import create_state, save_state as _save_state

    # Читаємо текст ситуації
    if situation_file:
        raw_situation = Path(situation_file).read_text(encoding="utf-8").strip()
    elif situation:
        raw_situation = situation.strip()
    else:
        console.print("[red]Помилка: вкажіть --situation або --situation-file[/red]")
        return

    if not raw_situation:
        console.print("[red]Помилка: текст ситуації порожній.[/red]")
        return

    _load_settings()

    case_parties = {
        "plaintiff": plaintiff,
        "plaintiff_details": plaintiff_details or None,
        "defendant": defendant,
        "defendant_details": defendant_details or None,
        "court": court,
        "lawyer": lawyer or None,
    }

    state = create_state(
        raw_situation=raw_situation,
        case_parties=case_parties,
        case_number=case_number,
        max_iterations=max_iterations,
    )

    final_state = run_pipeline(
        state=state,
        run_analysis=not no_analysis and not analysis_file,
        use_existing_analysis_path=analysis_file,
    )

    if save_state_flag:
        path = _save_state(final_state)
        console.print(f"[dim]Стан пайплайну збережено: {path}[/dim]")

    if final_state.status == "completed":
        console.print(f"\n[bold green]Готово![/bold green] Файл: {final_state.final_docx_path}")
    else:
        console.print(f"\n[red]Пайплайн завершився зі статусом: {final_state.status}[/red]")
        if final_state.error_message:
            console.print(f"[red]{final_state.error_message}[/red]")


# ===========================================================================
# Точка входу
# ===========================================================================

if __name__ == "__main__":
    cli()
