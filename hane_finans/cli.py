"""Command line interface (Turkish commands). Run ``finans --help``.

The CLI is the prototype's user interface; all logic lives in the library
modules, so a web interface can reuse it later.
"""

from __future__ import annotations

import functools
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Optional

import httpx
import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from hane_finans import VERSION_LABEL, __version__, config
from hane_finans.core.dates import add_months, month_label, month_start, parse_date
from hane_finans.core.inflation import annual_pct, monthly_pct
from hane_finans.core.kinds import (
    ASSET,
    KINDS,
    LIABILITY,
    TERMS,
    TIERS,
    debt_term,
)
from hane_finans.core.money import format_amount, parse_decimal
from hane_finans.core.prices import PriceBook
from hane_finans.errors import FinansError
from hane_finans.ledger import accounts as acc_mod
from hane_finans.ledger.db import (
    backup,
    init_db,
    is_initialised,
    make_engine,
    session_scope,
)
from hane_finans.ledger.models import (
    Account,
    Commodity,
    Person,
    Posting,
    Price,
    Rule,
    Transaction,
)
from hane_finans.ledger.queries import (
    account_balance,
    commodity_totals,
    transaction_date_range,
)
from hane_finans.ledger.transactions import (
    Leg,
    add_transaction,
    delete_transaction,
    opening_balance,
    record_exchange,
    record_expense,
    record_income,
    record_transfer,
)
from hane_finans.prices.store import (
    MANUAL,
    index_sources,
    load_index,
    load_pricebook,
    price_range,
    upsert_price,
)

console = Console()
err = Console(stderr=True)

app = typer.Typer(
    name="finans",
    help=f"Hane-Finans {VERSION_LABEL}: çift taraflı defter ve dört birimle net servet (TL, reel TL, USD, gram altın).",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)
hesap_app = typer.Typer(help="Hesapları ekle, listele, kapat.", no_args_is_help=True)
islem_app = typer.Typer(help="Genel işlem ekle, listele, sil.", no_args_is_help=True)
kural_app = typer.Typer(help="Kategori kuralları (açıklama içeriyorsa → hesap).", no_args_is_help=True)
fiyat_app = typer.Typer(help="Kurlar, altın, TÜFE ve elle girilen fiyatlar.", no_args_is_help=True)
birim_app = typer.Typer(help="Birimler: para birimleri, altın, fonlar, hisseler.", no_args_is_help=True)
kisi_app = typer.Typer(help="Hesap sahipleri (aile bireyleri).", no_args_is_help=True)
ayar_app = typer.Typer(help="Ayarlar (EVDS anahtarı).", no_args_is_help=True)
for sub, name in (
    (hesap_app, "hesap"),
    (islem_app, "islem"),
    (kural_app, "kural"),
    (fiyat_app, "fiyat"),
    (birim_app, "birim"),
    (kisi_app, "kisi"),
    (ayar_app, "ayar"),
):
    app.add_typer(sub, name=name)


@dataclass
class State:
    db_path: Path
    explicit: bool


