// M-Pesa SMS parser and category helpers. A line-for-line port of MpesaParser in pesaplan.py,
// so the phone and the PC read messages the same way.

const expenseCats = [
  'Food & Groceries', 'Transport', 'Airtime & Data', 'Bills & Utilities', 'Rent', 'Shopping', 'Health',
  'Education', 'Entertainment', 'Savings & Investments', 'Loans & Fuliza', 'Cash Withdrawal',
  'Transaction Fees', 'Other'
];
const incomeCats = [
  'Salary', 'Side Hustle', 'Deals', 'Gifts', 'Received Money', 'Refund', 'Interest & Dividends', 'Other Income'
];
const accountKinds = ['Bank', 'M-Pesa', 'Cash', 'Other'];
const defaultAccounts = [
  ['M-Pesa', 'M-Pesa'],
  ['Cash', 'Cash'],
  ['Bank Account 1', 'Bank'],
  ['Bank Account 2', 'Bank'],
  ['Bank Account 3', 'Bank'],
  ['Bank Account 4', 'Bank'],
];

const _keywords = <List<Object>>[
  ['Airtime & Data', ['airtime', 'bundles', 'data bundle']],
  ['Bills & Utilities', ['kplc', 'kenya power', 'nairobi water', 'water', 'zuku', 'dstv', 'gotv', 'startimes', 'wifi', 'wi-fi', 'internet', 'faiba', 'jtl', 'safaricom home']],
  ['Transport', ['uber', 'bolt', 'little cab', 'matatu', 'fuel', 'petrol', 'shell', 'totalenergies', 'total energies', 'rubis', 'kenol', 'oilibya', 'parking', 'sgr', 'fare', 'boda', 'nduthi']],
  ['Food & Groceries', ['naivas', 'quickmart', 'carrefour', 'chandarana', 'supermarket', 'butchery', 'restaurant', 'kfc', 'java', 'cafe', 'coffee', 'hotel', 'mboga', 'food', 'pizza', 'kitchen', 'eatery', 'bakery', 'glovo', 'chicken', 'grocer', 'market', 'kiosk', 'duka', 'minimart', 'mini mart', 'supplies']],
  ['Health', ['pharmacy', 'chemist', 'hospital', 'clinic', 'nhif', 'social health', 'medical', 'dental']],
  ['Education', ['school', 'college', 'university', 'tuition', 'helb', 'academy', 'books']],
  ['Entertainment', ['showmax', 'netflix', 'spotify', 'sportpesa', 'betika', 'odibets', 'betway', 'cinema', 'movie', 'lounge', 'club']],
  ['Savings & Investments', ['m-shwari', 'mshwari', 'ziidi', 'sacco', 'mmf', 'money market', 'chama', 'lock savings', 'invest', 'cytonn', 'etf']],
  ['Loans & Fuliza', ['fuliza', 'tala', 'okolea', 'hustler', 'loan', 'timiza', 'zenka']],
  ['Rent', ['rent', 'landlord', 'caretaker']],
  ['Shopping', ['jumia', 'kilimall', 'mall', 'clothes', 'fashion', 'shoes', 'boutique', 'electronics', 'hardware', 'furniture', 'amazon', 'shein', 'aliexpress', 'cosmetics', 'beauty']],
];

String guessCategory(String? text, [String typ = 'expense']) {
  final low = (text ?? '').toLowerCase();
  if (typ == 'income') {
    if (['salary', 'payroll', 'wages'].any(low.contains)) return 'Salary';
    if (['refund', 'reversal'].any(low.contains)) return 'Refund';
    return 'Received Money';
  }
  for (final e in _keywords) {
    final words = e[1] as List<String>;
    if (words.any(low.contains)) return e[0] as String;
  }
  return 'Other';
}

class ParsedSms {
  final String key;
  final String? code;
  final DateTime ts;
  final String type; // income | expense
  final String kind; // paid sent received withdrawn airtime fuliza reversal deposit
  final double amount;
  final double fee;
  final String description;
  final String category;
  final double? balance;
  ParsedSms({
    required this.key,
    required this.code,
    required this.ts,
    required this.type,
    required this.kind,
    required this.amount,
    required this.fee,
    required this.description,
    required this.category,
    required this.balance,
  });
}

class MpesaParser {
  static final _codeStart = RegExp(r'(?<![A-Za-z0-9])[A-Z0-9]{10}\s+Confirmed');
  static final _amt = RegExp(r'(?:Ksh|KES)\.?\s?([\d,]+(?:\.\d{1,2})?)', caseSensitive: false);
  static final _bal = RegExp(r'balance\s+(?:is|was)?\s*:?\s*(?:Ksh|KES)\.?\s?([\d,]+(?:\.\d{1,2})?)', caseSensitive: false);
  static final _fee = RegExp(r'Transaction cost,?\s*:?\s*(?:Ksh|KES)\.?\s?([\d,]+(?:\.\d{1,2})?)', caseSensitive: false);
  static final _date = RegExp(r'(\d{1,2}/\d{1,2}/\d{2,4})\s+at\s+(\d{1,2}:\d{2}\s*[AaPp][Mm])');
  static const _on = r'\s+on\s+\d{1,2}/\d{1,2}/\d{2,4}';

