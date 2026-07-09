"""Turn a bank-notification email into a ParsedPayment.

Both supported banks lay the transaction out as ``Label: value`` pairs, but with
different structure, so there's one parser per sender:

* **K PLUS** (Kasikornbank) — plain text, a Thai section followed by an English
  one. We anchor on the (unambiguous) English labels and read each value to the
  end of its line. This matters: the per-transaction reference (Payment Code /
  MERCHANTNO.1 / SHOP ID / Reference 1 / Mobile No.) lives on the line *after*
  the merchant, so a line-bounded value keeps the merchant key clean.
* **Krungsri** (Bank of Ayudhya) — an HTML table where each label and its value
  sit in adjacent cells. We strip tags, collapse whitespace, then slice the flat
  text into ``{label: value}`` by letting each value run until the next known
  label (so e.g. a "To e-Wallet" row between "To Biller" and "Amount" can't leak
  into the merchant).

Amounts are THB with decimals but the app stores integers, so we round to the
nearest whole unit and keep the original string in ``raw_amount``.
"""
import html
import re
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Callable

from mail_worker import MailMessage

CURRENCY = "THB"

# English amount label, shared by both banks ("Amount (THB): 1,207.21").
_AMOUNT_RE = re.compile(r"Amount\s*\(THB\)\s*:\s*([\d,]+\.\d{2})", re.I)
_DECIMAL_RE = re.compile(r"[\d,]+\.\d{2}")

# K PLUS merchant/counterparty label, in priority order (bill payment first,
# then the two transfer flavours). Read to end-of-line.
_KPLUS_MERCHANT_LABELS = ["Company Name", "Received Name", "Account Name"]

# Krungsri: every label we recognise. Order is irrelevant (the boundary regex is
# built longest-first so no label shadows a longer one it prefixes); non-merchant
# labels are listed only so they terminate the preceding value.
_KRUNGSRI_FIELDS = [
    "Amount (THB)", "Fee (THB)", "To Biller", "To PromptPay ID", "To e-Wallet",
    "Transaction Result", "Type of Transaction", "From Account", "Merchant ID",
    "Transaction ID", "Reference No", "Date/Time", "Memo",
]
_KRUNGSRI_MERCHANT_LABELS = ["To Biller", "To PromptPay ID"]

# A label, an optional trailing dot (Krungsri's "Reference No.:"), then a colon.
_SEP = r"\s*\.?\s*:\s*"
_KRUNGSRI_LABEL_ALT = "|".join(
    re.escape(f) for f in sorted(_KRUNGSRI_FIELDS, key=len, reverse=True)
)
# value = everything up to the next known label (or end of text).
_KRUNGSRI_FIELD_RE = re.compile(
    rf"(?P<label>{_KRUNGSRI_LABEL_ALT}){_SEP}(?P<value>.*?)"
    rf"(?=(?:{_KRUNGSRI_LABEL_ALT}){_SEP}|$)",
    re.S,
)


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
    """Strip HTML tags, unescape entities, collapse all whitespace to spaces."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _amount_to_int(raw: str) -> int:
    return int(round(float(raw.replace(",", ""))))


def _is_success(mail: MailMessage) -> bool:
    return "success" in (mail.subject or "").lower()


def _kind(mail: MailMessage) -> str:
    """Transaction kind from the (English) subject line."""
    return "bill_payment" if "bill payment" in (mail.subject or "").lower() else "transfer"


def parse_kplus(mail: MailMessage) -> ParsedPayment | None:
    """Kasikornbank / K PLUS — plain text, parsed line-wise (see module docstring)."""
    if not _is_success(mail):
        return None
    body = mail.body
    amount_m = _AMOUNT_RE.search(body)
    if not amount_m:
        return None

    merchant = None
    for label in _KPLUS_MERCHANT_LABELS:
        # `.` excludes newline, so the value stops at end of line (before the ref).
        m = re.search(rf"{label}\s*:\s*(.+)", body, re.I)
        if m and m.group(1).strip():
            merchant = m.group(1).strip()
            break
    if not merchant:
        return None

    ref_m = re.search(r"Transaction\s*Number\s*:\s*(\S+)", body, re.I)
    return ParsedPayment(
        amount=_amount_to_int(amount_m.group(1)),
        merchant=merchant,
        kind=_kind(mail),
        external_ref=ref_m.group(1) if ref_m else None,
        raw_amount=amount_m.group(1),
    )


def _krungsri_fields(body: str) -> dict[str, str]:
    """Map every recognised Krungsri label to its value (last wins)."""
    flat = _flatten(body)
    return {m.group("label"): m.group("value").strip()
            for m in _KRUNGSRI_FIELD_RE.finditer(flat)}


def parse_krungsri(mail: MailMessage) -> ParsedPayment | None:
    """Krungsri (Bank of Ayudhya) — HTML table, parsed via label boundaries."""
    if not _is_success(mail):
        return None
    fields = _krungsri_fields(mail.body)

    amount_m = _DECIMAL_RE.search(fields.get("Amount (THB)", ""))
    if not amount_m:
        return None

    merchant = next(
        (fields[label] for label in _KRUNGSRI_MERCHANT_LABELS if fields.get(label)),
        None,
    )
    if not merchant:
        return None

    return ParsedPayment(
        amount=_amount_to_int(amount_m.group(0)),
        merchant=merchant,
        kind=_kind(mail),
        external_ref=fields.get("Reference No"),
        raw_amount=amount_m.group(0),
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