def guarded(fn):
    """Print user errors without a traceback and exit with status 1."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except FinansError as exc:
            err.print(f"[bold red]Hata:[/] {escape(str(exc))}")
            raise typer.Exit(1) from None
        except httpx.HTTPError as exc:
            err.print(f"[bold red]Ağ hatası:[/] {escape(str(exc))}")
            raise typer.Exit(2) from None

    return wrapper


@app.callback()
def _root(
    ctx: typer.Context,
    veritabani: Annotated[
        Optional[Path],
        typer.Option(
            "--veritabani",
            "-d",
            help=f"Defter dosyası. Varsayılan: ${config.ENV_DB} ya da ~/.local/share/hane-finans/defter.sqlite",
        ),
    ] = None,
) -> None:
    ctx.obj = State(veritabani.expanduser() if veritabani else config.default_db_path(), veritabani is not None)


@contextmanager
def ledger(ctx: typer.Context) -> Iterator[Session]:
    path: Path = ctx.obj.db_path
    if not path.exists():
        raise FinansError(f"Defter bulunamadı: {path}\nÖnce şunu çalıştırın: finans baslat")
    engine = make_engine(path)
    try:
        if not is_initialised(engine):
            raise FinansError(f"{path} bir Hane-Finans defteri değil.")
        with session_scope(engine) as session:
            yield session
    finally:
        engine.dispose()


def table(*columns: str, **kwargs) -> Table:
    """Rich table; a column name ending in '>' is right-aligned (numbers)."""
    t = Table(**kwargs)
    for name in columns:
        if name.endswith(">"):
            t.add_column(name[:-1], justify="right")
        else:
            t.add_column(name)
    return t


def money(value: Decimal | float | None, decimals: int = 2) -> str:
    return format_amount(value, decimals)


def amount(text: str) -> Decimal:
    return parse_decimal(text)


def day(text: str | None) -> date:
    return parse_date(text or "bugün")


def _decimals(session: Session, code: str) -> int:
    c = session.get(Commodity, code)
    return c.decimals if c else 2


# --------------------------------------------------------------------------- kurulum


@app.command()
@guarded
def baslat(ctx: typer.Context) -> None:
    """Defteri oluşturur (varsa dokunmaz) ve paketteki TÜFE kopyasını yükler."""
    from hane_finans.prices.update import load_bundled_cpi

    path: Path = ctx.obj.db_path
    existed = path.exists()
    engine = make_engine(path)
    try:
        init_db(engine)
        with session_scope(engine) as s:
            report = load_bundled_cpi(s)
    finally:
        engine.dispose()
    path.chmod(0o600)
    console.print(f"[green]Defter {'zaten vardı' if existed else 'oluşturuldu'}:[/] {path}")
    for line in report.messages:
        console.print(f"  {line}")
    console.print(
        "\nSonraki adımlar:\n"
        "  finans hesap ekle \"Varlık:Banka:Vadesiz\" --tur vadesiz --kurum \"Banka adı\"\n"
        "  finans acilis \"Varlık:Banka:Vadesiz\" 25000\n"
        "  finans fiyat guncelle\n"
        "  finans bilanco\n"
        "Denemek için örnek hane: finans ornek"
    )


@app.command()
@guarded
def durum(ctx: typer.Context) -> None:
    """Defterin özeti: dosya, sayılar, son fiyatlar, TÜFE, tutarlılık."""
    with ledger(ctx) as s:
        n_acc = s.scalar(select(func.count()).select_from(Account))
        n_tx = s.scalar(select(func.count()).select_from(Transaction))
        n_price = s.scalar(select(func.count()).select_from(Price))
        first, last = transaction_date_range(s)
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_row("Sürüm", f"{VERSION_LABEL} ({__version__})")
        table.add_row("Defter", str(ctx.obj.db_path))
        table.add_row("Hesap / işlem / fiyat", f"{n_acc} / {n_tx} / {n_price}")
        table.add_row("İşlem tarihleri", f"{first or '—'} → {last or '—'}")
        for code, quote in (("USD", "TRY"), ("EUR", "TRY"), ("XAU_G", "USD")):
            a, b = price_range(s, code, quote)
            table.add_row(f"Fiyat {code}/{quote}", f"{a or '—'} → {b or '—'}")
        index = load_index(s)
        sources = ", ".join(f"{k}: {v} ay" for k, v in index_sources(s).items()) or "yok"
        if index:
            table.add_row("TÜFE", f"{min(index):%Y-%m} → {max(index):%Y-%m} ({sources})")
        else:
            table.add_row("TÜFE", "yok")
        table.add_row("EVDS anahtarı", "var" if config.evds_api_key() else "yok (TCMB tablosu kullanılır)")
        imbalance = {c: v for c, v in commodity_totals(s).items() if v != 0}
        table.add_row(
            "Defter dengesi",
            "[green]tamam (her birimde toplam sıfır)[/]" if not imbalance else f"[red]HATA: {imbalance}[/]",
        )
        console.print(table)


# --------------------------------------------------------------------------- birim, kişi, ayar


@birim_app.command("liste")
@guarded
def birim_liste(ctx: typer.Context) -> None:
    """Tanımlı birimler."""
    with ledger(ctx) as s:
        t = Table("Kod", "Ad", "Tür", "Ondalık", "Fiyat kaynağı")
        for c in s.scalars(select(Commodity).order_by(Commodity.code)):
            t.add_row(c.code, c.name, c.kind, str(c.decimals), c.price_source)
        console.print(t)


@birim_app.command("ekle")
@guarded
def birim_ekle(
    ctx: typer.Context,
    kod: Annotated[str, typer.Argument(help="Örn. GBP, TEFAS.AFT, BIST.THYAO")],
    ad: Annotated[str, typer.Argument(help="Görünen ad")],
    tur: Annotated[str, typer.Option(help="para, altin, fon, hisse, diger")] = "diger",
    ondalik: Annotated[int, typer.Option(help="Miktardaki ondalık basamak")] = 6,
    fiyat_kaynagi: Annotated[str, typer.Option(help="tcmb (TCMB'nin yayımladığı dövizler) ya da elle")] = "elle",
) -> None:
    """Yeni birim ekler. TCMB'nin yayımladığı bir döviz için --tur para --fiyat-kaynagi tcmb --ondalik 2."""
    import re

    code = kod.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_.-]{0,31}", code):
        raise FinansError("Kod harfle başlamalı; harf, rakam, '_', '.', '-' içerebilir (en çok 32 karakter).")
    if tur not in ("para", "altin", "fon", "hisse", "diger"):
        raise FinansError("Tür: para, altin, fon, hisse ya da diger.")
    if fiyat_kaynagi not in ("tcmb", "elle"):
        raise FinansError("Fiyat kaynağı: tcmb ya da elle (TEFAS ve BIST sonraki sürümlerde).")
    if not 0 <= ondalik <= 8:
        raise FinansError("Ondalık 0–8 arasında olmalı.")
    with ledger(ctx) as s:
        if s.get(Commodity, code):
            raise FinansError(f"{code} zaten tanımlı.")
        s.add(Commodity(code=code, name=ad, kind=tur, decimals=ondalik, price_source=fiyat_kaynagi))
    console.print(f"[green]Birim eklendi:[/] {code} ({ad})")


@kisi_app.command("liste")
@guarded
def kisi_liste(ctx: typer.Context) -> None:
    """Hesap sahipleri. Sahibi olmayan hesaplar 'ortak' sayılır."""
    with ledger(ctx) as s:
        t = table("Ad", "Hesap sayısı>")
        for p in s.scalars(select(Person).order_by(Person.name)):
            n = s.scalar(select(func.count()).select_from(Account).where(Account.owner_id == p.id))
            t.add_row(p.name, str(n))
        console.print(t)


@kisi_app.command("ekle")
@guarded
def kisi_ekle(ctx: typer.Context, ad: str) -> None:
    """Kişi ekler (hesap eklerken --sahip ile de otomatik eklenir)."""
    with ledger(ctx) as s:
        if acc_mod.get_person(s, ad):
            raise FinansError(f"{ad} zaten var.")
        acc_mod.get_person(s, ad, create=True)
    console.print(f"[green]Kişi eklendi:[/] {ad}")


@ayar_app.command("evds-anahtari")
@guarded
def ayar_evds(anahtar: Annotated[str, typer.Argument(help="evds3.tcmb.gov.tr profilinizdeki API anahtarı")]) -> None:
    """EVDS API anahtarını ~/.config/hane-finans/ayarlar.toml dosyasına (yalnızca size okunur) kaydeder."""
    path = config.save_evds_api_key(anahtar)
    console.print(f"[green]Kaydedildi:[/] {path}")


@ayar_app.command("goster")
@guarded
def ayar_goster(ctx: typer.Context) -> None:
    """Dosya yolları ve ayarlar."""
    key = config.evds_api_key()
    console.print(f"Veri dizini : {config.data_dir()}")
    console.print(f"Defter      : {ctx.obj.db_path}")
    console.print(f"Ayar dosyası: {config.settings_path()}")
    console.print(f"EVDS anahtarı: {'…' + key[-4:] if key else 'yok'}")


# --------------------------------------------------------------------------- hesaplar


