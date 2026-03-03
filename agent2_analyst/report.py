"""
Форматування та збереження звітів аналізу
"""
import json
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from shared.config import settings
from shared.logger import get_logger
from shared.models import AnalysisReport

logger = get_logger(__name__)
console = Console()


def format_report(analysis: AnalysisReport) -> str:
    """Форматує звіт у Markdown"""
    case = analysis.case_description
    lines = [
        f"# Звіт аналізу судової практики",
        f"",
        f"**Дата аналізу:** {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        f"**Категорія справи:** {case.category}",
        f"**Предмет спору:** {case.subject}",
        f"**Бажаний результат:** {case.desired_outcome}",
        f"**Оцінка шансів:** {analysis.confidence_score:.0%}",
        f"",
        f"---",
        f"",
        f"## Релевантна практика ({len(analysis.relevant_decisions)} рішень)",
        f"",
    ]

    for i, d in enumerate(analysis.relevant_decisions, 1):
        lines.append(
            f"### {i}. {d.registry_number} — {d.court_name} ({d.decision_date})"
        )
        lines.append(f"**Результат:** {d.result}")
        lines.append(f"**Предмет:** {d.subject[:300]}")
        if d.legal_positions:
            lines.append("**Правові позиції:**")
            for pos in d.legal_positions:
                lines.append(f"- {pos}")
        lines.append(f"**Джерело:** {d.url}")
        lines.append("")

    lines += [
        "---",
        "",
        "## Правові аргументи на захист клієнта",
        "",
    ]
    for i, arg in enumerate(analysis.legal_arguments, 1):
        lines.append(f"{i}. {arg}")

    lines += [
        "",
        "## Можливі контраргументи опонента",
        "",
    ]
    for i, arg in enumerate(analysis.counter_arguments, 1):
        lines.append(f"{i}. {arg}")

    lines += [
        "",
        "## Рекомендована стратегія",
        "",
        analysis.recommended_strategy,
        "",
        "---",
        "*Звіт згенеровано автоматично системою аналізу судової практики.*",
        "*Потребує перевірки кваліфікованим юристом.*",
    ]

    return "\n".join(lines)


def save_report(analysis: AnalysisReport, filename: str | None = None) -> str:
    """Зберігає звіт у data/analysis_reports/ як .md файл. Повертає шлях."""
    Path(settings.REPORTS_PATH).mkdir(parents=True, exist_ok=True)

    if filename is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_subject = analysis.case_description.subject[:30].replace(" ", "_").replace("/", "-")
        filename = f"report_{ts}_{safe_subject}.md"

    report_path = Path(settings.REPORTS_PATH) / filename
    report_md = format_report(analysis)
    report_path.write_text(report_md, encoding="utf-8")

    # Також зберегти JSON для подальшої обробки
    json_path = report_path.with_suffix(".json")
    json_path.write_text(
        analysis.model_dump_json(indent=2, default=str),
        encoding="utf-8",
    )

    logger.info(f"Звіт збережено: {report_path}")
    return str(report_path)


def print_summary(analysis: AnalysisReport) -> None:
    """Виводить короткий резюме у консоль через rich"""
    case = analysis.case_description

    # Заголовок
    console.print(
        Panel(
            f"[bold]{case.subject}[/bold]\n"
            f"Категорія: [cyan]{case.category}[/cyan] | "
            f"Рівень: [cyan]{case.court_level}[/cyan]",
            title="[bold blue]Аналіз судової практики",
            border_style="blue",
        )
    )

    # Оцінка шансів
    score_pct = analysis.confidence_score * 100
    color = "green" if score_pct >= 60 else "yellow" if score_pct >= 40 else "red"
    console.print(f"\nОцінка шансів: [{color}]{score_pct:.0f}%[/{color}]")
    console.print(f"Знайдено рішень: [bold]{len(analysis.relevant_decisions)}[/bold]\n")

    # Аргументи
    if analysis.legal_arguments:
        console.print("[bold green]Правові аргументи:[/bold green]")
        for i, arg in enumerate(analysis.legal_arguments[:5], 1):
            console.print(f"  {i}. {arg[:120]}{'...' if len(arg) > 120 else ''}")

    if analysis.counter_arguments:
        console.print("\n[bold yellow]Контраргументи:[/bold yellow]")
        for i, arg in enumerate(analysis.counter_arguments[:3], 1):
            console.print(f"  {i}. {arg[:120]}{'...' if len(arg) > 120 else ''}")

    if analysis.recommended_strategy:
        console.print(f"\n[bold]Стратегія:[/bold] {analysis.recommended_strategy[:300]}")

    # Топ-5 рішень
    if analysis.relevant_decisions:
        table = Table(title="\nТоп-5 релевантних рішень", box=box.SIMPLE)
        table.add_column("№", style="dim", width=4)
        table.add_column("Справа", style="cyan")
        table.add_column("Суд", style="white")
        table.add_column("Дата")
        table.add_column("Результат", style="green")

        for i, d in enumerate(analysis.relevant_decisions[:5], 1):
            table.add_row(
                str(i),
                d.registry_number[:25],
                d.court_name[:30],
                str(d.decision_date),
                d.result,
            )
        console.print(table)
