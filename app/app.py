"""Gradio web UI for the 'Дожми сегодня' assistant.

Run locally:  python app.py
Docker:       docker build -t perfect_pull-up . && docker run -p 7860:7860 perfect_pull-up
"""

from __future__ import annotations

from datetime import date, datetime

import gradio as gr

from engine import format_top, load_deals, load_deals_from_url, rank

DEFAULT_DATE = "13.11.2026"  # from the 'Задание' sheet; overridable in the UI


def parse_ui_date(value: str) -> date:
    raw = (value or "").strip()
    if not raw:
        return date.today()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return date.today()


def analyze(file, url_text: str, today_text: str) -> str:
    url = (url_text or "").strip()
    if url:
        try:
            deals = load_deals_from_url(url)
        except Exception as exc:
            return f"Не удалось скачать документ по ссылке: {exc}"
    elif file is not None:
        try:
            deals = load_deals(file.name)
        except (OSError, ValueError) as exc:
            return f"Не удалось прочитать файл: {exc}"
    else:
        return "Загрузите CSV/XLSX-файл или вставьте ссылку на документ."

    if not deals:
        return "В документе не найдено сделок (нужен лист «Сделки» или CSV с колонкой «Клиент»)."

    today = parse_ui_date(today_text)
    selected = rank(deals, today)
    return format_top(selected, total=len(deals))


with gr.Blocks(title="Дожми сегодня — Идеальный турник") as demo:
    gr.Markdown(
        "# Дожми сегодня\n"
        "Ассистент отдела продаж «Идеальный турник». Загрузите выгрузку сделок "
        "(CSV, source_sheet.xlsx или ссылку на документ) — программа покажет 5 сделок, "
        "которые стоит дожать сегодня, в порядке приоритета и с конкретным шагом на сегодня.\n\n"
        "Решения принимаются по правилам компании (оплата 100% предоплатой, скидка до 7%, "
        "счёт действует 5 рабочих дней, ЛПР — руководитель/владелец). "
        "Нейросеть в работе не вызывается."
    )
    with gr.Row():
        file_input = gr.File(label="CSV или XLSX со сделками", file_types=[".csv", ".xlsx", ".xlsm"])
        date_input = gr.Textbox(
            label="Сегодня (дд.мм.гггг)",
            value=DEFAULT_DATE,
            info="По умолчанию — дата из техзадания (13.11.2026). Оставьте пустым для текущей даты.",
        )
    url_input = gr.Textbox(
        label="Или вставьте ссылку на документ",
        placeholder="https://docs.google.com/spreadsheets/d/…/edit или прямая ссылка на .csv/.xlsx",
        info="Если ссылка указана, она имеет приоритет над загруженным файлом.",
    )
    button = gr.Button("Дожми сегодня", variant="primary")
    output = gr.Textbox(label="Результат", lines=20)
    button.click(analyze, inputs=[file_input, url_input, date_input], outputs=output)


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        footer_links=[],  # hide "Use via API / Built with Gradio / Settings"
    )
