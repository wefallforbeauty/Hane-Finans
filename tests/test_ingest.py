"""CSV import, duplicate detection and categorisation rules."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from hane_finans.core.kinds import EXPENSE, INCOME
from hane_finans.errors import FinansError
from hane_finans.ingest.csvfile import (
    CsvFormat,
    import_csv,
    import_key,
    parse_column_map,
    read_rows,
)
from hane_finans.ingest.rules import (
    add_rule,
    apply_rules_to_uncategorised,
    load_rules,
    match,
)
from hane_finans.ledger.accounts import create_account
from hane_finans.ledger.models import Transaction
from hane_finans.ledger.queries import commodity_totals, positions_at

STANDARD = """tarih;aciklama;tutar;hesap;karsi_hesap;birim;karsi_taraf;not
2026-10-01;MİGROS KONYA;-450,00;Bonus;;;;
2026-10-02;Maaş Ekim;65.000,00;Vadesiz;Gelir:Maaş;;Şirket;
2026-10-03;Kart ödemesi;-450,00;Vadesiz;Bonus;;;
2026-10-04;KAHVE DÜNYASI;-95,50;Bonus;;;;
2026-10-04;KAHVE DÜNYASI;-95,50;Bonus;;;;ikinci kahve
2026-10-05;BİLİNMEYEN YER;-10,00;Bonus;;;;
"""


@pytest.fixture
def ledger(session):
    create_account(session, "Varlık:Banka:Vadesiz", kind="vadesiz")
    create_account(session, "Borç:Kredi Kartı:Bonus", kind="kredi_karti")
    create_account(session, "Gider:Gıda:Market")
    create_account(session, "Gider:Gıda:Kahve")
    create_account(session, "Gelir:Maaş")
    add_rule(session, "migros", "Market")
    add_rule(session, r"kahve\s+dunyasi", "Kahve", is_regex=True)
    return session


def write(tmp_path, text, name="ekstre.csv", encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return path


def tx_count(session):
    return session.scalar(select(func.count()).select_from(Transaction))


def test_standard_import_with_rules_and_duplicates(ledger, tmp_path):
    s = ledger
    path = write(tmp_path, STANDARD)
    result = import_csv(s, path)
    assert result.errors == [] and result.written
    assert result.added == 6 and result.duplicates == 0 and result.uncategorised == 1
    how = [r.how for r in result.rows]
    assert how[0].startswith("kural #") and how[1] == "dosya" and how[3].startswith("kural #") and how[5] == "kategorisiz"
    balances = {p.account: p.quantity for p in positions_at(s, date(2026, 10, 31), types=("varlik", "borc", EXPENSE, INCOME))}
    assert balances["Gider:Gıda:Market"] == Decimal("450.00")
    assert balances["Gider:Gıda:Kahve"] == Decimal("191.00")  # both identical coffees are kept
    assert balances["Gider:Kategorisiz"] == Decimal("10.00")
    assert balances["Borç:Kredi Kartı:Bonus"] == Decimal("-201.00")  # 450 + 191 + 10 spent, 450 paid
    assert balances["Varlık:Banka:Vadesiz"] == Decimal("64550.00")
    assert all(v == 0 for v in commodity_totals(s).values())

    again = import_csv(s, path)
    assert again.duplicates == 6 and again.added == 0
    assert tx_count(s) == 6


def test_dry_run_writes_nothing(ledger, tmp_path):
    result = import_csv(ledger, write(tmp_path, STANDARD), dry_run=True)
    assert len(result.rows) == 6 and not result.written and result.added == 0
    assert tx_count(ledger) == 0


def test_invalid_rows_abort_the_whole_import(ledger, tmp_path):
    text = STANDARD + "2026-10-06;Bozuk satır;on beş lira;Bonus;;;;\n2026-13-01;Tarih yanlış;-5;Bonus;;;;\n"
    result = import_csv(ledger, write(tmp_path, text))
    assert len(result.errors) == 2 and not result.written
    assert result.errors[0].startswith("satır 8")
    assert tx_count(ledger) == 0


def test_unknown_account_is_an_error(ledger, tmp_path):
    result = import_csv(ledger, write(tmp_path, "tarih;aciklama;tutar;hesap\n2026-10-01;x;-1;Yok Böyle\n"))
    assert result.errors and "bulunamadı" in result.errors[0]


def test_bank_layout_with_mapping(ledger, tmp_path):
    """Windows-1254 file, two lines of account info above the header, separate
    debit/credit columns, Turkish dates and decimal commas."""
    text = (
        "Hesap No: TR00 0000;;;\n"
        "Dönem: Ekim 2026;;;\n"
        "İşlem Tarihi;Açıklama;Borç;Alacak\n"
        "01.10.2026;MİGROS ŞUBE;1.250,75;\n"
        "02.10.2026;MAAŞ;;65.000,00\n"
    )
    path = write(tmp_path, text, encoding="cp1254")
    fmt = CsvFormat(
        date_format="%d.%m.%Y",
        columns=parse_column_map("tarih=İşlem Tarihi,aciklama=Açıklama,cikis=Borç,giris=Alacak"),
        skip_rows=2,
    )
    rows, errors, encoding = read_rows(path, fmt)
    assert errors == [] and encoding == "cp1254"
    assert [(r.date, r.amount) for r in rows] == [(date(2026, 10, 1), Decimal("-1250.75")), (date(2026, 10, 2), Decimal("65000.00"))]
    result = import_csv(ledger, path, fmt, account="Vadesiz")
    assert result.written and result.added == 2
    assert [r.counter_account for r in result.rows] == ["Gider:Gıda:Market", "Gelir:Kategorisiz"]


def test_invert_sign_and_autodetected_delimiter(ledger, tmp_path):
    text = "tarih,aciklama,tutar\n2026-10-01,MIGROS,\"1.000,00\"\n"
    fmt = CsvFormat(delimiter=None, invert_sign=True)
    result = import_csv(ledger, write(tmp_path, text), fmt, account="Bonus")
    assert result.written and result.rows[0].amount == Decimal("-1000.00")


def test_layout_errors(tmp_path):
    with pytest.raises(FinansError, match="tarih ve aciklama"):
        read_rows(write(tmp_path, "a;b\n1;2\n"), CsvFormat())
    with pytest.raises(FinansError, match="'Tutar X' sütunu"):
        read_rows(write(tmp_path, "tarih;aciklama\n"), CsvFormat(columns={"tutar": "Tutar X"}))
    with pytest.raises(FinansError, match="tutar"):
        read_rows(write(tmp_path, "tarih;aciklama\n2026-10-01;x\n"), CsvFormat())
    with pytest.raises(FinansError):
        parse_column_map("tarih")
    with pytest.raises(FinansError, match="Bilinmeyen alan"):
        parse_column_map("tutarr=Tutar")


def test_import_key_is_stable_and_order_sensitive():
    from hane_finans.ingest.csvfile import Row

    row = Row(2, date(2026, 10, 1), "MİGROS", Decimal("-450.00"), None, None, None, None, None)
    same = Row(9, date(2026, 10, 1), "migros", Decimal("-450.0"), None, None, None, None, None)
    assert import_key("Borç:Kart", row, 1) == import_key("borc:kart", same, 1)
    assert import_key("Borç:Kart", row, 1) != import_key("Borç:Kart", row, 2)


def test_rules_priority_folding_and_recategorisation(ledger):
    s = ledger
    create_account(s, "Gider:Gıda:Restoran")
    add_rule(s, "MİGROS JET", "Restoran", priority=5)  # more specific, tried first
    rules = load_rules(s)
    assert match(rules, "migros jet kadikoy").account.path == "Gider:Gıda:Restoran"
    assert match(rules, "Migros Konya").account.path == "Gider:Gıda:Market"
    assert match(rules, "baska") is None
    with pytest.raises(FinansError):
        add_rule(s, "(", "Market", is_regex=True)
    with pytest.raises(FinansError):
        add_rule(s, "  ", "Market")

    from hane_finans.ledger.transactions import Leg, add_transaction

    add_transaction(s, date(2026, 10, 1), "BİM MARKET", [Leg("Gider:Kategorisiz", Decimal("80")), Leg("Bonus", None)])
    assert apply_rules_to_uncategorised(s) == []
    add_rule(s, "bim", "Market")
    moved = apply_rules_to_uncategorised(s)
    assert [(tx.description, acc.path) for tx, acc in moved] == [("BİM MARKET", "Gider:Gıda:Market")]
