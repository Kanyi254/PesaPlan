import 'package:flutter_test/flutter_test.dart';
import 'package:pesaplan/parser.dart';

// Expected values were produced by the PC app's Python parser, so a pass means phone and PC agree.

String two(int n) => n.toString().padLeft(2, '0');
String stamp(DateTime d) => '${d.year}-${two(d.month)}-${two(d.day)} ${two(d.hour)}:${two(d.minute)}';

void main() {
  final raw = r'''UCB7K2LM4P Confirmed. Ksh1,250.00 paid to NAIVAS SUPERMARKET. on 8/11/26 at 6:42 PM.New M-PESA balance is Ksh4,310.55. Transaction cost, Ksh0.00.

UCB1X9QZ3A Confirmed. Ksh500.00 sent to JOHN KAMAU 0712345678 on 9/11/26 at 8:05 AM. New M-PESA balance is Ksh3,803.55. Transaction cost, Ksh7.00.

UCB5T8WN2D Confirmed. You have received Ksh6,500.00 from MERCY ACHIENG 0722111222 on 9/11/26 at 10:17 AM. New M-PESA balance is Ksh10,303.55.

UCB9R4HV6E Confirmed. Ksh2,000.00 withdrawn from 654321 - Mwangi Agency on 9/11/26 at 12:30 PM. New M-PESA balance is Ksh8,275.55. Transaction cost, Ksh28.00.

UCB3P7DS8F Confirmed. You bought Ksh100.00 of airtime on 9/11/26 at 1:55 PM. New M-PESA balance is Ksh8,175.55. Transaction cost, Ksh0.00.

UCB6M2JK1G Confirmed. Ksh1,500.00 sent to KPLC PREPAID for account 14400123 on 9/11/26 at 3:20 PM. New M-PESA balance is Ksh6,661.55. Transaction cost, Ksh13.00.

UDE1A2B3C4 Confirmed. Ksh3,000.00 paid to Java House Westlands. on 12/11/2026 at 12:15 PM.New M-PESA balance is Ksh1,000.00. Transaction cost, Ksh0.00.
UDE2A2B3C4 Confirmed.on 12/11/26 at 9:05 AM Ksh2,500.00 received from SAFARICOM ACME LTD SALARY 0700000000. New M-PESA balance is Ksh9,000.00.
UDE3A2B3C4 Confirmed. Ksh1,000.00 reversed. on 13/11/26 at 1:00 AM. New M-PESA balance is Ksh2,000.00.
UDE4A2B3C4 Confirmed. Ksh700.00 from your M-PESA has been used to partially pay your outstanding Fuliza M-PESA. on 13/11/26 at 11:59 PM. New M-PESA balance is Ksh0.00.
UDE5A2B3C4 Confirmed. Give Ksh2,000.00 cash to Mwangi Agency 654321 New M-PESA balance is Ksh5,000.00. on 13/11/26 at 12:00 PM
UDE6A2B3C4 Confirmed. Ksh45.00 sent to Equity Paybill for account 99. on 31/2/26 at 3:00 PM. New M-PESA balance is Ksh10.00. Transaction cost, Ksh5.00.
You have an outstanding Fuliza amount is Ksh500. Confirmed.
Hello this is not an mpesa message''';
  test('splits into the same number of messages as the PC app', () {
    expect(MpesaParser.splitMessages(raw).length, 12);
  });
  final msgs = MpesaParser.splitMessages(raw);
  test('message 0 parses (paid)', () {
    final p = MpesaParser.parse(msgs[0])!;
    expect(p.key, 'UCB7K2LM4P'); expect(p.kind, 'paid'); expect(p.type, 'expense');
    expect(p.amount, 1250.0); expect(p.fee, 0.0); expect(p.balance, 4310.55);
    expect(p.description, "NAIVAS SUPERMARKET"); expect(p.category, "Food & Groceries");
    expect(stamp(p.ts), '2026-11-08 18:42');
  });
  test('message 1 parses (sent)', () {
    final p = MpesaParser.parse(msgs[1])!;
    expect(p.key, 'UCB1X9QZ3A'); expect(p.kind, 'sent'); expect(p.type, 'expense');
    expect(p.amount, 500.0); expect(p.fee, 7.0); expect(p.balance, 3803.55);
    expect(p.description, "JOHN KAMAU"); expect(p.category, "Other");
    expect(stamp(p.ts), '2026-11-09 08:05');
  });
  test('message 2 parses (received)', () {
    final p = MpesaParser.parse(msgs[2])!;
    expect(p.key, 'UCB5T8WN2D'); expect(p.kind, 'received'); expect(p.type, 'income');
    expect(p.amount, 6500.0); expect(p.fee, 0.0); expect(p.balance, 10303.55);
    expect(p.description, "MERCY ACHIENG"); expect(p.category, "Received Money");
    expect(stamp(p.ts), '2026-11-09 10:17');
  });
  test('message 3 parses (withdrawn)', () {
    final p = MpesaParser.parse(msgs[3])!;
    expect(p.key, 'UCB9R4HV6E'); expect(p.kind, 'withdrawn'); expect(p.type, 'expense');
    expect(p.amount, 2000.0); expect(p.fee, 28.0); expect(p.balance, 8275.55);
    expect(p.description, "Agent withdrawal - 654321 - Mwangi Agency"); expect(p.category, "Cash Withdrawal");
    expect(stamp(p.ts), '2026-11-09 12:30');
  });
  test('message 4 parses (airtime)', () {
    final p = MpesaParser.parse(msgs[4])!;
    expect(p.key, 'UCB3P7DS8F'); expect(p.kind, 'airtime'); expect(p.type, 'expense');
    expect(p.amount, 100.0); expect(p.fee, 0.0); expect(p.balance, 8175.55);
    expect(p.description, "Safaricom Airtime"); expect(p.category, "Airtime & Data");
    expect(stamp(p.ts), '2026-11-09 13:55');
  });
  test('message 5 parses (sent)', () {
    final p = MpesaParser.parse(msgs[5])!;
    expect(p.key, 'UCB6M2JK1G'); expect(p.kind, 'sent'); expect(p.type, 'expense');
    expect(p.amount, 1500.0); expect(p.fee, 13.0); expect(p.balance, 6661.55);
    expect(p.description, "KPLC PREPAID for account 14400123"); expect(p.category, "Bills & Utilities");
    expect(stamp(p.ts), '2026-11-09 15:20');
  });
  test('message 6 parses (paid)', () {
    final p = MpesaParser.parse(msgs[6])!;
    expect(p.key, 'UDE1A2B3C4'); expect(p.kind, 'paid'); expect(p.type, 'expense');
    expect(p.amount, 3000.0); expect(p.fee, 0.0); expect(p.balance, 1000.0);
    expect(p.description, "Java House Westlands"); expect(p.category, "Food & Groceries");
    expect(stamp(p.ts), '2026-11-12 12:15');
  });
  test('message 7 is ignored', () { expect(MpesaParser.parse(msgs[7]), isNull); });
  test('message 8 parses (reversal)', () {
    final p = MpesaParser.parse(msgs[8])!;
    expect(p.key, 'UDE3A2B3C4'); expect(p.kind, 'reversal'); expect(p.type, 'income');
    expect(p.amount, 1000.0); expect(p.fee, 0.0); expect(p.balance, 2000.0);
    expect(p.description, "M-Pesa reversal"); expect(p.category, "Refund");
    expect(stamp(p.ts), '2026-11-13 01:00');
  });
  test('message 9 parses (fuliza)', () {
    final p = MpesaParser.parse(msgs[9])!;
    expect(p.key, 'UDE4A2B3C4'); expect(p.kind, 'fuliza'); expect(p.type, 'expense');
    expect(p.amount, 700.0); expect(p.fee, 0.0); expect(p.balance, 0.0);
    expect(p.description, "Fuliza repayment"); expect(p.category, "Loans & Fuliza");
    expect(stamp(p.ts), '2026-11-13 23:59');
  });
  test('message 10 parses (deposit)', () {
    final p = MpesaParser.parse(msgs[10])!;
    expect(p.key, 'UDE5A2B3C4'); expect(p.kind, 'deposit'); expect(p.type, 'income');
    expect(p.amount, 2000.0); expect(p.fee, 0.0); expect(p.balance, 5000.0);
    expect(p.description, "Cash deposit - Mwangi Agency 654321"); expect(p.category, "Received Money");
    expect(stamp(p.ts), '2026-11-13 12:00');
  });
  test('message 11 is ignored', () { expect(MpesaParser.parse(msgs[11]), isNull); });
}
