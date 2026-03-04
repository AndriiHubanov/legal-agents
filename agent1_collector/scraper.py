"""
Асинхронний Playwright-скрапер для https://reyestr.court.gov.ua/

Реальна структура сайту (перевірено):
- Головна сторінка: https://reyestr.court.gov.ua/
- Рішення: https://reyestr.court.gov.ua/Review/{id}
- Метадані рішення: #divcasecat
- Текст рішення: #divdocument (всередині iframe #divframe)
- Посилання на результати: a[href*="/Review/"]
"""
import asyncio
import json
import re
from pathlib import Path
from typing import Optional

from playwright.async_api import (
    async_playwright,
    Page,
    BrowserContext,
    TimeoutError as PWTimeout,
)

from agent1_collector.filters import SearchFilters, CATEGORIES
from shared.config import settings
from shared.logger import get_logger

logger = get_logger(__name__)

BASE_URL = "https://reyestr.court.gov.ua"
MAIN_URL = f"{BASE_URL}/"

# Форма судочинства для фільтру категорії
CATEGORY_FORM_VALUES = {
    "civil":      "Цивільне",
    "admin":      "Адміністративне",
    "commercial": "Господарське",
    "criminal":   "Кримінальне",
    "labor":      "Цивільне",  # трудові спори — цивільне судочинство
}