@hesap_app.command("turler")
def hesap_turler() -> None:
    """Varlık ve borç hesap türleri, likidite katmanları ve vadeler."""
    t = Table("Tür", "Açıklama", "Hesap", "Likidite / vade", "Varsayılan birim")
    for k in KINDS.values():
        where = "Varlık" if k.type == ASSET else "Borç"
        extra = TIERS[k.tier] if k.type == ASSET else TERMS[k.term or "uzun"]
        t.add_row(k.key, k.label, where, extra, k.default_commodity or "— (belirtilmeli)")
    console.print(t)
    console.print("Likidite katmanları: " + ", ".join(f"{i} = {label}" for i, label in TIERS.items()))


@hesap_app.command("ekle")
@guarded
def hesap_ekle(
    ctx: typer.Context,
    yol: Annotated[str, typer.Argument(help="Örn. 'Varlık:Banka:Vadesiz', 'Borç:Kredi Kartı:Bonus', 'Gider:Gıda:Market'")],
    tur: Annotated[Optional[str], typer.Option(help="Varlık/borç türü; liste için: finans hesap turler")] = None,
    birim: Annotated[Optional[str], typer.Option(help="TRY, USD, XAU_G… (varsayılan türe göre)")] = None,
    sahip: Annotated[Optional[str], typer.Option(help="Kişi adı; boş ya da 'ortak' = hanenin ortak hesabı")] = None,
    kurum: Annotated[Optional[str], typer.Option(help="Banka ya da kurum adı")] = None,
    likidite: Annotated[Optional[int], typer.Option(help="Katmanı elle ver (0–3)")] = None,
    acilis_tarihi: Annotated[Optional[str], typer.Option(help="Bu tarihten önce işlem girilemez")] = None,
    not_: Annotated[Optional[str], typer.Option("--not", help="Not")] = None,
) -> None:
    """Hesap ekler. Kök: Varlık, Borç, Özkaynak, Gelir, Gider (Türkçe harfsiz de yazılabilir)."""
    with ledger(ctx) as s:
        a = acc_mod.create_account(
            s,
            yol,
            kind=tur,
            commodity=birim,
            owner=sahip,
            institution=kurum,
            tier=likidite,
            opened_on=parse_date(acilis_tarihi) if acilis_tarihi else None,
            note=not_,
        )
        detail = ", ".join(filter(None, [a.kind, a.commodity_code, acc_mod.owner_name(a) if a.type in (ASSET, LIABILITY) else None]))
    console.print(f"[green]Hesap eklendi:[/] {a.path}" + (f" ({detail})" if detail else ""))


@hesap_app.command("liste")
@guarded
def hesap_liste(
    ctx: typer.Context,
    tarih: Annotated[Optional[str], typer.Option(help="Bakiyelerin tarihi (varsayılan bugün)")] = None,
    kapali: Annotated[bool, typer.Option(help="Kapatılmış hesapları da göster")] = False,
) -> None:
    """Hesaplar ve bakiyeleri (doğal işaretle: borçlar pozitif gösterilir)."""
    at = day(tarih)
    with ledger(ctx) as s:
        t = table("Hesap", "Tür", "Birim", "Sahip", "Kurum", "Bakiye>")
        for a in s.scalars(select(Account).options(selectinload(Account.owner)).order_by(Account.type, Account.path)):
            if a.closed_on and not kapali:
                continue
            balances = account_balance(s, a, at)
            sign = -1 if a.type in (LIABILITY, "gelir", "ozkaynak") else 1
            text = ", ".join(f"{money(sign * v, _decimals(s, c))} {c}" for c, v in sorted(balances.items())) or "0"
            t.add_row(
                escape(a.path) + (" [dim](kapalı)[/]" if a.closed_on else ""),
                a.kind or a.type,
                a.commodity_code or "çoklu",
                acc_mod.owner_name(a) if a.type in (ASSET, LIABILITY) else "",
                escape(a.institution or ""),
                text,
            )
        console.print(t)


@hesap_app.command("kapat")
@guarded
def hesap_kapat(
    ctx: typer.Context,
    hesap: str,
    tarih: Annotated[Optional[str], typer.Option(help="Kapanış tarihi (varsayılan bugün)")] = None,
) -> None:
    """Hesabı kapatır; bakiyesi sıfır olmalı."""
    at = day(tarih)
    with ledger(ctx) as s:
        a = acc_mod.find_account(s, hesap)
        if account_balance(s, a, at):
            raise FinansError(f"{a.path} bakiyesi sıfır değil; önce bakiyeyi başka hesaba aktarın.")
        later = s.scalar(
            select(func.count()).select_from(Posting).join(Transaction).where(Posting.account_id == a.id, Transaction.date > at)
        )
        if later:
            raise FinansError(f"{a.path} hesabında {at} sonrasına ait {later} işlem satırı var.")
        a.closed_on = at
        path = a.path
    console.print(f"[green]Kapatıldı:[/] {path} ({at})")


# --------------------------------------------------------------------------- işlemler


@app.command()
@guarded
def acilis(
    ctx: typer.Context,
    hesap: str,
    bakiye: Annotated[str, typer.Argument(help="Doğal işaretle: varlıkta elinizdeki, borçta borcunuz (pozitif)")],
    tarih: Annotated[Optional[str], typer.Option(help="Varsayılan bugün")] = None,
) -> None:
    """Hesabın başlangıç bakiyesini girer (karşılığı Özkaynak:Açılış Bakiyeleri)."""
    with ledger(ctx) as s:
        tx = opening_balance(s, hesap, amount(bakiye), day(tarih))
        console.print(f"[green]#{tx.id}[/] {tx.date} {escape(tx.description)}")


@app.command()
@guarded
def harcama(
    ctx: typer.Context,
    tutar: str,
    gider: Annotated[str, typer.Argument(help="Gider hesabı, örn. 'Gider:Gıda:Market' ya da kısaca 'Market'")],
    kaynak: Annotated[str, typer.Option(help="Ödemenin yapıldığı hesap (banka, nakit ya da kredi kartı)")],
    aciklama: Annotated[Optional[str], typer.Option(help="Varsayılan: gider hesabının adı")] = None,
    tarih: Annotated[Optional[str], typer.Option()] = None,
    karsi_taraf: Annotated[Optional[str], typer.Option(help="Mağaza / kişi")] = None,
) -> None:
    """Harcama: gider artar, kaynak hesap azalır (kredi kartında borç artar)."""
    with ledger(ctx) as s:
        g = acc_mod.find_account(s, gider)
        tx = record_expense(s, amount(tutar), g, kaynak, day(tarih), aciklama or g.path.split(":")[-1], karsi_taraf)
        console.print(f"[green]#{tx.id}[/] {tx.date} {escape(tx.description)}: {money(amount(tutar))}")


