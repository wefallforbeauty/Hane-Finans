"""The command line interface, end to end on temporary ledgers (no network)."""

import csv
from importlib import resources

import httpx
import pytest
from typer.testing import CliRunner

from hane_finans import config
from hane_finans.cli import app
from hane_finans.prices import http as http_module

runner = CliRunner(env={"COLUMNS": "200"})  # wide enough that tables do not wrap


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "defter.sqlite")


def run(db, *args):
    result = runner.invoke(app, ["-d", db, *args])
    return result


def ok(db, *args):
    result = run(db, *args)
    assert result.exit_code == 0, result.output
    return result.output


def test_full_flow(db, tmp_path):
    assert "oluşturuldu" in ok(db, "baslat")
    assert "zaten vardı" in ok(db, "baslat")
    ok(db, "hesap", "ekle", "Varlık:Banka:Vadesiz", "--tur", "vadesiz", "--sahip", "Ben", "--kurum", "Banka A")
    ok(db, "hesap", "ekle", "Varlık:Döviz:USD Hesabı", "--tur", "doviz", "--birim", "USD")
    ok(db, "hesap", "ekle", "Borç:Kredi Kartı:Bonus", "--tur", "kredi_karti")
    ok(db, "hesap", "ekle", "Gider:Gıda:Market")
    ok(db, "hesap", "ekle", "Gelir:Maaş")
    ok(db, "acilis", "Vadesiz", "100.000,00", "--tarih", "2026-10-01")
    ok(db, "acilis", "Bonus", "2500", "--tarih", "2026-10-01")
    ok(db, "gelir", "65000", "Maaş", "--hedef", "Vadesiz", "--tarih", "2026-10-02")
    ok(db, "harcama", "450,5", "market", "--kaynak", "bonus", "--tarih", "2026-10-03")
    ok(db, "transfer", "2950,50", "--kaynak", "Vadesiz", "--hedef", "Bonus", "--tarih", "2026-10-04")
    out = ok(db, "degisim", "--kaynak", "Vadesiz", "--verilen", "49126,70", "--hedef", "USD Hesabı", "--alinan", "1000", "--tarih", "2026-10-08")
    assert "Kur: 1 USD = 49,126700 TRY" in out
    ok(db, "islem", "ekle", "--aciklama", "Elle", "-s", "Market=10", "-s", "Vadesiz", "--tarih", "2026-10-08")
    ok(db, "fiyat", "ekle", "USD", "49,1267", "--tarih", "2026-10-08")
    ok(db, "fiyat", "ekle", "XAU_G", "130", "--tarih", "2026-10-08", "--para", "USD")

    out = ok(db, "bilanco", "--tarih", "2026-10-08")
    assert "Net servet" in out and "Reel TL" in out and "USD" in out and "Gram altın" in out
    # 100 000 + 65 000 − 2 950,50 − 49 126,70 − 10 = 112 912,80 TL plus 1 000 USD × 49,1267
    assert "162.039,50" in out

    out = ok(db, "durum")
    assert "tamam" in out
    assert "Vadesiz" in ok(db, "hesap", "liste", "--tarih", "2026-10-08")
    assert "Elle" in ok(db, "islem", "liste")
    assert "Eylül 2026" in ok(db, "tufe", "--son", "3")
    out = ok(db, "servet", "--baslangic", "2026-10-01", "--bitis", "2026-10-08", "--siklik", "gun", "--csv", str(tmp_path / "s.csv"))
    assert (tmp_path / "s.csv").exists()
    ok(db, "grafik", "--bitis", "2026-10-08", "--dosya", str(tmp_path / "g.png"))
    assert (tmp_path / "g.png").exists()
    assert "Yedek" in ok(db, "yedekle", "--hedef", str(tmp_path / "yedek"))


