"""Unit tests for the deterministic ranking engine."""

import io
import sys
from datetime import date
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

from engine import format_top, load_deals, rank, read_deals  # noqa: E402

from rules import add_workdays, invoice_expiry  # noqa: E402

XLSX_PATH = Path(__file__).resolve().parents[2] / "source_sheet.xlsx"

CSV_PATH = Path(__file__).resolve().parents[1] / "sample_data" / "deals.csv"


@pytest.fixture(scope="module")
def deals():
    return read_deals(CSV_PATH.read_text(encoding="utf-8-sig"))


def test_csv_parsed(deals):
    assert len(deals) == 40
    assert deals[0].client == "ООО «СпортПарк»"
    assert deals[0].number_display == "1"


def test_working_days():
    friday = date(2026, 11, 13)
    assert add_workdays(friday, 1) == date(2026, 11, 16)
    assert invoice_expiry(date(2026, 11, 9)) == date(2026, 11, 16)


def test_top5_on_13_november(deals):
    selected = rank(deals, date(2026, 11, 13))
    assert len(selected) == 5
    numbers = {d.number_display for d in selected}
    assert "27" in numbers  # client asked to issue an invoice
    assert "37" in numbers  # director asked to recalculate
    assert "28" in numbers  # budget deal with contract needed


def test_dead_deals_never_selected(deals):
    selected = rank(deals, date(2026, 11, 13))
    numbers = {d.number_display for d in selected}
    assert not numbers & {"9", "39", "23"}  # competitor / bad number / 2027 budget


def test_duplicate_collapsed(deals):
    selected = rank(deals, date(2026, 11, 13))
    numbers = [d.number_display for d in selected]
    assert not ("1" in numbers and "12" in numbers), "duplicate deals must be collapsed"


def test_output_format(deals):
    text = format_top(rank(deals, date(2026, 11, 13)), total=len(deals))
    assert text.strip().endswith("Из 40 сделок отобрано 5.")
    assert "Итог:" in text
    assert "Что сделать сегодня:" in text


def test_other_day_still_works(deals):
    selected = rank(deals, date(2026, 11, 25))
    assert len(selected) == 5
    assert "Итог:" in format_top(selected)


def test_deferment_violation_flagged(deals):
    from engine import classify

    deal = next(d for d in deals if d.number_display == "10")
    classify(deal, date(2026, 11, 13))
    assert "deferment_request" in deal.tags
    assert "отсрочка 60 дней недопустима" in deal.action


def test_discount_above_limit_flagged(deals):
    from engine import classify

    deal = next(d for d in deals if d.number_display == "14")
    classify(deal, date(2026, 11, 13))
    assert "discount_above_limit" in deal.tags


def test_xlsx_source_sheet_loads():
    """The exact file the user uploads must be accepted."""
    if not XLSX_PATH.exists():
        pytest.skip("source_sheet.xlsx not next to the project")
    deals = load_deals(str(XLSX_PATH))
    assert len(deals) == 40
    assert deals[0].client == "ООО «СпортПарк»"
    text = format_top(rank(deals, date(2026, 11, 13)), total=len(deals))
    assert "Итог: 34, 28, 12, 37, 27" in text


def test_google_sheets_url_normalization():
    from engine import normalize_doc_url

    out = normalize_doc_url(
        "https://docs.google.com/spreadsheets/d/ABC123/edit?usp=sharing#gid=42"
    )
    assert out == "https://docs.google.com/spreadsheets/d/ABC123/export?format=csv&gid=42"


def test_load_from_url_csv(tmp_path):
    import http.server
    import functools
    import threading

    from engine import load_deals_from_url

    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=str(CSV_PATH.parent),
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        loaded = load_deals_from_url(f"http://127.0.0.1:{port}/deals.csv")
        assert len(loaded) == 40
        assert loaded[0].client == "ООО «СпортПарк»"
    finally:
        server.shutdown()
        server.server_close()

