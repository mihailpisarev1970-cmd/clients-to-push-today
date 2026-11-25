"""Deterministic deal-ranking engine for the 'Дожми сегодня' task.

Reads a CSV export of the 'Сделки' sheet, scores every open deal using the
company rules (app/rules.py) and comment heuristics, and returns the top 5
deals with a concrete action for today.  No neural-network calls at runtime.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable

from rules import (
    DEFERMENT_ALLOWED_FOR,
    DECISION_MAKERS,
    MANAGER_DISCOUNT_LIMIT,
    TOP_N,
    add_workdays,
    invoice_expiry,
    parse_rub,
)

DATE_FORMATS = ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%y")

COLUMN_ALIASES = {
    "number": ("№", "n", "number", "номер", "ном"),
    "client": ("клиент", "client", "клиент (название)"),
    "client_type": ("тип клиента", "тип", "client type"),
    "product": ("товар", "product", "наименование"),
    "amount": ("сумма, ₽", "сумма", "сумма, rub", "amount"),
    "stage": ("этап", "stage", "статус"),
    "created": ("создана", "created"),
    "last_contact": ("последний контакт", "last contact"),
    "next_step_date": ("следующий шаг (дата)", "следующий шаг", "next step"),
    "comment": ("комментарий менеджера", "комментарий", "comment"),
}


def parse_date(value: str | None) -> date | None:
    if not value or not str(value).strip():
        return None
    raw = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(value: str | None) -> float:
    if not value:
        return 0.0
    raw = str(value).replace("₽", "").replace("\xa0", " ").replace(" ", "")
    raw = raw.replace(",", ".")
    match = re.search(r"-?\d+(?:\.\d+)?", raw)
    return float(match.group()) if match else 0.0


def normalize_header(header: str) -> str:
    return re.sub(r"\s+", " ", header.strip().lower().replace("\ufeff", ""))



def _deals_from_rows(rows: list[list[str]], source: str = "CSV") -> list["Deal"]:
    """Shared row-to-Deal conversion for CSV and XLSX inputs."""
    if not rows:
        return []
    header = rows[0]
    mapping: dict[str, int] = {}
    for idx, raw in enumerate(header):
        norm = normalize_header(str(raw))
        for key, aliases in COLUMN_ALIASES.items():
            if norm in aliases:
                mapping[key] = idx
                break
    if "client" not in mapping:
        raise ValueError(f"{source} must contain a 'Клиент' column")

    def cell(row: list[str], key: str) -> str:
        idx = mapping.get(key)
        return str(row[idx]).strip() if idx is not None and idx < len(row) else ""

    deals: list[Deal] = []
    for row in rows[1:]:
        if not cell(row, "client"):
            continue
        deals.append(
            Deal(
                number=cell(row, "number"),
                client=cell(row, "client"),
                client_type=cell(row, "client_type"),
                product=cell(row, "product"),
                amount=parse_amount(cell(row, "amount")),
                stage=cell(row, "stage"),
                created=parse_date(cell(row, "created")),
                last_contact=parse_date(cell(row, "last_contact")),
                next_step_date=parse_date(cell(row, "next_step_date")),
                comment=cell(row, "comment"),
            )
        )
    return deals


def read_deals(csv_text: str) -> list["Deal"]:
    """Parse CSV text into Deal objects."""
    text = csv_text.lstrip("\ufeff")
    reader = csv.reader(io.StringIO(text))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    return _deals_from_rows(rows, source="CSV")


def read_deals_xlsx(path: str) -> list["Deal"]:
    """Read the 'Сделки' sheet from an XLSX workbook (e.g. source_sheet.xlsx)."""
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = None
        for name in workbook.sheetnames:
            if "сдел" in name.lower():
                sheet = workbook[name]
                break
        if sheet is None:
            raise ValueError("В XLSX не найден лист «Сделки»")

        rows: list[list[str]] = []
        for row in sheet.iter_rows(values_only=True):
            cells = ["" if value is None else str(value) for value in row]
            if any(cell.strip() for cell in cells):
                rows.append(cells)
        return _deals_from_rows(rows, source="XLSX")
    finally:
        workbook.close()


def load_deals(path: str) -> list["Deal"]:
    """Dispatch by file extension: .xlsx -> 'Сделки' sheet, otherwise CSV."""
    if path.lower().endswith((".xlsx", ".xlsm")):
        return read_deals_xlsx(path)
    with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
        return read_deals(handle.read())


def normalize_doc_url(url: str) -> str:
    """Turn a Google Sheets view/edit link into a direct export link."""
    url = url.strip()
    match = re.search(r"/spreadsheets/d/([^/]+)", url)
    if match:
        doc_id = match.group(1)
        gid_match = re.search(r"[?&#]gid=(\d+)", url)
        gid = gid_match.group(1) if gid_match else "0"
        return f"https://docs.google.com/spreadsheets/d/{doc_id}/export?format=csv&gid={gid}"
    return url


def load_deals_from_url(url: str) -> list["Deal"]:
    """Download a CSV/XLSX document over HTTP(S) and parse it."""
    from urllib.parse import urlparse
    from urllib.request import Request, urlopen

    if not url or not url.strip():
        raise ValueError("Ссылка пустая")
    target = normalize_doc_url(url)
    parsed = urlparse(target)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Нужна ссылка вида http://… или https://…")

    request = Request(target, headers={"User-Agent": "Mozilla/5.0 (perfect-pull-up)"})
    with urlopen(request, timeout=30) as response:
        content_type = response.headers.get("Content-Type", "").lower()
        raw = response.read(20 * 1024 * 1024 + 1)
    if len(raw) > 20 * 1024 * 1024:
        raise ValueError("Файл по ссылке больше 20 МБ")

    path = parsed.path.lower()
    is_xlsx = path.endswith((".xlsx", ".xlsm")) or "spreadsheet" in content_type or "officedocument" in content_type

    if is_xlsx:
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as handle:
            handle.write(raw)
            temp_path = handle.name
        try:
            return read_deals_xlsx(temp_path)
        finally:
            import os

            os.unlink(temp_path)

    text = raw.decode("utf-8-sig", errors="replace")
    return read_deals(text)




@dataclass
class Deal:
    number: str
    client: str
    client_type: str
    product: str
    amount: float
    stage: str
    created: date | None
    last_contact: date | None
    next_step_date: date | None
    comment: str
    score: float = 0.0
    action: str = ""
    reason: str = ""
    uncertain: bool = False
    tags: set[str] = field(default_factory=set)

    @property
    def number_display(self) -> str:
        if re.fullmatch(r"\d+\.0+", self.number):
            return self.number.split(".")[0]
        return self.number

    @property
    def is_budget(self) -> bool:
        return "госзаказчик" in self.client_type.lower() or "бюджет" in self.client_type.lower()



LOWER_IS_DEAD = (
    "купили у конкурента",
    "номер, похоже, неверный",
    "не отвечает 3 недели",
    "вернёмся позже",
    "вернемся позже",
    "бюджет на 2027",
)


def _has(text: str, *needles: str) -> bool:
    low = text.lower()
    return any(n in low for n in needles)


def classify(deal: Deal, today: date) -> None:
    """Attach tags and compute deterministic priority score."""
    text = deal.comment
    tags: set[str] = set()
    score = 0.0

    if _has(text, *LOWER_IS_DEAD):
        tags.add("dead")
        score -= 1000.0
    if _has(text, "дубль", "заведена повторно"):
        tags.add("duplicate")
        score -= 40.0
    if _has(text, "оплата пришла", "оплачен", "100%"):
        tags.add("paid")
        score += 120.0
    if _has(text, "выставляйте счёт", "выставляйте счет", "счёт не выставлен", "счет не выставлен"):
        tags.add("issue_invoice")
        score += 115.0
    if _has(text, "пересчитать", "поменять цвет", "просил поменять", "попросил поменять"):
        tags.add("revise_quote")
        score += 110.0
    if _has(text, "готов брать", "готовы брать", "готов купить"):
        tags.add("ready_to_buy")
        score += 95.0
    if _has(text, "оплатим на этой неделе", "оплата на этой неделе"):
        tags.add("promised_payment")
        score += 85.0
    if _has(text, "проект договора", "договор пока не отправлен", "ждут от нас проект"):
        tags.add("contract_needed")
        score += 90.0
    if _has(text, "секретар"):
        tags.add("wrong_lpr")
        score -= 35.0
    if _has(text, "завхоз") and deal.is_budget:
        tags.add("budget_clerk_ok")
        score += 10.0

    if _has(text, "скидк", "%"):
        discount = parse_rub(text)
        if discount and discount - 0.0001 > MANAGER_DISCOUNT_LIMIT:
            tags.add("discount_above_limit")
            score += 60.0
    if _has(text, "отсрочк", "60 дней", "отсрочку"):
        tags.add("deferment_request")
        if not any(t in deal.client_type.lower() for t in DEFERMENT_ALLOWED_FOR):
            score -= 25.0
        else:
            score += 20.0
    if _has(text, "созвон назначен", "согласуют внутри", "ответ обещали"):
        tags.add("meeting_or_approval")
        score += 55.0

    stage = deal.stage.lower()
    if "счёт выставлен" in stage or "счет выставлен" in stage:
        tags.add("invoice_sent")
        score += 70.0
    if "кп отправл" in stage:
        score += 25.0
    if "согласован" in stage:
        score += 30.0
    if "новая заявка" in stage or "квалификац" in stage:
        score += 10.0

    invoice_base = deal.last_contact or deal.created
    if "invoice_sent" in tags and invoice_base:
        expiry = invoice_expiry(invoice_base)
        if today > expiry:
            tags.add("invoice_expired")
            score -= 30.0
        else:
            tags.add("invoice_valid")
            score += 20.0

    if deal.next_step_date:
        delta = (deal.next_step_date - today).days
        if delta < 0:
            tags.add("overdue_step")
            score -= 20.0
        elif delta <= 7:
            tags.add("step_this_week")
            score += 35.0
        elif delta <= 14:
            tags.add("step_soon")
            score += 10.0
        else:
            tags.add("step_far")
            score -= 80.0
    elif not any(t in tags for t in ("paid", "issue_invoice", "ready_to_buy", "revise_quote")):
        tags.add("no_next_step")
        score -= 5.0

    score += min(deal.amount / 50_000.0, 20.0)
    if deal.last_contact:
        days = (today - deal.last_contact).days
        if days > 45:
            score -= 25.0
        elif days > 21:
            score -= 10.0

    deal.tags = tags
    deal.score = score

    comment: str
    score: float = 0.0
    action: str = ""
    reason: str = ""
    uncertain: bool = False
    tags: set[str] = field(default_factory=set)

    @property
    def number_display(self) -> str:
        if re.fullmatch(r"\d+\.0+", self.number):
            return self.number.split(".")[0]
        return self.number

    @property
    def is_budget(self) -> bool:
        return "госзаказчик" in self.client_type.lower() or "бюджет" in self.client_type.lower()


def make_action(deal: Deal, today: date) -> None:
    """Build a concrete 'what to do today' string from tags and rules."""
    t = deal.tags
    uncertain = False

    if "paid" in t:
        action = "Деньги уже пришли (100% предоплата) — сегодня подтвердить отгрузку и сроки доставки."
    elif "issue_invoice" in t:
        action = "Выставить счёт сегодня (клиент просит). Счёт действует 5 рабочих дней; условия — 100% предоплата."
    elif "revise_quote" in t:
        extra = " Скидку >7% согласуйте с руководителем." if "discount_above_limit" in t else ""
        action = "Пересчитать КП и отправить обновлённую версию сегодня." + extra
    elif "contract_needed" in t:
        action = ("Отправить проект договора сегодня: деньги надо освоить до 15.12, "
                  "завхоз правомочен для бюджетного учреждения.")
    elif "ready_to_buy" in t:
        if "deferment_request" in t and not deal.is_budget:
            action = ("Звонить сегодня: готовность есть, но отсрочка 60 дней недопустима — "
                      "предложить 100% предоплату и скидку до 7% максимум.")
        else:
            action = "Звонить сегодня и выставить счёт на 100% предоплату."
    elif "invoice_valid" in t or "invoice_sent" in t:
        expiry = invoice_expiry(deal.last_contact or deal.created or today)
        if "invoice_expired" in t:
            action = ("Счёт просрочен (действует 5 рабочих дней) — перевыставить счёт сегодня "
                      "и позвонить, напомнив об оплате.")
        else:
            action = (f"Позвонить и напомнить об оплате сегодня. Счёт действует до "
                      f"{expiry.strftime('%d.%m.%Y')} (5 рабочих дней).")
    elif "wrong_lpr" in t:
        action = "Сегодня найти контакт директора/владельца (ЛПР) и позвонить ему, а не секретарю."
    elif "discount_above_limit" in t:
        action = "Согласовать скидку с руководителем сегодня (>7% по правилам) и вернуться с ответом клиенту."
    elif "meeting_or_approval" in t:
        when = deal.next_step_date.strftime('%d.%m.%Y') if deal.next_step_date else 'сегодня'
        action = f"Подготовиться к созвону/ответу ({when}); сегодня подтвердить решение ЛПР и детали."
    elif "overdue_step" in t:
        action = "Шаг просрочен — сегодня перезвонить и уточнить, актуальна ли ещё заявка."
        uncertain = True
    elif "no_next_step" in t:
        action = "Позвонить сегодня и выяснить потребность; следующий шаг не был поставлен."
        uncertain = True
    else:
        action = "Позвонить сегодня, выяснить потребность и зафиксировать следующий шаг."
        uncertain = True

    if "duplicate" in t:
        action += " Внимание: дубль другой сделки — сверить и оставить одну."

    deal.action = action
    if uncertain:
        deal.uncertain = True
        deal.reason = "не уверен, что делать"


def rank(deals: Iterable[Deal], today: date, top_n: int = TOP_N) -> list[Deal]:
    """Score, deduplicate and return the top-N deals ordered by priority."""
    items = list(deals)
    for deal in items:
        classify(deal, today)
        make_action(deal, today)

    alive = [d for d in items if "dead" not in d.tags and "step_far" not in d.tags]
    alive.sort(key=lambda d: (-d.score, -d.amount, d.number_display))

    seen: dict[tuple[str, str], Deal] = {}
    unique: list[Deal] = []
    for deal in alive:
        key = (deal.client.strip().lower(), deal.product.strip().lower())
        if key in seen:
            seen[key].tags.add("has_duplicate")
            seen[key].action += f" (дубль сделки №{deal.number_display} — сверить)"
            continue
        seen[key] = deal
        unique.append(deal)
    return unique[:top_n]


def format_top(deals: list[Deal], total: int | None = None) -> str:
    """Render the exact answer format required by the task."""
    lines: list[str] = []
    for i, deal in enumerate(deals, 1):
        amount = f"{deal.amount:,.0f}".replace(",", " ")
        lines.append(f"{i}. №{deal.number_display} · {deal.client} · {amount} ₽")
        if deal.uncertain:
            lines.append(f"   Что сделать сегодня: не уверен, что делать. {deal.action}")
        else:
            lines.append(f"   Что сделать сегодня: {deal.action}")
        if deal.next_step_date:
            lines.append(f"   Следующий шаг: {deal.next_step_date.strftime('%d.%m.%Y')}")
    lines.append("Итог: " + ", ".join(d.number_display for d in deals))
    if total is not None:
        lines.append(f"Из {total} сделок отобрано {len(deals)}.")
    return "\n".join(lines)

