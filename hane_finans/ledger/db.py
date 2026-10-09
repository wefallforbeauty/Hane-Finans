"""Engine, sessions, schema creation and seed data."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from hane_finans import __version__
from hane_finans.core.kinds import EQUITY, EXPENSE, INCOME
from hane_finans.ledger.models import Account, Base, Commodity, Meta

SCHEMA_VERSION = "1"

DEFAULT_COMMODITIES = (
    # code, name, kind, decimals, price source
    ("TRY", "Türk lirası", "para", 2, "sabit"),
    ("USD", "ABD doları", "para", 2, "tcmb"),
    ("EUR", "Euro", "para", 2, "tcmb"),
    ("XAU_G", "Gram altın (24 ayar, has)", "altin", 4, "altin"),
)

OPENING_ACCOUNT = "Özkaynak:Açılış Bakiyeleri"
TRADING_ROOT = "Özkaynak:Takas"
UNCATEGORISED_EXPENSE = "Gider:Kategorisiz"
UNCATEGORISED_INCOME = "Gelir:Kategorisiz"

DEFAULT_ACCOUNTS = (
    (OPENING_ACCOUNT, EQUITY),
    (UNCATEGORISED_EXPENSE, EXPENSE),
    (UNCATEGORISED_INCOME, INCOME),
)


def make_engine(path: Path | str, echo: bool = False) -> Engine:
    """SQLite engine with foreign keys enforced. ``":memory:"`` for tests."""
    if str(path) == ":memory:":
        url = "sqlite+pysqlite:///:memory:"
    else:
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+pysqlite:///{p}"
    engine = create_engine(url, echo=echo)

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def init_db(engine: Engine) -> None:
    """Create the tables and seed data. Safe to run again."""
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        if s.get(Meta, "schema_version") is None:
            s.add(Meta(key="schema_version", value=SCHEMA_VERSION))
            s.add(Meta(key="created_with", value=__version__))
            s.add(Meta(key="created_at", value=datetime.now().isoformat(timespec="seconds")))
        for code, name, kind, decimals, source in DEFAULT_COMMODITIES:
            if s.get(Commodity, code) is None:
                s.add(Commodity(code=code, name=name, kind=kind, decimals=decimals, price_source=source))
        s.flush()
        existing = set(s.scalars(select(Account.path)))
        for path, account_type in DEFAULT_ACCOUNTS:
            if path not in existing:
                s.add(Account(path=path, type=account_type))


def is_initialised(engine: Engine) -> bool:
    with engine.connect() as conn:
        rows = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name='meta'")
        return rows.first() is not None


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Commit on success, roll back on any error."""
    factory = sessionmaker(engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def backup(db_path: Path, target_dir: Path) -> Path:
    """Consistent copy through SQLite's online backup API."""
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = target_dir / f"{db_path.stem}-{stamp}.sqlite"
    with closing(sqlite3.connect(db_path)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
    target.chmod(0o600)
    return target
