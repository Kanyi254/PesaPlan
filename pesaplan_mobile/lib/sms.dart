// M-Pesa SMS capture: reads the phone's SMS inbox (catch-up) and the queue filled by the Android receiver.
import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';

import 'parser.dart';
import 'store.dart';
import 'sync.dart';

const _channel = MethodChannel('pesaplan/sms');

class ImportResult {
  int imported = 0;
  int duplicates = 0;
  final List<String> unrecognised = [];
  String? error;
  void add(ImportResult o) {
    imported += o.imported;
    duplicates += o.duplicates;
    unrecognised.addAll(o.unrecognised);
    error ??= o.error;
  }
}

/// Parse M-Pesa messages and save everything new. Mirrors import_mpesa_text in pesaplan_phone.py.
Future<ImportResult> importMessages(Store s, List<String> texts, {String? account}) async {
  final res = ImportResult();
  final wanted = account ?? await s.getSetting('sms_account');
  String? acc;
  if (wanted != null && wanted.isNotEmpty && await s.account(wanted) != null) acc = wanted;
  acc ??= await s.firstAccountOfKind('M-Pesa');
  if (acc == null) {
    final names = await s.accountNames();
    if (names.isNotEmpty) acc = names.first;
  }
  if (acc == null) {
    res.error = 'Add an account first';
    return res;
  }
  final cash = await s.firstAccountOfKind('Cash');
  final seen = <String>{};
  for (final text in texts) {
    for (final msg in MpesaParser.splitMessages(text)) {
      final p = MpesaParser.parse(msg);
      if (p == null) {
        res.unrecognised.add(msg.length > 160 ? msg.substring(0, 160) : msg);
        continue;
      }
      if (seen.contains(p.key) || await s.codeExists(p.key)) {
        res.duplicates++;
        continue;
      }
      seen.add(p.key);
      final ts = fmtTs(p.ts);
      try {
        if ((p.kind == 'withdrawn' || p.kind == 'deposit') && cash != null && cash != acc) {
          if (p.kind == 'withdrawn') {
            await s.addTransfer(ts, acc, cash, p.amount, p.description, code: p.key, balance: p.balance, fee: p.fee);
          } else {
            await s.addTransfer(ts, cash, acc, p.amount, p.description, code: p.key, balance: p.balance);
          }
        } else {
          var cat = p.category;
          if (p.kind == 'withdrawn') cat = 'Cash Withdrawal';
          if (p.kind == 'deposit') cat = 'Received Money';
          await s.addTx(ts, p.type, p.amount, cat, p.description, acc, code: p.key, fee: p.fee, balance: p.balance);
        }
        res.imported++;
      } catch (_) {
        res.duplicates++;
      }
    }
  }
  return res;
}

class SmsBridge {
  static Future<bool> hasPermission() async => (await _channel.invokeMethod<bool>('hasPermission')) ?? false;
  static Future<bool> requestPermission() async => (await _channel.invokeMethod<bool>('requestPermission')) ?? false;

  static Future<List<String>> readInbox(int sinceMs) async {
    final r = await _channel.invokeMethod<List<dynamic>>('readInbox', {'since': sinceMs}) ?? [];
    return [for (final m in r) (m as Map)['body'] as String];
  }

  /// Messages the Android receiver caught while the app was closed.
  static Future<ImportResult> drainQueue(Store s) async {
    final res = ImportResult();
    final q = await _channel.invokeMethod<List<dynamic>>('peekQueue') ?? [];
    if (q.isEmpty) return res;
    final texts = [for (final m in q) (m as Map)['body'] as String];
    final r = await importMessages(s, texts);
    res.add(r);
    if (r.error == null) {
      await _channel.invokeMethod('ackQueue', [for (final m in q) (m as Map)['id']]);
    }
    return res;
  }

  /// Everything in the inbox since the last scan (a day of overlap; duplicates are skipped by M-Pesa code).
  static Future<ImportResult> catchUp(Store s) async {
    final res = ImportResult();
    if (!await hasPermission()) return res;
    final saved = int.tryParse(await s.getSetting('sms_since') ?? '');
    final now = DateTime.now();
    final since = saved ?? DateTime(now.year, now.month, 1).millisecondsSinceEpoch;
    final texts = await readInbox(since);
    if (texts.isNotEmpty) res.add(await importMessages(s, texts));
    if (res.error == null) {
      await s.setSetting('sms_since', '${now.subtract(const Duration(days: 1)).millisecondsSinceEpoch}');
    }
    res.add(await drainQueue(s));
    return res;
  }
}

/// Entry point the Android receiver starts in a background engine when an M-Pesa message arrives
/// while the app is closed: save it, push it to the server, then tell Android it can stop.
@pragma('vm:entry-point')
Future<void> smsBackground() async {
  WidgetsFlutterBinding.ensureInitialized();
  try {
    final s = await Store.open();
    await SmsBridge.drainQueue(s);
    final url = await s.getSetting('sync_url') ?? '';
    final tok = await s.getSetting('sync_token') ?? '';
    if (url.isNotEmpty && tok.isNotEmpty) {
      await syncOnce(s, url, tok);
    }
    await s.db.close();
  } catch (_) {
    // the message stays queued and is picked up the next time the app opens
  } finally {
    try {
      await _channel.invokeMethod('done');
    } catch (_) {}
  }
}