@app.command()
@guarded
def gelir(
    ctx: typer.Context,
    tutar: str,
    gelir_hesabi: Annotated[str, typer.Argument(metavar="GELIR", help="Gelir hesabı, örn. 'Gelir:Maaş'")],
    hedef: Annotated[str, typer.Option(help="Paranın girdiği hesap")],
    aciklama: Annotated[Optional[str], typer.Option()] = None,
    tarih: Annotated[Optional[str], typer.Option()] = None,
    karsi_taraf: Annotated[Optional[str], typer.Option()] = None,
) -> None:
    """Gelir: hedef hesap artar."""
    with ledger(ctx) as s:
        g = acc_mod.find_account(s, gelir_hesabi)
        tx = record_income(s, amount(tutar), g, hedef, day(tarih), aciklama or g.path.split(":")[-1], karsi_taraf)
        console.print(f"[green]#{tx.id}[/] {tx.date} {escape(tx.description)}: {money(amount(tutar))}")


@app.command()
@guarded
def transfer(
    ctx: typer.Context,
    tutar: str,
    kaynak: Annotated[str, typer.Option()],
    hedef: Annotated[str, typer.Option()],
    aciklama: Annotated[Optional[str], typer.Option()] = None,
    tarih: Annotated[Optional[str], typer.Option()] = None,
) -> None:
    """Aynı birimdeki iki hesap arasında aktarım (kart borcu ödemek de bir transferdir)."""
    with ledger(ctx) as s:
        tx = record_transfer(s, amount(tutar), kaynak, hedef, day(tarih), aciklama)
        console.print(f"[green]#{tx.id}[/] {tx.date} {escape(tx.description)}: {money(amount(tutar))}")


@app.command()
@guarded
def degisim(
    ctx: typer.Context,
    kaynak: Annotated[str, typer.Option(help="Ödemenin çıktığı hesap")],
    verilen: Annotated[str, typer.Option(help="Kaynak hesaptan çıkan tutar")],
    hedef: Annotated[str, typer.Option(help="Alınan birimin girdiği hesap")],
    alinan: Annotated[str, typer.Option(help="Alınan miktar (USD, gram…)")],
    aciklama: Annotated[Optional[str], typer.Option()] = None,
    tarih: Annotated[Optional[str], typer.Option()] = None,
) -> None:
    """Döviz/altın/fon alım-satımı: farklı birimler arasında değişim (takas hesaplarıyla)."""
    with ledger(ctx) as s:
        tx = record_exchange(s, kaynak, amount(verilen), hedef, amount(alinan), day(tarih), aciklama)
        console.print(f"[green]#{tx.id}[/] {tx.date} {escape(tx.description)} — {tx.note}")


def _parse_leg(text: str) -> Leg:
    """``"Gider:Market=450"``, ``"USD Hesabı=100 USD"`` or ``"Banka"`` (balancing leg)."""
    if "=" not in text:
        return Leg(text.strip(), None)
    account, rest = text.rsplit("=", 1)
    parts = rest.split()
    if not parts:
        return Leg(account.strip(), None)
    if len(parts) > 2:
        raise FinansError(f"Satır anlaşılamadı: {text!r} (biçim: HESAP=TUTAR [BİRİM])")
    return Leg(account.strip(), parse_decimal(parts[0]), parts[1].upper() if len(parts) == 2 else None)


@islem_app.command("ekle")
@guarded
def islem_ekle(
    ctx: typer.Context,
    aciklama: Annotated[str, typer.Option(help="İşlem açıklaması")],
    satir: Annotated[list[str], typer.Option("--satir", "-s", help="HESAP=TUTAR [BİRİM]; tutarsız tek satır dengeyi alır")],
    tarih: Annotated[Optional[str], typer.Option()] = None,
    karsi_taraf: Annotated[Optional[str], typer.Option()] = None,
    not_: Annotated[Optional[str], typer.Option("--not")] = None,
) -> None:
    """Genel işlem: işaretli tutarlar, her birimde toplam sıfır olmalı.

    Örnek: finans islem ekle --aciklama "Kredi taksiti" -s "İhtiyaç Kredisi=3000"
    -s "Gider:Faiz ve Masraflar=1500" -s "Vadesiz"
    """
    with ledger(ctx) as s:
        tx = add_transaction(s, day(tarih), aciklama, [_parse_leg(x) for x in satir], payee=karsi_taraf, note=not_)
        console.print(f"[green]#{tx.id}[/] {tx.date} {escape(tx.description)}")
        for p in tx.postings:
            console.print(f"   {escape(p.account.path):<45} {money(p.amount, _decimals(s, p.commodity_code)):>18} {p.commodity_code}")


@islem_app.command("liste")
@guarded
def islem_liste(
    ctx: typer.Context,
    hesap: Annotated[Optional[str], typer.Option(help="Yalnızca bu hesabın işlemleri")] = None,
    baslangic: Annotated[Optional[str], typer.Option()] = None,
    bitis: Annotated[Optional[str], typer.Option()] = None,
    son: Annotated[int, typer.Option(help="En fazla kaç işlem (en yeniler)")] = 30,
) -> None:
    """İşlemleri satırlarıyla listeler."""
    with ledger(ctx) as s:
        stmt = select(Transaction).options(selectinload(Transaction.postings).selectinload(Posting.account))
        if hesap:
            a = acc_mod.find_account(s, hesap)
            stmt = stmt.where(Transaction.postings.any(Posting.account_id == a.id))
        if baslangic:
            stmt = stmt.where(Transaction.date >= parse_date(baslangic))
        if bitis:
            stmt = stmt.where(Transaction.date <= parse_date(bitis))
        txs = list(s.scalars(stmt.order_by(Transaction.date.desc(), Transaction.id.desc()).limit(son)))
        t = table("#>", "Tarih", "Açıklama", "Hesap", "Tutar>", "Birim", "Kaynak")
        for tx in reversed(txs):
            for i, p in enumerate(tx.postings):
                t.add_row(
                    str(tx.id) if i == 0 else "",
                    str(tx.date) if i == 0 else "",
                    escape(tx.description) if i == 0 else "",
                    escape(p.account.path),
                    money(p.amount, _decimals(s, p.commodity_code)),
                    p.commodity_code,
                    tx.source if i == 0 else "",
                )
        console.print(t)


