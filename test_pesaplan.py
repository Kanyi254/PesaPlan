import os, sqlite3, tempfile, unittest
from datetime import datetime, timezone
import pesaplan as P
import pesaplan_phone as PH

PAID = ("UCB7K2LM4P Confirmed. Ksh1,250.00 paid to NAIVAS SUPERMARKET. on 8/11/26 at 6:42 PM."
        "New M-PESA balance is Ksh4,310.55. Transaction cost, Ksh0.00.")
WITHDRAW = ("UCB9R4HV6E Confirmed. Ksh2,000.00 withdrawn from 654321 - Mwangi Agency on 9/11/26 at 12:30 PM. "
            "New M-PESA balance is Ksh8,275.55. Transaction cost, Ksh28.00.")


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = P.Database(os.path.join(self.dir.name, "t.db"))

    def tearDown(self):
        self.db.conn.close()
        self.dir.cleanup()

    def test_import_and_dedupe(self):
        r = self.db.import_mpesa_text(PAID, "M-Pesa")
        self.assertEqual((r["imported"], r["duplicates"]), (1, 0))
        r = self.db.import_mpesa_text(PAID + "\n\n" + PAID, "M-Pesa")
        self.assertEqual((r["imported"], r["duplicates"]), (0, 2))
        self.assertEqual(self.db.count(), 1)

    def test_unrecognised(self):
        r = self.db.import_mpesa_text("hello there", "M-Pesa")
        self.assertEqual(r["imported"], 0)
        self.assertEqual(r["unrecognised"], ["hello there"])

    def test_withdrawal_makes_transfer_and_fee(self):
        r = self.db.import_mpesa_text(WITHDRAW, "M-Pesa")
        self.assertEqual(r["imported"], 1)
        self.assertEqual(len(self.db.transfers()), 1)
        fee = self.db.query(category="Transaction Fees")
        self.assertEqual([(f["amount"], f["mpesa_code"]) for f in fee], [(28.0, "UCB9R4HV6E-FEE")])

    def test_fee_failure_rolls_back_transfer(self):
        def boom(*a, **k):
            raise sqlite3.IntegrityError("simulated")
        self.db.add = boom
        r = self.db.import_mpesa_text(WITHDRAW, "M-Pesa")
        self.assertEqual(r["imported"], 0)
        self.assertEqual(len(self.db.transfers()), 0)


class TimezoneTests(unittest.TestCase):
    def test_utc_converted_to_local(self):
        want = datetime(2026, 11, 13, 7, 0, tzinfo=timezone.utc).astimezone().replace(tzinfo=None).strftime(P.FMT)
        self.assertEqual(PH._dt(P, "2026-11-13T07:00:00Z"), want)

    def test_naive_is_left_alone(self):
        self.assertEqual(PH._dt(P, "2026-11-13T10:00"), "2026-11-13 10:00:00")


if __name__ == "__main__":
    unittest.main()