  /// Splits pasted or inbox text into one string per M-Pesa message.
  static List<String> splitMessages(String text) {
    final out = <String>[];
    for (final block in text.trim().split(RegExp(r'\n\s*\n'))) {
      // cut the block in front of every "<10-char code> Confirmed"
      final cuts = <int>[0];
      for (final m in _codeStart.allMatches(block)) {
        if (m.start > 0) cuts.add(m.start);
      }
      var parts = <String>[];
      for (var i = 0; i < cuts.length; i++) {
        final end = i + 1 < cuts.length ? cuts[i + 1] : block.length;
        final s = block.substring(cuts[i], end);
        if (s.trim().isNotEmpty) parts.add(s);
      }
      final lines = block.split('\n').where((l) => l.trim().isNotEmpty).toList();
      if (parts.length <= 1 && lines.length > 1 && lines.every((l) => l.toLowerCase().contains('ksh'))) {
        parts = lines;
      }
      for (final p in parts) {
        final q = p.trim().split(RegExp(r'\s+')).join(' ');
        if (q.isNotEmpty) out.add(q);
      }
    }
    return out;
  }

  static double _num(String s) => double.parse(s.replaceAll(',', ''));

  static String _cleanName(String name) {
    final n = name.trim().replaceAll(RegExp(r'\s*(\+?254|0)\d{8,9}\s*$'), '');
    return n.trim().replaceAll(RegExp(r'^[ .]+|[ .]+$'), '');
  }

  static String? _group(RegExp re, String s) => re.firstMatch(s)?.group(1);

  static DateTime _stamp(String msg) {
    final now = DateTime.now();
    final fallback = DateTime(now.year, now.month, now.day, now.hour, now.minute);
    final dm = _date.firstMatch(msg);
    if (dm == null) return fallback;
    try {
      final d = dm.group(1)!.split('/');
      final day = int.parse(d[0]);
      final month = int.parse(d[1]);
      var year = int.parse(d[2]);
      if (d[2].length == 2) year += 2000;
      final t = RegExp(r'(\d{1,2}):(\d{2})([AP])M').firstMatch(dm.group(2)!.toUpperCase().replaceAll(' ', ''))!;
      var h = int.parse(t.group(1)!);
      final mi = int.parse(t.group(2)!);
      if (h < 1 || h > 12 || mi > 59) return fallback;
      h = h % 12 + (t.group(3) == 'P' ? 12 : 0);
      final dt = DateTime(year, month, day, h, mi);
      if (dt.month != month || dt.day != day) return fallback; // e.g. 31/02
      return dt;
    } catch (_) {
      return fallback;
    }
  }

  static ParsedSms? parse(String msg) {
    final low = msg.toLowerCase();
    final am = _amt.firstMatch(msg);
    if (am == null || !low.contains('confirmed')) return null;
    if (low.contains('fuliza') && low.contains('amount is')) return null; // limit notices
    final amount = _num(am.group(1)!);

    final code = RegExp(r'^\s*([A-Z0-9]{10})\s+Confirmed').firstMatch(msg)?.group(1);

    String kind;
    var who = '';
    if (low.contains('you have received')) {
      kind = 'received';
      final m = _group(RegExp(r'from\s+(.+?)' + _on, caseSensitive: false), msg);
      who = m != null ? _cleanName(m) : 'M-Pesa';
    } else if (low.contains('you bought') && low.contains('airtime')) {
      kind = 'airtime';
      who = 'Safaricom Airtime';
    } else if (RegExp(r'withdraw').hasMatch(low)) {
      kind = 'withdrawn';
      final m = _group(RegExp(r'withdrawn from\s+(.+?)' + _on, caseSensitive: false), msg);
      who = 'Agent withdrawal' + (m != null ? ' - ' + _cleanName(m) : '');
    } else if (low.contains(' paid to ')) {
      kind = 'paid';
      final m = _group(RegExp(r'paid to\s+(.+?)\.?' + _on, caseSensitive: false), msg);
      who = m != null ? _cleanName(m) : 'Payment';
    } else if (low.contains(' sent to ')) {
      kind = 'sent';
      final m = _group(RegExp(r'sent to\s+(.+?)' + _on, caseSensitive: false), msg);
      who = m != null ? _cleanName(m) : 'Transfer';
    } else if (low.contains('fuliza') && (low.contains('pay') || low.contains('repa'))) {
      kind = 'fuliza';
      who = 'Fuliza repayment';
    } else if (low.contains('reversed') || low.contains('reversal')) {
      kind = 'reversal';
      who = 'M-Pesa reversal';
    } else if (low.contains('give ksh') || low.contains('deposit')) {
      kind = 'deposit';
      who = 'Cash deposit';
      final m = _group(RegExp(r'cash to\s+(.+?)\s+New M-PESA', caseSensitive: false), msg);
      if (m != null) who += ' - ' + _cleanName(m);
    } else {
      return null;
    }

    final ts = _stamp(msg);
    final fm = _group(_fee, msg);
    final bm = _group(_bal, msg);
    final fee = fm != null ? _num(fm) : 0.0;
    final balance = bm != null ? _num(bm) : null;
    final typ = (kind == 'received' || kind == 'deposit' || kind == 'reversal') ? 'income' : 'expense';

    String category;
    if (kind == 'airtime') {
      category = 'Airtime & Data';
    } else if (kind == 'fuliza') {
      category = 'Loans & Fuliza';
    } else if (kind == 'reversal') {
      category = 'Refund';
    } else if (kind == 'withdrawn') {
      category = 'Cash Withdrawal';
    } else {
      category = guessCategory(who, typ);
    }

    String two(int n) => n.toString().padLeft(2, '0');
    final key = code ?? 'SMS${ts.year}${two(ts.month)}${two(ts.day)}${two(ts.hour)}${two(ts.minute)}${(amount * 100).toInt()}';
    return ParsedSms(
      key: key,
      code: code,
      ts: ts,
      type: typ,
      kind: kind,
      amount: amount,
      fee: fee,
      description: who,
      category: category,
      balance: balance,
    );
  }
}