@islem_app.command("sil")
@guarded
def islem_sil(ctx: typer.Context, no: int) -> None:
    """İşlemi siler (geri alınamaz; önce 'finans yedekle' önerilir)."""
    with ledger(ctx) as s:
        tx = delete_transaction(s, no)
        console.print(f"[yellow]Silindi:[/] #{no} {tx.date} {escape(tx.description)}")


# --------------------------------------------------------------------------- içe aktarma ve kurallar


@app.command()
@guarded
def iceaktar(
    ctx: typer.Context,
    dosya: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="CSV dosyası")],
    hesap: Annotated[Optional[str], typer.Option(help="Dosyada 'hesap' sütunu yoksa tüm satırların hesabı")] = None,
    ayrac: Annotated[str, typer.Option(help="Sütun ayırıcı: ';', ',', 'tab' ya da 'oto'")] = ";",
    ondalik: Annotated[str, typer.Option(help="Ondalık ayırıcı: ',' ya da '.'")] = ",",
    tarih_bicimi: Annotated[Optional[str], typer.Option(help="Örn. %d.%m.%Y (varsayılan: 2026-10-09 ve 09.10.2026)")] = None,
    kodlama: Annotated[Optional[str], typer.Option(help="utf-8, cp1254… (varsayılan: otomatik)")] = None,
    sutunlar: Annotated[Optional[str], typer.Option(help="Eşleme: 'tarih=İşlem Tarihi,aciklama=Açıklama,tutar=Tutar'")] = None,
    atla: Annotated[int, typer.Option(help="Başlık satırından önce atlanacak satır sayısı")] = 0,
    isaret_ters: Annotated[bool, typer.Option(help="Harcamaları pozitif yazan ekstreler için")] = False,
    deneme: Annotated[bool, typer.Option(help="Hiçbir şey yazmadan önizle")] = False,
) -> None:
    """CSV içe aktarır; aynı satırlar ikinci kez eklenmez. Biçim: README → İçe aktarma."""
    from hane_finans.ingest.csvfile import CsvFormat, import_csv, parse_column_map

    delimiter = {"oto": None, "tab": "\t"}.get(ayrac, ayrac)
    fmt = CsvFormat(
        delimiter=delimiter,
        decimal_sep=ondalik,
        date_format=tarih_bicimi,
        encoding=kodlama,
        columns=parse_column_map(sutunlar) if sutunlar else {},
        skip_rows=atla,
        invert_sign=isaret_ters,
    )
    with ledger(ctx) as s:
        result = import_csv(s, dosya, fmt, account=hesap, dry_run=deneme)
        t = table("Satır>", "Tarih", "Açıklama", "Tutar>", "Hesap", "Karşı hesap", "Nasıl", "")
        for r in result.rows[:200]:
            t.add_row(
                str(r.line),
                str(r.date),
                escape(r.description[:40]),
                f"{money(r.amount)} {r.commodity}",
                escape(r.account),
                escape(r.counter_account),
                r.how,
                "[dim]zaten var[/]" if r.duplicate else "",
            )
        console.print(t)
        if len(result.rows) > 200:
            console.print(f"… {len(result.rows) - 200} satır daha")
        console.print(f"Kodlama: {result.encoding}")
        if result.errors:
            for e in result.errors:
                err.print(f"[red]{escape(e)}[/]")
            raise FinansError(f"{len(result.errors)} hatalı satır var; hiçbir şey yazılmadı.")
        new = len(result.rows) - result.duplicates
        if deneme:
            console.print(f"[yellow]Deneme:[/] {new} yeni, {result.duplicates} tekrar; hiçbir şey yazılmadı.")
        else:
            console.print(
                f"[green]{result.added} işlem eklendi[/], {result.duplicates} satır zaten vardı, "
                f"{result.uncategorised} kategorisiz (kural ekleyip 'finans kural uygula' çalıştırabilirsiniz)."
            )


@kural_app.command("ekle")
@guarded
def kural_ekle(
    ctx: typer.Context,
    metin: Annotated[str, typer.Argument(help="Açıklamada aranacak metin (büyük/küçük harf ve Türkçe harf fark etmez)")],
    hesap: Annotated[str, typer.Argument(help="Eşleşen işlemin karşı hesabı")],
    regex: Annotated[bool, typer.Option(help="Metin bir düzenli ifade")] = False,
    oncelik: Annotated[int, typer.Option(help="Büyük olan önce denenir")] = 0,
) -> None:
    """Kategori kuralı ekler."""
    from hane_finans.ingest.rules import add_rule

    with ledger(ctx) as s:
        r = add_rule(s, metin, hesap, regex, oncelik)
        console.print(f"[green]Kural #{r.id}:[/] '{r.pattern}' → {r.account.path}")


@kural_app.command("liste")
@guarded
def kural_liste(ctx: typer.Context) -> None:
    """Kurallar, denenme sırasıyla."""
    with ledger(ctx) as s:
        t = Table("#", "Metin", "Regex", "Öncelik", "Hesap")
        for r in s.scalars(select(Rule).options(selectinload(Rule.account)).order_by(Rule.priority.desc(), Rule.id)):
            t.add_row(str(r.id), escape(r.pattern), "evet" if r.is_regex else "", str(r.priority), escape(r.account.path))
        console.print(t)


@kural_app.command("sil")
@guarded
def kural_sil(ctx: typer.Context, no: int) -> None:
    """Kuralı siler."""
    from hane_finans.ingest.rules import delete_rule

    with ledger(ctx) as s:
        delete_rule(s, no)
    console.print(f"[yellow]Kural #{no} silindi.[/]")


@kural_app.command("uygula")
@guarded
def kural_uygula(ctx: typer.Context) -> None:
    """Kategorisiz işlemlere kuralları uygular."""
    from hane_finans.ingest.rules import apply_rules_to_uncategorised

    with ledger(ctx) as s:
        moved = apply_rules_to_uncategorised(s)
        for tx, target in moved:
            console.print(f"#{tx.id} {tx.date} {escape(tx.description)} → {escape(target.path)}")
        console.print(f"[green]{len(moved)} işlem kategorilendi.[/]")


