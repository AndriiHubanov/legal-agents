"""
Оркестратор 5-агентного пайплайну.

Схема:
  Agent1 → Agent2 → [Collector+Analyzer] → loop:
    Agent3 → if needs_revision → Agent1 → Agent2 → Agent3 (repeat)
    Agent4 → Agent5 → if revise_generator → Agent4 → Agent5
                   → if revise_critic    → Agent3 → ... → Agent4 → Agent5
  → done
"""
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from shared.claude_client import ClaudeClient
from shared.logger import get_logger
from shared.models import PipelineState

from agent1_intake.intake_agent import IntakeAgent
from agent2_fees.fees_calculator import FeesCalculator
from agent3_critic.critic_agent import CriticAgent
from agent4_generator.generator_v2 import GeneratorAgentV2
from agent5_expert.expert_reviewer import ExpertReviewer

logger = get_logger(__name__)
console = Console()

# Мінімальний бал критика для переходу до генерації
CRITIC_MIN_SCORE_TO_GENERATE = 5.0

# Максимум ітерацій між Agent3→Agent1 (inner loop)
MAX_CRITIC_INNER_LOOPS = 2


def run_pipeline(
    state: PipelineState,
    run_analysis: bool = True,
    use_existing_analysis_path: str | None = None,
) -> PipelineState:
    """
    Запускає повний 5-агентний пайплайн.

    state                      — початковий стан (з create_state()).
    run_analysis               — чи запускати збір і аналіз практики (Agent1_collector+Agent2_analyst).
    use_existing_analysis_path — шлях до існуючого JSON-звіту аналізу (пропустити збір).

    Повертає оновлений PipelineState із final_docx_path.
    """
    claude = ClaudeClient()
    agent1 = IntakeAgent(claude)
    agent2 = FeesCalculator(claude)
    agent3 = CriticAgent(claude)
    agent4 = GeneratorAgentV2(claude)
    agent5 = ExpertReviewer(claude)

    state.status = "running"
    _print_banner(state)

    # ── Крок 1: Агент 1 — структурування ситуації ─────────────────────────
    _section("Крок 1 / 5 — Архітектор позову (Agent 1)")
    intake, stats1 = agent1.process(
        raw_situation=state.raw_situation,
        iteration=0,
    )
    state.intake_result = intake
    _print_agent_result("Agent1", stats1, {
        "Тип справи": intake.case_type,
        "Кодекс": intake.procedural_code,
        "Документ": intake.recommended_doc_type,
        "Впевненість": f"{intake.confidence:.0%}",
        "Вимоги": str(len(intake.identified_claims)),
        "Відсутня інформація": str(len(intake.missing_info)),
    })

    # ── Крок 2: Агент 2 — судовий збір ───────────────────────────────────
    _section("Крок 2 / 5 — Калькулятор збору (Agent 2)")
    fees, stats2 = agent2.calculate(intake, iteration=0)
    state.fees_calculation = fees
    _print_agent_result("Agent2", stats2, {
        "Тип вимог": fees.claim_type,
        "Ціна позову": f"{fees.claim_amount} грн" if fees.claim_amount else "немайнові",
        "Судовий збір": f"{fees.fee_amount:.2f} грн",
        "Підстава": fees.fee_basis,
        "Підсудність": fees.court_jurisdiction[:60] if fees.court_jurisdiction else "—",
    })

    # ── Крок 3 (опційно): Збір та аналіз практики ──────────────────────────
    analysis = None
    if use_existing_analysis_path:
        analysis = _load_existing_analysis(use_existing_analysis_path)
        state.analysis_report = analysis
    elif run_analysis:
        _section("Крок 3 / 5 — Аналіз судової практики (Collector + Analyzer)")
        analysis = _run_analysis_pipeline(state, intake)
        state.analysis_report = analysis

    # ── Головний цикл ─────────────────────────────────────────────────────
    for master_iter in range(state.max_iterations):
        state.current_iteration = master_iter
        _section(f"Ітерація {master_iter + 1} / {state.max_iterations} — Цикл покращення")

        # ── Agent 3: критичний аналіз ──────────────────────────────────────
        expert_fb = state.expert_reviews[-1] if state.expert_reviews else None
        critic_review, stats3 = agent3.review(
            intake=state.intake_result,
            fees=state.fees_calculation,
            analysis=analysis,
            expert_feedback=expert_fb,
            iteration=master_iter,
        )
        state.critic_reviews.append(critic_review)
        _print_agent_result("Agent3", stats3, {
            "Статус": critic_review.status,
            "Оцінка": f"{critic_review.overall_score:.1f}/10",
            "Заперечень": str(len(critic_review.objections)),
            "Ризиків": str(len(critic_review.legal_risks)),
            "Питань до Agent1": str(len(critic_review.questions_for_intake)),
        })

        # ── Внутрішній цикл: Agent3 → Agent1 → Agent2 → Agent3 ────────────
        if (
            critic_review.status == "critical_issues"
            and critic_review.questions_for_intake
            and master_iter < MAX_CRITIC_INNER_LOOPS
        ):
            console.print(
                f"[yellow]Agent3: критичні проблеми. "
                f"Повертаємо до Agent1 з {len(critic_review.questions_for_intake)} питань.[/yellow]"
            )
            intake, stats1r = agent1.process(
                raw_situation=state.raw_situation,
                critic_questions=critic_review.questions_for_intake,
                iteration=master_iter + 1,
            )
            state.intake_result = intake
            _print_agent_result("Agent1 (revision)", stats1r, {
                "Впевненість": f"{intake.confidence:.0%}",
                "Відсутня інформація": str(len(intake.missing_info)),
            })

            if critic_review.needs_fee_recalculation:
                fees, stats2r = agent2.calculate(intake, iteration=master_iter + 1)
                state.fees_calculation = fees
                _print_agent_result("Agent2 (revision)", stats2r, {
                    "Збір": f"{fees.fee_amount:.2f} грн",
                })

            # Повторна критика
            critic_review, stats3r = agent3.review(
                intake=state.intake_result,
                fees=state.fees_calculation,
                analysis=analysis,
                expert_feedback=None,
                iteration=master_iter + 1,
            )
            state.critic_reviews.append(critic_review)
            _print_agent_result("Agent3 (revision)", stats3r, {
                "Статус": critic_review.status,
                "Оцінка": f"{critic_review.overall_score:.1f}/10",
            })

        # Якщо після ревізії все ще критично — продовжуємо з поточним станом
        if critic_review.overall_score < CRITIC_MIN_SCORE_TO_GENERATE and master_iter < state.max_iterations - 1:
            console.print(
                f"[red]Agent3: оцінка {critic_review.overall_score:.1f} < {CRITIC_MIN_SCORE_TO_GENERATE}. "
                f"Наступна ітерація.[/red]"
            )
            continue

        # ── Agent 4: генерація документа ──────────────────────────────────
        _section(f"Ітерація {master_iter + 1} — Генерація документа (Agent 4)")
        expert_fb_for_gen = state.expert_reviews[-1] if state.expert_reviews else None
        generated, stats4g, stats4c = agent4.generate(
            intake=state.intake_result,
            fees=state.fees_calculation,
            critic_review=critic_review,
            analysis=analysis,
            expert_feedback=expert_fb_for_gen,
            case_parties=state.case_parties,
            case_number=state.case_number,
            iteration=master_iter,
        )
        state.generated_document = generated
        compliance = generated.compliance
        _print_agent_result("Agent4", stats4g, {
            "Обсяг": f"{len(generated.content)} символів",
            "Відповідність": f"{'ТАК' if compliance.is_compliant else 'НІ'} ({compliance.compliance_score:.1f}/10)",
            "Порушень": str(len(compliance.violations)),
        })

        # ── Agent 5: фінальний аудит ───────────────────────────────────────
        _section(f"Ітерація {master_iter + 1} — Фінальний аудит (Agent 5)")
        expert_review, stats5 = agent5.review(
            generated=generated,
            intake=state.intake_result,
            fees=state.fees_calculation,
            analysis=analysis,
            iteration=master_iter,
            previous_reviews=state.expert_reviews,
        )
        state.expert_reviews.append(expert_review)
        _print_expert_result(expert_review, stats5)

        # ── Рішення ───────────────────────────────────────────────────────
        if expert_review.decision == "approved":
            console.print(
                f"\n[bold green]Agent5 схвалив документ! "
                f"Загальна оцінка: {expert_review.total_score:.1f}/10[/bold green]"
            )
            break

        elif expert_review.decision == "revise_generator":
            console.print(
                f"[yellow]Agent5 → revise_generator. "
                f"Оцінка: {expert_review.total_score:.1f}/10. "
                f"Правок: {len(expert_review.mandatory_fixes)}.[/yellow]"
            )
            # Швидка ревізія генератора без нового циклу критика
            if master_iter < state.max_iterations - 1:
                _section(f"Швидка ревізія Agent4 (ітерація {master_iter + 1}b)")
                generated, stats4gr, stats4cr = agent4.generate(
                    intake=state.intake_result,
                    fees=state.fees_calculation,
                    critic_review=critic_review,
                    analysis=analysis,
                    expert_feedback=expert_review,
                    case_parties=state.case_parties,
                    case_number=state.case_number,
                    iteration=master_iter,
                )
                state.generated_document = generated
                expert_review2, stats5r = agent5.review(
                    generated=generated,
                    intake=state.intake_result,
                    fees=state.fees_calculation,
                    analysis=analysis,
                    iteration=master_iter,
                    previous_reviews=state.expert_reviews,
                )
                state.expert_reviews.append(expert_review2)
                _print_expert_result(expert_review2, stats5r)
                if expert_review2.decision == "approved":
                    console.print(
                        f"\n[bold green]Agent5 схвалив (після ревізії)! "
                        f"Оцінка: {expert_review2.total_score:.1f}/10[/bold green]"
                    )
                    expert_review = expert_review2
                    break

        elif expert_review.decision == "revise_critic":
            console.print(
                f"[red]Agent5 → revise_critic. "
                f"Оцінка: {expert_review.total_score:.1f}/10. "
                f"Продовжуємо цикл.[/red]"
            )
            # Продовжуємо зовнішній цикл — Agent3 отримає expert_feedback

    # ── Збереження .docx ──────────────────────────────────────────────────
    if state.generated_document:
        _section("Збереження фінального документа (.docx)")
        docx_path = agent4.build_docx(
            generated=state.generated_document,
            case_parties=state.case_parties,
            case_number=state.case_number,
            lawyer_name=state.case_parties.get("lawyer", ""),
        )
        state.generated_document.docx_path = docx_path
        state.final_docx_path = docx_path
        state.status = "completed"
        console.print(f"\n[bold green]Документ збережено: {docx_path}[/bold green]")
    else:
        state.status = "failed"
        state.error_message = "Документ не було згенеровано."

    _print_summary(state)
    return state