class CourtScraper:
    def __init__(self):
        self._context: Optional[BrowserContext] = None
        self._cookies_file = Path(settings.RAW_DATA_PATH) / "session_cookies.json"
        self._delay = settings.SCRAPE_DELAY_SECONDS

    # ------------------------------------------------------------------
    # Управління браузером
    # ------------------------------------------------------------------

    async def __aenter__(self):
        self._playwright = await async_playwright().start()
        browser = await self._playwright.chromium.launch(
            headless=settings.HEADLESS_BROWSER,
        )
        self._context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="uk-UA",
            viewport={"width": 1280, "height": 900},
        )
        await self._load_cookies()
        return self

    async def __aexit__(self, *args):
        await self._save_cookies()
        if self._context:
            await self._context.browser.close()
        await self._playwright.stop()

    async def _load_cookies(self) -> None:
        if self._cookies_file.exists():
            try:
                cookies = json.loads(self._cookies_file.read_text(encoding="utf-8"))
                await self._context.add_cookies(cookies)
            except Exception as e:
                logger.warning(f"Не вдалося завантажити cookies: {e}")

    async def _save_cookies(self) -> None:
        try:
            cookies = await self._context.cookies()
            self._cookies_file.parent.mkdir(parents=True, exist_ok=True)
            self._cookies_file.write_text(
                json.dumps(cookies, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"Не вдалося зберегти cookies: {e}")

    # ------------------------------------------------------------------
    # Публічний API
    # ------------------------------------------------------------------

    async def search(self, filters: SearchFilters) -> list[dict]:
        """Пошук рішень за фільтрами, повертає список мета-даних"""
        page = await self._context.new_page()
        results: list[dict] = []

        try:
            logger.info(f"Відкриваю {MAIN_URL} ...")
            await page.goto(MAIN_URL, wait_until="domcontentloaded", timeout=40_000)
            await asyncio.sleep(3)

            # Спробуємо заповнити форму пошуку
            filled = await self._apply_filters(page, filters)
            if not filled:
                logger.warning("Форму пошуку не вдалось заповнити — збираємо з поточної сторінки")

            await asyncio.sleep(2)

            # Збираємо результати по сторінках
            page_num = 1
            while len(results) < filters.max_results:
                items = await self._extract_list_items(page)

                if not items:
                    logger.info(f"Сторінка {page_num}: рішень не знайдено")
                    # Зберігаємо скріншот для діагностики
                    await self._save_debug_screenshot(page, f"page_{page_num}")
                    break

                results.extend(items)
                logger.info(
                    f"Сторінка {page_num}: +{len(items)} рішень (всього {len(results)})"
                )

                if len(results) >= filters.max_results:
                    break
                if not await self._go_to_next_page(page):
                    break
                page_num += 1
                await self._sleep()

        except PWTimeout:
            logger.error("Timeout при завантаженні сторінки пошуку")
        except Exception as e:
            logger.error(f"Помилка пошуку: {e}", exc_info=True)
        finally:
            await page.close()

        return results[: filters.max_results]

    async def get_decision_details(self, decision_id: str) -> dict:
        """
        Завантажити сторінку рішення та витягти дані.
        URL: https://reyestr.court.gov.ua/Review/{decision_id}
        """
        url = f"{BASE_URL}/Review/{decision_id}"
        page = await self._context.new_page()
        data = {
            "id": decision_id,
            "url": url,
            "full_text": "",
            "registry_number": "",
            "court_name": "",
            "decision_date": "",
            "category": "",
        }

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(2)

            # Метадані з #divcasecat
            meta_el = page.locator("#divcasecat")
            if await meta_el.count():
                meta_text = await meta_el.inner_text()
                data.update(self._parse_meta_text(meta_text))

            # Текст рішення — спершу пробуємо #divdocument, потім iframe
            text = ""
            doc_el = page.locator("#divdocument")
            if await doc_el.count():
                text = await doc_el.inner_text()

            if not text:
                # Чекаємо iframe та зчитуємо його вміст
                frame_el = page.locator("#divframe")
                if await frame_el.count():
                    try:
                        frame = page.frame_locator("#divframe")
                        text = await frame.locator("body").inner_text(timeout=10_000)
                    except Exception:
                        pass

            if not text:
                text = await page.inner_text("body")

            data["full_text"] = text.strip()

        except PWTimeout:
            logger.warning(f"Timeout для рішення {decision_id}")
        except Exception as e:
            logger.error(f"Помилка завантаження рішення {decision_id}: {e}")
        finally:
            await page.close()

        return data

    async def scrape_batch(self, filters: SearchFilters) -> list[dict]:
        """Повний збір: пошук + завантаження деталей кожного рішення"""
        meta_list = await self.search(filters)

        if not meta_list:
            logger.warning(
                "Пошук не дав результатів. Перевірте скріншот у data/raw_decisions/debug_*.png"
            )
            return []

        decisions: list[dict] = []
        for i, meta in enumerate(meta_list, 1):
            decision_id = meta.get("id", "")
            logger.info(f"[{i}/{len(meta_list)}] Завантажую рішення {decision_id}")
            try:
                details = await self.get_decision_details(decision_id)
                # Об'єднуємо мета з деталями
                meta.update({k: v for k, v in details.items() if v})
                decisions.append(meta)
            except Exception as e:
                logger.error(f"Помилка для {decision_id}: {e}")
            await self._sleep()

        logger.info(f"Зібрано {len(decisions)} рішень")
        return decisions

    # ------------------------------------------------------------------
    # Приватні методи — взаємодія з формою
    # ------------------------------------------------------------------

    async def _apply_filters(self, page: Page, filters: SearchFilters) -> bool:
        """
        Заповнити форму пошуку на reyestr.court.gov.ua.
        Повертає True якщо вдалося натиснути кнопку пошуку.
        """
        try:
            params = filters.to_query_params()
            date_from_str = filters.date_from.strftime("%d.%m.%Y")
            date_to_str = filters.date_to.strftime("%d.%m.%Y")

            # --- Текстовий пошук ---
            # Сайт має поле з id="logon" або схожі поля введення тексту
            text_selectors = [
                "input#logon",
                "input[type='text']:visible",
                "input[placeholder*='пошук' i]",
                "input[placeholder*='текст' i]",
                "#searchInput",
            ]
            keywords_text = " ".join(filters.keywords) if filters.keywords else ""

            for sel in text_selectors:
                el = page.locator(sel)
                if await el.count() and keywords_text:
                    await el.first.fill(keywords_text)
                    logger.info(f"Введено ключові слова у {sel!r}")
                    break

            # --- Дата від ---
            date_from_selectors = [
                "input[id*='DateFrom' i]",
                "input[id*='date_from' i]",
                "input[id*='sd']",
                "input[placeholder*='від' i]",
                "input[placeholder*='дд.мм.рррр']:first-of-type",
            ]
            for sel in date_from_selectors:
                el = page.locator(sel)
                if await el.count():
                    await el.first.fill(date_from_str)
                    logger.info(f"Дата від встановлена: {date_from_str}")
                    break

            # --- Дата до ---
            date_to_selectors = [
                "input[id*='DateTo' i]",
                "input[id*='date_to' i]",
                "input[id*='ed']",
                "input[placeholder*='до' i]",
                "input[placeholder*='дд.мм.рррр']:last-of-type",
            ]
            for sel in date_to_selectors:
                el = page.locator(sel)
                if await el.count():
                    await el.first.fill(date_to_str)
                    logger.info(f"Дата до встановлена: {date_to_str}")
                    break

            # --- Кнопка пошуку ---
            search_btn_selectors = [
                "button[type='submit']",
                "input[type='submit']",
                "button:has-text('Шукати')",
                "button:has-text('Пошук')",
                "a:has-text('Пошук')",
                ".searchBtn",
                "#searchBtn",
                "input[value='Пошук']",
                "input[value='Шукати']",
            ]
            for sel in search_btn_selectors:
                btn = page.locator(sel)
                if await btn.count() and await btn.first.is_visible():
                    await btn.first.click()
                    await asyncio.sleep(3)
                    await page.wait_for_load_state("domcontentloaded", timeout=20_000)
                    logger.info(f"Форму надіслано через {sel!r}")
                    return True

            logger.warning("Кнопку пошуку не знайдено")
            return False

        except Exception as e:
            logger.error(f"Помилка заповнення форми: {e}")
            return False

    # ------------------------------------------------------------------
    # Приватні методи — парсинг результатів
    # ------------------------------------------------------------------

    async def _extract_list_items(self, page: Page) -> list[dict]:
        """
        Витягти список рішень з поточної сторінки.
        Шукаємо всі посилання що ведуть на /Review/{id}
        """
        items: list[dict] = []
        try:
            # Всі посилання на рішення мають href містить /Review/
            links = page.locator("a[href*='/Review/']")
            count = await links.count()
            logger.debug(f"Знайдено посилань /Review/: {count}")

            seen_ids: set[str] = set()
            for i in range(count):
                link = links.nth(i)
                try:
                    href = await link.get_attribute("href") or ""
                    text = (await link.inner_text()).strip()

                    # Витягуємо числовий ID з URL
                    match = re.search(r"/Review/(\d+)", href, re.I)
                    if not match:
                        continue
                    decision_id = match.group(1)

                    if decision_id in seen_ids:
                        continue
                    seen_ids.add(decision_id)

                    items.append({
                        "id": decision_id,
                        "registry_number": text or decision_id,
                        "url": f"{BASE_URL}/Review/{decision_id}",
                        "full_text": "",
                    })
                except Exception:
                    continue

        except Exception as e:
            logger.warning(f"Помилка парсингу списку: {e}")

        return items

    async def _go_to_next_page(self, page: Page) -> bool:
        """Перейти на наступну сторінку результатів"""
        next_selectors = [
            "a.enButton",          # клас з реального сайту
            "a[title='Наступна']",
            "a:has-text('>')",
            "a:has-text('»')",
            ".pagination a:last-child",
        ]
        for sel in next_selectors:
            btn = page.locator(sel)
            if await btn.count() and await btn.first.is_visible():
                try:
                    await btn.first.click()
                    await asyncio.sleep(2)
                    await page.wait_for_load_state("domcontentloaded", timeout=15_000)
                    return True
                except Exception:
                    continue
        return False

    # ------------------------------------------------------------------
    # Утиліти
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_meta_text(meta_text: str) -> dict:
        """Витягує структуровані дані з тексту блоку #divcasecat"""
        data: dict = {}

        # Номер справи
        m = re.search(r"справи?\s*[№#]?\s*([\d\-/а-яА-ЯіІїЇєЄ]+/\d{4})", meta_text)
        if m:
            data["registry_number"] = m.group(1)

        # Дата надіслання
        m = re.search(r"Надіслано\D+(\d{2}\.\d{2}\.\d{4})", meta_text)
        if m:
            data["decision_date"] = m.group(1)

        return data

    async def _save_debug_screenshot(self, page: Page, name: str) -> None:
        """Зберігає скріншот поточного стану сторінки для діагностики"""
        try:
            debug_dir = Path(settings.RAW_DATA_PATH)
            debug_dir.mkdir(parents=True, exist_ok=True)
            path = str(debug_dir / f"debug_{name}.png")
            await page.screenshot(path=path, full_page=False)
            logger.info(f"Скріншот збережено: {path}")
        except Exception as e:
            logger.warning(f"Не вдалося зберегти скріншот: {e}")

    async def _sleep(self) -> None:
        await asyncio.sleep(self._delay)
