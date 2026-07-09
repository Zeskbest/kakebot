"""Tests for the per-bank email parsers, using trimmed real email bodies.

Covers every transaction shape seen in the live inbox and two regressions:
the K PLUS merchant must not absorb the reference on the following line, and the
Krungsri merchant must not absorb the "To e-Wallet" row that sits before Amount.
"""
import unittest

from mail_worker import MailMessage
from parsers import parse_mail, parse_kplus, parse_krungsri

KPLUS = "K PLUS <KPLUS@kasikornbank.com>"
KRUNGSRI = '"krungsri app" <admin@krungsri.com>'


def mail(sender: str, subject: str, body: str) -> MailMessage:
    return MailMessage(message_id="<id@x>", sender=sender, subject=subject,
                       date=None, body=body)


# --- K PLUS: Thai section then English section (parser keys off English). ------

KPLUS_BILL = mail(KPLUS, "Result of Bill Payment (Success)", """\
เรียน ผู้ใช้โทรศัพท์มือถือ
	เพื่อเข้าบัญชีบริษัท: ลาซาด้า
	จำนวนเงิน (บาท): 1,906.91

Subject: Result of Payment (Success)
	Transaction Date: 09/07/2026  15:41:09
	Transaction Number: 016190154109320787
	Paid From Account: xxx-x-x2024-x
	Company Name: LAZADA
	Payment Code : 8973589289 Invoice number 8973589289
	Amount (THB): 1,906.91
	Fee (THB): 0.00
""")

KPLUS_TRANSFER = mail(KPLUS, "Result of Funds Transfer (Success)", """\
	Transaction Number: 016185224339BTF03073
	To Account: 399-2-92750-9
	Account Name: MS. KANJANA JANYO
	Amount (THB): 25.00
	Fee (THB): 0.00
""")

KPLUS_PROMPTPAY = mail(KPLUS, "Result of PromptPay Funds Transfer (Success)", """\
	Transaction Number: 016185150932BPP00256
	To PromptPay ID: xxx-xxx-6963
	Received Name: WEIQIN MA MRS.
	Amount (THB): 1,012.00
""")

# --- Krungsri: HTML table, label and value in adjacent cells. -----------------

KRUNGSRI_BILL = mail(KRUNGSRI, "Result of bill payment (Success)", """\
<table><tbody>
  <tr><td><strong>To Biller:</strong></td><td> LazadaPay</td></tr>
  <tr><td><strong>Amount (THB):</strong></td><td> 442.63</td></tr>
  <tr><td><strong>Merchant ID:</strong></td><td> KB000002169812</td></tr>
  <tr><td><strong>Reference No.:</strong></td><td> KSA00000000698955871</td></tr>
</tbody></table>""")

KRUNGSRI_EWALLET = mail(KRUNGSRI, "transfer-ewallet-result-success", """\
<table><tbody>
  <tr><td><strong>To Biller:</strong></td><td> MS. KEDMANEE AUNTARASEN</td></tr>
  <tr><td><strong>To e-Wallet:</strong></td><td> 004999148594335</td></tr>
  <tr><td><strong>Amount (THB):</strong></td><td> 1,290.00</td></tr>
  <tr><td><strong>Reference No.:</strong></td><td> KSA00000000696459327</td></tr>
</tbody></table>""")

KRUNGSRI_PROMPTPAY = mail(KRUNGSRI, "Result of fund transfer to PromptPay (Success)", """\
<table><tbody>
  <tr><td><strong>To PromptPay ID:</strong></td><td> XXX-XXX-2545 MR.TAKSIN MEEPHIAN</td></tr>
  <tr><td><strong>Amount (THB):</strong></td><td> 150.00</td></tr>
  <tr><td><strong>Reference No.:</strong></td><td> KSA00000000693473576</td></tr>
</tbody></table>""")


class KplusTests(unittest.TestCase):
    def test_bill_payment(self):
        p = parse_kplus(KPLUS_BILL)
        self.assertEqual(p.amount, 1907)          # rounded from 1,906.91
        self.assertEqual(p.raw_amount, "1,906.91")
        self.assertEqual(p.merchant, "LAZADA")    # regression: no "Payment Code ..."
        self.assertEqual(p.kind, "bill_payment")
        self.assertEqual(p.external_ref, "016190154109320787")

    def test_funds_transfer_uses_account_name(self):
        p = parse_kplus(KPLUS_TRANSFER)
        self.assertEqual(p.amount, 25)
        self.assertEqual(p.merchant, "MS. KANJANA JANYO")
        self.assertEqual(p.kind, "transfer")

    def test_promptpay_uses_received_name(self):
        p = parse_kplus(KPLUS_PROMPTPAY)
        self.assertEqual(p.merchant, "WEIQIN MA MRS.")
        self.assertEqual(p.kind, "transfer")


class KrungsriTests(unittest.TestCase):
    def test_bill_payment(self):
        p = parse_krungsri(KRUNGSRI_BILL)
        self.assertEqual(p.amount, 443)
        self.assertEqual(p.merchant, "LazadaPay")
        self.assertEqual(p.kind, "bill_payment")
        self.assertEqual(p.external_ref, "KSA00000000698955871")

    def test_ewallet_merchant_stops_before_wallet_row(self):
        p = parse_krungsri(KRUNGSRI_EWALLET)
        self.assertEqual(p.merchant, "MS. KEDMANEE AUNTARASEN")  # not the wallet no.
        self.assertEqual(p.amount, 1290)
        self.assertEqual(p.kind, "transfer")

    def test_promptpay(self):
        p = parse_krungsri(KRUNGSRI_PROMPTPAY)
        self.assertEqual(p.merchant, "XXX-XXX-2545 MR.TAKSIN MEEPHIAN")
        self.assertEqual(p.amount, 150)


class DispatchTests(unittest.TestCase):
    def test_parse_mail_routes_by_sender(self):
        self.assertEqual(parse_mail(KPLUS_BILL).merchant, "LAZADA")
        self.assertEqual(parse_mail(KRUNGSRI_BILL).merchant, "LazadaPay")

    def test_unknown_sender_is_ignored(self):
        self.assertIsNone(parse_mail(mail("noreply@spam.com", "Success", "Amount (THB): 5.00")))

    def test_non_success_is_ignored(self):
        failed = mail(KPLUS, "Result of Bill Payment (Failed)", KPLUS_BILL.body)
        self.assertIsNone(parse_kplus(failed))


if __name__ == "__main__":
    unittest.main()