# ---------------------------------------------------------------------------
# Допоміжні функції
# ---------------------------------------------------------------------------

def _load_existing_analysis(path: str):
    """Завантажує існуючий звіт аналізу практики."""
    import json
    from pathlib import Path as P
    from shared.models import AnalysisReport
    data = json.loads(P(path).read_text(encoding="utf-8"))
    report = AnalysisReport.model_validate(data)
    console.print(f"[dim]Завантажено існуючий аналіз: {path}[/dim]")
    return report


def _run_analysis_pipeline(state: PipelineState, intake):
    """Запускає collector + analyzer для пошуку судової практики."""
    from agent1_collector.storage import DecisionStorage
    from agent2_analyst.retriever import PracticeRetriever
    from agent2_analyst.analyzer import PracticeAnalyzer

    storage = DecisionStorage()
    retriever = PracticeRetriever(storage)
    claude = ClaudeClient()
    analyzer = PracticeAnalyzer(claude)

    case = intake.case_description
    relevant = retriever.find_relevant(case, top_k=15)
    console.print(f"[dim]Знайдено {len(relevant)} релевантних рішень у базі[/dim]")

    if not relevant:
        console.print("[yellow]База порожня або немає релевантних рішень — пропускаємо аналіз.[/yellow]")
        return None

    return analyzer.analyze(case, relevant)


