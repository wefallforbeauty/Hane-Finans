"""Categorisation rules: "description contains X → account Y".

Matching ignores case and Turkish letters (``MİGROS`` matches the rule
``migros``). Rules with a higher priority are tried first; among equals the
older rule wins. A rule can also be a regular expression, matched against the
folded description.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from hane_finans.core.text import fold
from hane_finans.errors import FinansError, LedgerError
from hane_finans.ledger.accounts import find_account
from hane_finans.ledger.db import UNCATEGORISED_EXPENSE, UNCATEGORISED_INCOME
from hane_finans.ledger.models import Account, Posting, Rule, Transaction


@dataclass(frozen=True, slots=True)
class CompiledRule:
    rule_id: int
    account: Account
    matcher: re.Pattern | str

    def matches(self, folded_text: str) -> bool:
        if isinstance(self.matcher, str):
            return self.matcher in folded_text
        return self.matcher.search(folded_text) is not None


def add_rule(session: Session, pattern: str, account: str | Account, is_regex: bool = False, priority: int = 0) -> Rule:
    if not pattern.strip():
        raise FinansError("Kural metni boş olamaz.")
    if is_regex:
        try:
            re.compile(fold(pattern))
        except re.error as exc:
            raise FinansError(f"Geçersiz düzenli ifade: {exc}") from exc
    target = find_account(session, account)
    rule = Rule(pattern=pattern.strip(), is_regex=is_regex, account=target, priority=priority)
    session.add(rule)
    session.flush()
    return rule


def delete_rule(session: Session, rule_id: int) -> None:
    rule = session.get(Rule, rule_id)
    if rule is None:
        raise FinansError(f"Kural bulunamadı: #{rule_id}")
    session.delete(rule)


def load_rules(session: Session) -> list[CompiledRule]:
    rules = session.scalars(
        select(Rule).options(selectinload(Rule.account)).order_by(Rule.priority.desc(), Rule.id)
    )
    return [
        CompiledRule(r.id, r.account, re.compile(fold(r.pattern)) if r.is_regex else fold(r.pattern))
        for r in rules
    ]


def match(rules: list[CompiledRule], text: str) -> CompiledRule | None:
    key = fold(text)
    for rule in rules:
        if rule.matches(key):
            return rule
    return None


def apply_rules_to_uncategorised(session: Session) -> list[tuple[Transaction, Account]]:
    """Move postings from the ``Kategorisiz`` accounts to the account of the first matching rule."""
    rules = load_rules(session)
    if not rules:
        return []
    placeholders = [
        a for a in session.scalars(select(Account).where(Account.path.in_([UNCATEGORISED_EXPENSE, UNCATEGORISED_INCOME])))
    ]
    if not placeholders:
        return []
    moved: list[tuple[Transaction, Account]] = []
    postings = session.scalars(
        select(Posting).where(Posting.account_id.in_([a.id for a in placeholders])).options(
            selectinload(Posting.transaction)
        )
    )
    for posting in postings:
        tx = posting.transaction
        rule = match(rules, " ".join(filter(None, [tx.description, tx.payee])))
        if rule is None or rule.account.id == posting.account_id:
            continue
        if rule.account.commodity_code and rule.account.commodity_code != posting.commodity_code:
            raise LedgerError(
                f"Kural #{rule.rule_id} {rule.account.path} hesabına yönlendiriyor ama işlem #{tx.id} "
                f"{posting.commodity_code} biriminde."
            )
        posting.account = rule.account
        moved.append((tx, rule.account))
    session.flush()
    return moved
