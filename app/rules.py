"""Company rules from the 'Правила' sheet of source_sheet.xlsx.

These are deterministic constants used by the ranking engine.  No AI model
is called at runtime — this module simply encodes the spreadsheet rules.
"""

from datetime import date, timedelta

# Оплата: юрлица и физлица — 100% предоплата; отсрочка только госзаказчикам по контракту.
PREPAYMENT_PERCENT = 100
DEFERMENT_ALLOWED_FOR = ("госзаказчик",)

# Скидка: менеджер до 7%, больше — только с согласия руководителя.
MANAGER_DISCOUNT_LIMIT = 0.07

# Изготовление 10–14 рабочих дней после оплаты, доставка 2–5 дней.
PRODUCTION_WORKDAYS_MIN = 10
PRODUCTION_WORKDAYS_MAX = 14
DELIVERY_WORKDAYS_MIN = 2
DELIVERY_WORKDAYS_MAX = 5

# Счёт действует 5 рабочих дней.
INVOICE_VALID_WORKDAYS = 5

# Кто решает: руководитель/владелец; секретарь и завхоз не ЛПР,
# кроме бюджетных учреждений, где договор готовит завхоз.
DECISION_MAKERS = ("руководитель", "владелец", "директор", "лпр")
CLERK_NOT_DM = ("секретар", "завхоз")
BUDGET_TYPES = ("госзаказчик", "бюджет")

# Цель менеджера из листа «Задание».
MONTHLY_GAP_RUB = 1_550_000
TOP_N = 5


def add_workdays(start: date, workdays: int) -> date:
    """Add N working days (Mon–Fri) to a date, inclusive-of-next-day semantics."""
    current = start
    added = 0
    while added < workdays:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def invoice_expiry(invoice_date: date) -> date:
    """Invoice expires 5 working days after it was issued."""
    return add_workdays(invoice_date, INVOICE_VALID_WORKDAYS)


def parse_rub(text: str) -> float | None:
    """Extract a percentage-like discount from free-form comment text."""
    import re

    match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", text)
    if not match:
        return None
    return float(match.group(1).replace(",", ".")) / 100.0
