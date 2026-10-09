"""Exceptions with user-facing (Turkish) messages.

The CLI prints the message of any ``FinansError`` without a traceback.
"""


class FinansError(Exception):
    """Base class for errors the user can fix."""


class AmountError(FinansError, ValueError):
    """An amount could not be parsed or has too many decimals."""


class DateError(FinansError, ValueError):
    """A date could not be parsed."""


class AccountError(FinansError):
    """Invalid account path, kind or commodity."""


class AccountNotFound(AccountError):
    pass


class AmbiguousAccount(AccountError):
    def __init__(self, query: str, candidates: list[str]):
        self.candidates = candidates
        shown = "\n  ".join(candidates[:10])
        more = f"\n  … ve {len(candidates) - 10} hesap daha" if len(candidates) > 10 else ""
        super().__init__(f"'{query}' birden fazla hesapla eşleşiyor:\n  {shown}{more}")


class LedgerError(FinansError):
    """A transaction breaks a ledger rule."""


class UnbalancedTransaction(LedgerError):
    pass


class DuplicateTransaction(LedgerError):
    pass


class SourceError(FinansError):
    """A data source returned an error or unexpected content."""


class EvdsKeyError(SourceError):
    pass