def test_errors_are_short_messages(db):
    result = run(db, "bilanco")
    assert result.exit_code == 1 and "finans baslat" in result.output
    ok(db, "baslat")
    result = run(db, "harcama", "10", "Yok", "--kaynak", "Vadesiz")
    assert result.exit_code == 1 and "Hesap bulunamadı" in result.output
    result = run(db, "hesap", "ekle", "Banka:X")
    assert result.exit_code == 1 and "biriyle başlamalı" in result.output
    result = run(db, "islem", "ekle", "--aciklama", "x", "-s", "Gider:Kategorisiz=5", "-s", "Gelir:Kategorisiz=4")
    assert result.exit_code == 1 and "dengede değil" in result.output


def test_import_preview_and_rules(db, tmp_path):
    ok(db, "baslat")
    ok(db, "hesap", "ekle", "Borç:Kredi Kartı:Bonus", "--tur", "kredi_karti")
    ok(db, "hesap", "ekle", "Gider:Gıda:Market")
    path = tmp_path / "e.csv"
    path.write_text("tarih;aciklama;tutar\n2026-10-01;MİGROS;-450,00\n2026-10-02;BİM;-80\n", encoding="utf-8")
    out = ok(db, "iceaktar", str(path), "--hesap", "Bonus", "--deneme")
    assert "Deneme" in out and "2 yeni" in out
    out = ok(db, "iceaktar", str(path), "--hesap", "Bonus")
    assert "2 işlem eklendi" in out and "2 kategorisiz" in out
    out = ok(db, "iceaktar", str(path), "--hesap", "Bonus")
    assert "0 işlem eklendi" in out and "2 satır zaten vardı" in out
    ok(db, "kural", "ekle", "migros", "Market")
    assert "migros" in ok(db, "kural", "liste")
    assert "1 işlem kategorilendi" in ok(db, "kural", "uygula")


def test_price_update_with_mocked_sources(db, monkeypatch):
    text = resources.files("hane_finans.data").joinpath("tufe_tcmb_aylik.csv").read_text(encoding="utf-8")
    rows = list(csv.DictReader(text.splitlines()))
    page = "<table>" + "".join(
        f"<tr><td>{r['ay'][5:]}-{r['ay'][:4]}</td><td>{r['yillik_degisim']}</td><td>{r['aylik_degisim']}</td></tr>" for r in rows
    ) + "</table>"
    real_make_client = http_module.make_client
    monkeypatch.setattr(
        http_module, "make_client", lambda: real_make_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=page)))
    )
    ok(db, "baslat")
    out = ok(db, "fiyat", "guncelle", "--sadece", "tufe")
    assert "TCMB tablosu" in out


def test_demo_goes_to_a_separate_ledger(tmp_path, monkeypatch):
    out = runner.invoke(app, ["ornek", "--sentetik", "--baslangic", "2026-01-01", "--bitis", "2026-03-31"])
    assert out.exit_code == 0, out.output
    demo_path = config.data_dir() / "ornek.sqlite"
    assert demo_path.exists()
    assert not config.default_db_path().exists()  # the real ledger was not created
    again = runner.invoke(app, ["ornek", "--sentetik"])
    assert again.exit_code == 1 and "--uzerine-yaz" in again.output
    real = runner.invoke(app, ["-d", str(config.default_db_path()), "ornek", "--sentetik"])
    assert real.exit_code == 1 and "gerçek deftere" in real.output


def test_settings_key_file_is_private(tmp_path):
    result = runner.invoke(app, ["ayar", "evds-anahtari", "abc123"])
    assert result.exit_code == 0, result.output
    path = config.settings_path()
    assert path.stat().st_mode & 0o777 == 0o600
    assert config.evds_api_key() == "abc123"


def test_bank_text_with_brackets_is_not_markup(db, tmp_path):
    ok(db, "baslat")
    ok(db, "hesap", "ekle", "Varlık:Banka:Vadesiz", "--tur", "vadesiz")
    ok(db, "harcama", "5", "Gider:Kategorisiz", "--kaynak", "Vadesiz", "--aciklama", "[POS] MIGROS [/x] [bold]")
    assert "[POS] MIGROS [/x] [bold]" in ok(db, "islem", "liste")


def test_broken_settings_file_is_a_clear_error(db):
    path = config.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[evds\napi_key = ", encoding="utf-8")
    result = run(db, "ayar", "goster")
    assert result.exit_code == 1 and "Ayar dosyası okunamadı" in result.output