# --------------------------------------------------------------------------- fiyatlar ve TÜFE


@fiyat_app.command("guncelle")
@guarded
def fiyat_guncelle(
    ctx: typer.Context,
    baslangic: Annotated[Optional[str], typer.Option(help="Varsayılan: ilk işlem tarihi (yoksa 30 gün önce)")] = None,
    bitis: Annotated[Optional[str], typer.Option(help="Varsayılan: bugün")] = None,
    sadece: Annotated[Optional[str], typer.Option(help="doviz, altin ya da tufe")] = None,
) -> None:
    """TCMB kurları, altın (Frankfurter) ve TÜFE'yi indirir; eksik günleri tamamlar."""
    from hane_finans.prices.http import make_client
    from hane_finans.prices.update import (
        UpdateReport,
        tcmb_currencies,
        update_cpi,
        update_fx,
        update_gold,
    )

    if sadece not in (None, "doviz", "altin", "tufe"):
        raise FinansError("--sadece: doviz, altin ya da tufe.")
    today = date.today()
    key = config.evds_api_key()
    with ledger(ctx) as s:
        first, _ = transaction_date_range(s)
        start = parse_date(baslangic) if baslangic else (first or today - timedelta(days=30))
        end = parse_date(bitis) if bitis else today
        if end < start:
            raise FinansError("Bitiş başlangıçtan önce olamaz.")
        report = UpdateReport()
        with make_client() as client, console.status("Fiyatlar indiriliyor…") as status:
            if sadece in (None, "doviz"):
                report.merge(update_fx(s, client, start, end, tcmb_currencies(s), key, progress=status.update))
                s.commit()
            if sadece in (None, "altin"):
                report.merge(update_gold(s, client, start, end, progress=status.update))
                s.commit()
            if sadece in (None, "tufe"):
                status.update("TÜFE")
                report.merge(update_cpi(s, client, key, today))
                s.commit()
    for line in report.messages:
        console.print(f"  {line}")
    for line in report.warnings[:20]:
        err.print(f"  [yellow]{line}[/]")
    console.print(f"[green]{report.added} yeni, {report.updated} güncellenen kayıt.[/]")


@fiyat_app.command("ekle")
@guarded
def fiyat_ekle(
    ctx: typer.Context,
    birim: str,
    deger: Annotated[str, typer.Argument(help="Bir birimin fiyatı")],
    tarih: Annotated[Optional[str], typer.Option()] = None,
    para: Annotated[str, typer.Option(help="Fiyatın birimi")] = "TRY",
) -> None:
    """Elle fiyat girer (fon, hisse, gayrimenkul…). Otomatik güncelleme elle girileni ezmez."""
    code, quote = birim.strip().upper(), para.strip().upper()
    with ledger(ctx) as s:
        for c in (code, quote):
            if s.get(Commodity, c) is None:
                raise FinansError(f"Bilinmeyen birim: {c}")
        value = amount(deger)
        if value <= 0:
            raise FinansError("Fiyat pozitif olmalı.")
        status = upsert_price(s, code, quote, day(tarih), value, MANUAL)
    console.print(f"[green]{code}/{quote} {day(tarih)} = {deger} ({status})[/]")


@fiyat_app.command("liste")
@guarded
def fiyat_liste(
    ctx: typer.Context,
    birim: str,
    para: Annotated[Optional[str], typer.Option(help="Varsayılan: TRY (altında USD)")] = None,
    son: Annotated[int, typer.Option()] = 15,
) -> None:
    """Bir birimin son fiyatları."""
    code = birim.strip().upper()
    quote = (para or ("USD" if code == "XAU_G" else "TRY")).upper()
    with ledger(ctx) as s:
        rows = list(
            s.scalars(
                select(Price)
                .where(Price.commodity_code == code, Price.quote_code == quote)
                .order_by(Price.date.desc())
                .limit(son)
            )
        )
        t = table("Tarih", f"{code}/{quote}>", "Kaynak")
        for p in reversed(rows):
            t.add_row(str(p.date), money(p.value, 6), p.source)
        console.print(t)
        if code == "XAU_G":
            book = load_pricebook(s)
            r = book.resolve("XAU_G", date.today())
            if r:
                console.print(f"Gram altın bugün: {money(r.value, 2)} TL (son veri {r.as_of})")


@app.command()
@guarded
def tufe(
    ctx: typer.Context,
    son: Annotated[int, typer.Option(help="Kaç ay")] = 13,
) -> None:
    """TÜFE (2025=100): seviye, aylık ve yıllık değişim, kaynak."""
    with ledger(ctx) as s:
        index = load_index(s)
        if not index:
            raise FinansError("TÜFE yok: 'finans baslat' ya da 'finans fiyat guncelle --sadece tufe'.")
        mo, an = monthly_pct(index), annual_pct(index)
        t = table("Ay", "Endeks (2025=100)>", "Aylık %>", "Yıllık %>")
        for m in sorted(index)[-son:]:
            t.add_row(month_label(m), money(index[m], 2), money(mo.get(m), 2), money(an.get(m), 2))
        console.print(t)
        console.print("Kaynak: " + ", ".join(f"{k} ({v} ay)" for k, v in index_sources(s).items()))


# --------------------------------------------------------------------------- raporlar


SOURCE_LABELS = {
    "tcmb": "TCMB döviz alış",
    "evds": "EVDS döviz alış",
    "frankfurter": "Frankfurter XAU/USD",
    "elle": "elle girildi",
    "sentetik": "uydurma (örnek)",
}


def _price_note(book: PriceBook, code: str, at: date) -> str:
    r = book.resolve(code, at)
    if r is None:
        return "fiyat yok"
    sources = " × ".join(dict.fromkeys(SOURCE_LABELS.get(q.source, q.source) for q in r.path))
    unit = "TL/g" if code == "XAU_G" else "TL"
    return f"{money(r.value, 4 if code == 'USD' else 2)} {unit} · {r.as_of:%d.%m.%Y} · {sources}"


