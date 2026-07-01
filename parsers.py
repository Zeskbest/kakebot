"""Per-bank parsers that turn a bank-notification email into a ParsedPayment.

Parsing is regex-based, one parser per sender address. Emails carry both a Thai
and an English section; we key off the English labels (e.g. "Amount (THB):")
which are unambiguous. Amounts are THB decimals but the app stores integers, so
we round to the nearest whole unit and keep the original string in `raw_amount`.
"""
import html
import re
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Callable

from mail_worker import MailMessage

CURRENCY = "THB"

# English amount label, shared by every bank ("Amount (THB): 1,207.21").
AMOUNT_RE = re.compile(r"Amount\s*\(THB\)\s*:\s*([\d,]+\.\d{2})", re.I)


@dataclass
class ParsedPayment:
    amount: int              # rounded to whole THB (app stores integers)
    merchant: str
    currency: str = CURRENCY
    kind: str | None = None  # "bill_payment" | "transfer"
    external_ref: str | None = None
    raw_amount: str | None = None  # original "1,207.21" for audit/comment

    def __str__(self) -> str:
        return (f"ParsedPayment(amount={self.amount}, merchant={self.merchant!r}, "
                f"kind={self.kind}, ref={self.external_ref}, raw={self.raw_amount})")


def _flatten(text: str) -> str:
    """Strip HTML tags, unescape entities, collapse all whitespace to spaces.

    This normalises both the plain-text (K PLUS) and HTML (Krungsri) bodies so
    label/value pairs sit on a single line regardless of the original wrapping.
    """
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _amount_to_int(raw: str) -> int:
    return int(round(float(raw.replace(",", ""))))


def _is_success(mail: MailMessage) -> bool:
    return "success" in (mail.subject or "").lower()


def parse_kplus(mail: MailMessage) -> ParsedPayment | None:
    """Kasikornbank / K PLUS — plain text, Thai + English sections.

    Two flavours share the English "Amount (THB):" label:
      * Bill Payment        -> merchant from "Company Name:"
      * PromptPay Transfer  -> merchant from "Received Name:"

    Parsed line-wise (not flattened): the merchant name occupies its own line,
    while per-transaction refs (MERCHANTNO.1, Payment Code, SHOP ID, Reference 1)
    live on the *following* line and must NOT leak into the merchant key.
    """
    if not _is_success(mail):
        return None
    body = mail.body
    amount_m = AMOUNT_RE.search(body)
    if not amount_m:
        return None

    merchant = None
    kind = None
    company = re.search(r"Company\s*Name\s*:\s*(.+)", body, re.I)
    if company:
        merchant, kind = company.group(1).strip(), "bill_payment"
    else:
        received = re.search(r"Received\s*Name\s*:\s*(.+)", body, re.I)
        if received:
            merchant, kind = received.group(1).strip(), "transfer"
    if not merchant:
        return None

    ref_m = re.search(r"Transaction\s*Number\s*:\s*(\S+)", body, re.I)
    return ParsedPayment(
        amount=_amount_to_int(amount_m.group(1)),
        merchant=merchant,
        kind=kind,
        external_ref=ref_m.group(1) if ref_m else None,
        raw_amount=amount_m.group(1),
    )


def parse_krungsri(mail: MailMessage) -> ParsedPayment | None:
    """Krungsri (Bank of Ayudhya) — HTML table, bill payments.

    Merchant from "To Biller:" (Thai text); falls back to "Merchant ID:".
    """
    if not _is_success(mail):
        return None
    t = _flatten(mail.body)
    amount_m = AMOUNT_RE.search(t)
    if not amount_m:
        return None

    biller = re.search(r"To\s*Biller\s*:\s*(.+?)\s+Amount\s*\(THB\)", t, re.I)
    merchant = biller.group(1).strip() if biller else None
    if not merchant:
        mid = re.search(r"Merchant\s*ID\s*:\s*(\S+)", t, re.I)
        merchant = mid.group(1) if mid else None
    if not merchant:
        return None

    ref_m = re.search(r"Reference\s*No\.?\s*:\s*(\S+)", t, re.I)
    return ParsedPayment(
        amount=_amount_to_int(amount_m.group(1)),
        merchant=merchant,
        kind="bill_payment",
        external_ref=ref_m.group(1) if ref_m else None,
        raw_amount=amount_m.group(1),
    )


# sender address (lower-case) -> parser
PARSERS: dict[str, Callable[[MailMessage], "ParsedPayment | None"]] = {
    "kplus@kasikornbank.com": parse_kplus,
    "admin@krungsri.com": parse_krungsri,
}


def sender_address(mail: MailMessage) -> str:
    return parseaddr(mail.sender)[1].lower()


def parse_mail(mail: MailMessage) -> ParsedPayment | None:
    """Dispatch to the parser registered for the email's sender, if any."""
    parser = PARSERS.get(sender_address(mail))
    if parser is None:
        return None
    return parser(mail)