def _print_banner(state: PipelineState) -> None:
    console.print(Panel(
        f"[bold]5-агентний правовий пайплайн[/bold]\n"
        f"Session: [cyan]{state.session_id}[/cyan]\n"
        f"Максимум ітерацій: [cyan]{state.max_iterations}[/cyan]\n"
        f"Справа: [cyan]{state.case_number or '(не вказано)'}[/cyan]",
        border_style="blue",
        title="Legal Agents v2",
    ))


def _section(title: str) -> None:
    console.print(f"\n[bold blue]── {title} ──[/bold blue]")


def _print_agent_result(name: str, stats, fields: dict) -> None:
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("Поле", style="cyan")
    table.add_column("Значення")
    for k, v in fields.items():
        table.add_row(k, str(v))
    table.add_row(
        "Кеш",
        f"write={stats.cache_creation_tokens}, read={stats.cache_read_tokens}",
    )
    console.print(Panel(table, title=f"[green]{name}[/green]", border_style="green"))


def _print_expert_result(review, stats) -> None:
    score_color = "green" if review.total_score >= 8.0 else "yellow" if review.total_score >= 6.0 else "red"
    decision_color = "green" if review.decision == "approved" else "yellow" if review.decision == "revise_generator" else "red"
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("Критерій", style="cyan")
    table.add_column("Бал")
    table.add_row("Аргументація", f"{review.argumentation_score:.1f}/10")
    table.add_row("Відповідність", f"{review.compliance_score:.1f}/10")
    table.add_row("Доказова база", f"{review.evidence_score:.1f}/10")
    table.add_row("Переконливість", f"{review.persuasiveness_score:.1f}/10")
    table.add_row(f"[{score_color}]ЗАГАЛЬНА ОЦІНКА[/{score_color}]", f"[{score_color}]{review.total_score:.1f}/10[/{score_color}]")
    table.add_row(f"[{decision_color}]РІШЕННЯ[/{decision_color}]", f"[{decision_color}]{review.decision}[/{decision_color}]")
    table.add_row("Обов. правок", str(len(review.mandatory_fixes)))
    table.add_row("Кеш", f"write={stats.cache_creation_tokens}, read={stats.cache_read_tokens}")
    console.print(Panel(table, title="[bold]Agent5 — Експерт[/bold]", border_style="magenta"))

    if review.mandatory_fixes:
        console.print("[yellow]Обов'язкові правки:[/yellow]")
        for fix in review.mandatory_fixes:
            console.print(f"  [red]•[/red] {fix}")


def _print_summary(state: PipelineState) -> None:
    total_reviews = len(state.expert_reviews)
    final_score = state.expert_reviews[-1].total_score if state.expert_reviews else 0.0
    final_decision = state.expert_reviews[-1].decision if state.expert_reviews else "—"
    critic_iters = len(state.critic_reviews)

    console.print(Panel(
        f"Статус: [{'green' if state.status == 'completed' else 'red'}]{state.status}[/]\n"
        f"Ітерацій критика: {critic_iters}\n"
        f"Ітерацій експерта: {total_reviews}\n"
        f"Фінальна оцінка: {final_score:.1f}/10\n"
        f"Рішення: {final_decision}\n"
        f"Документ: {state.final_docx_path or '—'}",
        title="[bold]Підсумок пайплайну[/bold]",
        border_style="blue",
    ))