@app.command()
@guarded
def bilanco(
    ctx: typer.Context,
    tarih: Annotated[Optional[str], typer.Option(help="Varsayılan bugün")] = None,
    kisi: Annotated[Optional[str], typer.Option(help="Yalnızca bu kişinin hesapları; 'ortak' = ortak hesaplar")] = None,
    tufe_yontemi: Annotated[str, typer.Option(help="interpolasyon ya da aylik")] = "interpolasyon",
) -> None:
    """Kişisel bilanço: varlıklar, borçlar, dört birimle net servet, likidite."""
    from hane_finans.reports.balance_sheet import build_balance_sheet
    from hane_finans.reports.context import load_market_data

    at = day(tarih)
    with ledger(ctx) as s:
        market = load_market_data(s, tufe_yontemi)
        bs = build_balance_sheet(s, at, owner=kisi, market=market)
        title = f"Bilanço · {at:%d.%m.%Y}" + (f" · {kisi}" if kisi else "")
        if bs.empty:
            console.print(f"[bold]{title}[/]\nHenüz bakiyesi olan hesap yok. 'finans hesap ekle' ve 'finans acilis' ile başlayın.")
            return
        console.print(f"[bold]{title}[/]")

        a = table("Varlık", "Birim", "Miktar>", "Fiyat (TL)>", "Değer (TL)>", "Likidite", "Sahip")
        for v in bs.assets:
            p = v.position
            price = "—" if v.price is None else ("" if p.commodity == "TRY" else money(v.price.value, 4))
            if v.stale:
                price += f" [yellow](bayat: {v.price.as_of:%d.%m})[/]"
            a.add_row(
                escape(p.account),
                p.commodity,
                money(p.quantity, _decimals(s, p.commodity)),
                price,
                "[red]fiyat yok[/]" if v.value is None else money(v.value),
                TIERS.get(p.tier, ""),
                escape(p.owner or ""),
            )
        a.add_section()
        a.add_row("[bold]Toplam varlık[/]", "", "", "", f"[bold]{money(bs.net_worth.assets)}[/]", "", "")
        console.print(a)

        if bs.liabilities:
            b = table("Borç", "Birim", "Borç tutarı>", "Değer (TL)>", "Vade", "Sahip")
            for v in bs.liabilities:
                p = v.position
                b.add_row(
                    escape(p.account),
                    p.commodity,
                    money(-p.quantity, _decimals(s, p.commodity)),
                    "[red]fiyat yok[/]" if v.value is None else money(-v.value),
                    TERMS[debt_term(p.kind)],
                    p.owner or "",
                )
            b.add_section()
            b.add_row("[bold]Toplam borç[/]", "", "", f"[bold]{money(bs.net_worth.liabilities)}[/]", "", "")
            console.print(b)

        nw = bs.net_worth
        u = nw.net
        conv = nw.converter
        lines = [f"[bold]TL            {money(u.try_):>20}[/]"]
        if u.real_try is not None and market.base_month:
            note = f"{month_label(market.base_month)} fiyatlarıyla"
            if at < market.deflator.first_month:
                note += f"; TÜFE {month_label(market.deflator.first_month)} başlıyor, öncesi için ilk ay kullanıldı"
            elif conv.cpi_extrapolated:
                note += "; sonrası için TÜFE henüz yok"
            lines.append(f"Reel TL       {money(u.real_try):>20}   [dim]{note}[/]")
        else:
            lines.append("Reel TL       [dim]TÜFE yok (finans fiyat guncelle --sadece tufe)[/]")
        lines.append(
            f"USD           {money(u.usd):>20}   [dim]{_price_note(market.book, 'USD', at)}[/]"
            if u.usd is not None
            else "USD           [dim]USD kuru yok (finans fiyat guncelle)[/]"
        )
        lines.append(
            f"Gram altın    {money(u.gold_g):>18} g   [dim]{_price_note(market.book, 'XAU_G', at)}[/]"
            if u.gold_g is not None
            else "Gram altın    [dim]altın fiyatı yok (finans fiyat guncelle)[/]"
        )
        console.print(Panel("\n".join(lines), title="Net servet", title_align="left", expand=False))

        liq = bs.liquidity
        t = table("Likidite", "TL>")
        for tier, value in liq.by_tier.items():
            t.add_row(TIERS[tier], money(value))
        t.add_section()
        t.add_row("Likit varlık (≤ 1 hafta)", money(liq.liquid_assets))
        t.add_row("Kısa vadeli borç", money(liq.short_term_debt))
        t.add_row("[bold]Likit net varlık[/]", f"[bold]{money(liq.liquid_net)}[/]")
        console.print(t)

        if not kisi and len(bs.by_owner) > 1:
            o = table("Sahip", "Net (TL)>")
            for owner_, value in sorted(bs.by_owner.items()):
                o.add_row(owner_, money(value))
            console.print(o)

        for item in nw.missing:
            err.print(f"[red]Fiyatı olmayan, net servete katılmadı:[/] {escape(item)} → 'finans fiyat ekle' ya da 'finans fiyat guncelle'")
        for item in nw.stale:
            err.print(f"[yellow]Fiyatı 7 günden eski:[/] {escape(item)}")


@app.command()
@guarded
def servet(
    ctx: typer.Context,
    baslangic: Annotated[Optional[str], typer.Option(help="Varsayılan: ilk işlem tarihi")] = None,
    bitis: Annotated[Optional[str], typer.Option(help="Varsayılan: bugün")] = None,
    siklik: Annotated[str, typer.Option(help="ay, hafta ya da gun")] = "ay",
    kisi: Annotated[Optional[str], typer.Option()] = None,
    csv_dosyasi: Annotated[Optional[Path], typer.Option("--csv", help="Tabloyu CSV olarak da yaz")] = None,
    tufe_yontemi: Annotated[str, typer.Option()] = "interpolasyon",
) -> None:
    """Net servetin zaman içindeki seyri (TL, reel TL, USD, gram altın)."""
    from hane_finans.reports.context import load_market_data
    from hane_finans.reports.networth import networth_series

    with ledger(ctx) as s:
        market = load_market_data(s, tufe_yontemi)
        frame = networth_series(
            s,
            parse_date(baslangic) if baslangic else None,
            parse_date(bitis) if bitis else None,
            siklik,
            kisi,
            market,
        )
    if frame.empty:
        console.print("Henüz işlem yok.")
        return
    base = f" ({month_label(market.base_month)} fiyatlarıyla)" if market.base_month else ""
    t = table("Tarih", "TL>", f"Reel TL{base}>", "USD>", "Gram altın>", "Varlık>", "Borç>", "Likit net>")
    for r in frame.itertuples(index=False):
        flag = " [red]*[/]" if r.eksik_fiyat else ""
        t.add_row(
            f"{r.tarih:%d.%m.%Y}{flag}",
            f"[bold]{money(r.tl)}[/]",
            money(r.reel_tl),
            money(r.usd),
            money(r.altin_g),
            money(r.varlik),
            money(r.borc),
            money(r.likit_net),
        )
    console.print(t)
    if frame["eksik_fiyat"].any():
        err.print("[red]*[/] Bazı varlıkların fiyatı yok; o tarihlerde net servet eksik.")
    if csv_dosyasi:
        frame.to_csv(csv_dosyasi, index=False)
        console.print(f"CSV: {csv_dosyasi}")


@app.command()
@guarded
def grafik(
    ctx: typer.Context,
    dosya: Annotated[Optional[Path], typer.Option(help="PNG yolu (varsayılan: veri dizini/cikti/net_servet.png)")] = None,
    baslangic: Annotated[Optional[str], typer.Option()] = None,
    bitis: Annotated[Optional[str], typer.Option()] = None,
    siklik: Annotated[str, typer.Option(help="ay, hafta ya da gun")] = "hafta",
    kisi: Annotated[Optional[str], typer.Option()] = None,
) -> None:
    """Net servet grafiği (PNG): üstte TL, altta reel TL, USD, gram altın."""
    from hane_finans.reports.chart import save_networth_chart
    from hane_finans.reports.context import load_market_data
    from hane_finans.reports.networth import networth_series

    with ledger(ctx) as s:
        market = load_market_data(s)
        frame = networth_series(
            s, parse_date(baslangic) if baslangic else None, parse_date(bitis) if bitis else None, siklik, kisi, market
        )
    if frame.empty:
        raise FinansError("Henüz işlem yok.")
    target = dosya or config.data_dir() / "cikti" / "net_servet.png"
    subtitle = f"{frame['tarih'].iloc[0]:%d.%m.%Y} – {frame['tarih'].iloc[-1]:%d.%m.%Y}" + (f" · {kisi}" if kisi else "")
    save_networth_chart(frame, target, market.base_month, subtitle)
    console.print(f"[green]Grafik:[/] {target}")


# --------------------------------------------------------------------------- yedek ve örnek


@app.command()
@guarded
def yedekle(
    ctx: typer.Context,
    hedef: Annotated[Optional[Path], typer.Option(help="Yedek dizini (varsayılan: veri dizini/yedek)")] = None,
) -> None:
    """Defterin tutarlı bir kopyasını alır (SQLite yedekleme API'si)."""
    path: Path = ctx.obj.db_path
    if not path.exists():
        raise FinansError(f"Defter bulunamadı: {path}")
    target = backup(path, hedef or config.data_dir() / "yedek")
    console.print(f"[green]Yedek:[/] {target}")


@app.command()
@guarded
def ornek(
    ctx: typer.Context,
    baslangic: Annotated[Optional[str], typer.Option(help="Varsayılan: 12 ay önce")] = None,
    bitis: Annotated[Optional[str], typer.Option(help="Varsayılan bugün")] = None,
    sentetik: Annotated[bool, typer.Option(help="İnternetsiz: uydurma kur ve altın fiyatları")] = False,
    uzerine_yaz: Annotated[bool, typer.Option(help="Var olan örnek defteri silip yeniden oluştur")] = False,
) -> None:
    """Uydurma bir hane ile ayrı bir örnek defter oluşturur (gerçek defterinize dokunmaz)."""
    from hane_finans.demo import build_demo, synthetic_prices
    from hane_finans.prices.http import make_client
    from hane_finans.prices.update import load_bundled_cpi, update_fx, update_gold
    from hane_finans.reports.context import load_market_data

    real_ledger = config.default_db_path().resolve()
    path: Path = ctx.obj.db_path if ctx.obj.explicit else config.data_dir() / "ornek.sqlite"
    if path.resolve() == real_ledger:
        raise FinansError("Örnek veri gerçek deftere yazılmaz; başka bir --veritabani verin.")
    if path.exists():
        if not uzerine_yaz:
            raise FinansError(f"{path} zaten var. Yeniden oluşturmak için --uzerine-yaz.")
        path.unlink()
    end = day(bitis)
    start = parse_date(baslangic) if baslangic else add_months(month_start(end), -12)
    engine = make_engine(path)
    try:
        init_db(engine)
        with session_scope(engine) as s:
            load_bundled_cpi(s)
            source = "sentetik"
            if not sentetik:
                try:
                    with make_client() as client, console.status("Gerçek kur ve altın fiyatları indiriliyor…") as st:
                        update_fx(
                            s, client, start - timedelta(days=7), end, ["USD"], config.evds_api_key(), pause=0.1, progress=st.update
                        )
                        update_gold(s, client, start - timedelta(days=7), end, progress=st.update)
                    source = "TCMB + Frankfurter"
                except (httpx.HTTPError, FinansError) as exc:
                    err.print(f"[yellow]İndirme başarısız ({escape(str(exc))}); uydurma fiyatlar kullanılıyor.[/]")
                    s.rollback()
                    load_bundled_cpi(s)
                    sentetik = True
            if sentetik:
                for q in synthetic_prices(start - timedelta(days=7), end):
                    upsert_price(s, q.commodity, q.quote, q.on, q.value, q.source)
            s.flush()
            market = load_market_data(s)
            summary = build_demo(s, start, end, market.book, market.deflator)
    finally:
        engine.dispose()
    path.chmod(0o600)
    console.print(
        f"[green]Örnek defter:[/] {path}\n  {summary.start} – {summary.end}, {summary.transactions} işlem, fiyatlar: {source}\n"
        f"Görmek için:\n  finans -d {path} bilanco\n  finans -d {path} servet\n  finans -d {path} grafik"
    )


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
