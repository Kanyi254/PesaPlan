// Local database + sync bookkeeping. Same tables and column names as the PC app, so records sync 1:1.
import 'dart:math';

import 'package:path/path.dart' as p;
import 'package:sqflite/sqflite.dart';

typedef Rec = Map<String, Object?>;

/// Columns synced for each kind (identical to KINDS in pesaplan_sync.py).
const syncCols = <String, List<String>>{
  'transactions': ['ts', 'type', 'amount', 'category', 'description', 'source', 'mpesa_code', 'balance_after'],
  'transfers': ['ts', 'from_acc', 'to_acc', 'amount', 'note', 'code', 'balance_after'],
  'accounts': ['name', 'kind', 'opening_balance', 'opened_on', 'closed_on', 'sort'],
  'budgets': ['category', 'monthly_limit'],
};
const syncNkey = <String, String>{
  'transactions': 'mpesa_code',
  'transfers': 'code',
  'accounts': 'name',
  'budgets': 'category',
};

String _two(int n) => n.toString().padLeft(2, '0');

/// UTC timestamp with exactly 3 millisecond digits - the format the server and PC compare as text.
String utcNow() {
  final d = DateTime.now().toUtc();
  return '${d.year.toString().padLeft(4, '0')}-${_two(d.month)}-${_two(d.day)}T${_two(d.hour)}:${_two(d.minute)}:'
      '${_two(d.second)}.${d.millisecond.toString().padLeft(3, '0')}Z';
}

String fmtTs(DateTime d) =>
    '${d.year.toString().padLeft(4, '0')}-${_two(d.month)}-${_two(d.day)} ${_two(d.hour)}:${_two(d.minute)}:${_two(d.second)}';

DateTime parseTs(String s) {
  final t = s.replaceFirst('T', ' ');
  final a = t.split(' ');
  final d = a[0].split('-').map(int.parse).toList();
  final h = a.length > 1 ? a[1].split(':').map((x) => int.parse(x.split('.').first)).toList() : [0, 0, 0];
  return DateTime(d[0], d[1], d[2], h[0], h.length > 1 ? h[1] : 0, h.length > 2 ? h[2] : 0);
}

String newUuid() {
  final r = Random.secure();
  final b = StringBuffer();
  for (var i = 0; i < 16; i++) {
    b.write(r.nextInt(256).toRadixString(16).padLeft(2, '0'));
  }
  return b.toString();
}

double num0(Object? v) => (v as num?)?.toDouble() ?? 0.0;

class Store {
  final Database db;
  Store(this.db);

  static Future<Store> open({String name = 'pesaplan.db'}) async {
    final path = p.join(await getDatabasesPath(), name);
    final db = await openDatabase(
      path,
      version: 1,
      onConfigure: (d) async {
        // two isolates (the app and the background SMS handler) may open this file: wait instead of failing
        await d.rawQuery('PRAGMA journal_mode=WAL');
        await d.rawQuery('PRAGMA busy_timeout=8000');
      },
      onCreate: (d, v) async {
        await d.execute('CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)');
        await d.execute('''CREATE TABLE transactions(
          id INTEGER PRIMARY KEY AUTOINCREMENT, uuid TEXT UNIQUE, updated_at TEXT,
          ts TEXT NOT NULL, type TEXT NOT NULL, amount REAL NOT NULL, category TEXT NOT NULL,
          description TEXT DEFAULT '', source TEXT DEFAULT 'Cash', mpesa_code TEXT UNIQUE, balance_after REAL)''');
        await d.execute('CREATE INDEX idx_tx_ts ON transactions(ts)');
        await d.execute('''CREATE TABLE transfers(
          id INTEGER PRIMARY KEY AUTOINCREMENT, uuid TEXT UNIQUE, updated_at TEXT,
          ts TEXT NOT NULL, from_acc TEXT NOT NULL, to_acc TEXT NOT NULL, amount REAL NOT NULL,
          note TEXT DEFAULT '', code TEXT UNIQUE, balance_after REAL)''');
        await d.execute('CREATE INDEX idx_tr_ts ON transfers(ts)');
        await d.execute('''CREATE TABLE accounts(
          id INTEGER PRIMARY KEY AUTOINCREMENT, uuid TEXT UNIQUE, updated_at TEXT,
          name TEXT UNIQUE NOT NULL, kind TEXT NOT NULL DEFAULT 'Bank', opening_balance REAL NOT NULL DEFAULT 0,
          opened_on TEXT NOT NULL, closed_on TEXT, sort INTEGER DEFAULT 0)''');
        await d.execute('''CREATE TABLE budgets(
          id INTEGER PRIMARY KEY AUTOINCREMENT, uuid TEXT UNIQUE, updated_at TEXT,
          category TEXT UNIQUE NOT NULL, monthly_limit REAL NOT NULL)''');
        await d.execute('CREATE TABLE sync_dirty(kind TEXT NOT NULL, uuid TEXT NOT NULL, PRIMARY KEY(kind, uuid))');
        await d.execute(
            'CREATE TABLE sync_tombstones(kind TEXT NOT NULL, uuid TEXT NOT NULL, deleted_at TEXT NOT NULL, PRIMARY KEY(kind, uuid))');
      },
    );
    return Store(db);
  }

  // ---------------------------------------------------------------- settings
  Future<String?> getSetting(String key) async {
    final r = await db.query('settings', where: 'key=?', whereArgs: [key]);
    return r.isEmpty ? null : r.first['value'] as String?;
  }

  Future<void> setSetting(String key, String value) async {
    await db.insert('settings', {'key': key, 'value': value}, conflictAlgorithm: ConflictAlgorithm.replace);
  }

  // ---------------------------------------------------------------- write helpers (mark records for sync)
  Future<int> _insert(DatabaseExecutor x, String kind, Rec vals) async {
    final u = newUuid();
    final id = await x.insert(kind, {...vals, 'uuid': u, 'updated_at': utcNow()});
    await x.insert('sync_dirty', {'kind': kind, 'uuid': u}, conflictAlgorithm: ConflictAlgorithm.ignore);
    return id;
  }

  Future<void> _update(DatabaseExecutor x, String kind, int id, Rec vals) async {
    await x.update(kind, {...vals, 'updated_at': utcNow()}, where: 'id=?', whereArgs: [id]);
    final r = await x.query(kind, columns: ['uuid'], where: 'id=?', whereArgs: [id]);
    if (r.isNotEmpty) {
      await x.insert('sync_dirty', {'kind': kind, 'uuid': r.first['uuid']}, conflictAlgorithm: ConflictAlgorithm.ignore);
    }
  }

  Future<void> _delete(DatabaseExecutor x, String kind, int id) async {
    final r = await x.query(kind, columns: ['uuid'], where: 'id=?', whereArgs: [id]);
    if (r.isEmpty) return;
    final u = r.first['uuid'];
    await x.delete(kind, where: 'id=?', whereArgs: [id]);
    await x.delete('sync_dirty', where: 'kind=? AND uuid=?', whereArgs: [kind, u]);
    if (u != null) {
      await x.insert('sync_tombstones', {'kind': kind, 'uuid': u, 'deleted_at': utcNow()},
          conflictAlgorithm: ConflictAlgorithm.replace);
    }
  }

  /// After signing out: mark everything so a later sign-in (maybe to another server) uploads it all again.
  Future<void> markAllDirty() async {
    for (final kind in syncCols.keys) {
      await db.rawInsert('INSERT OR IGNORE INTO sync_dirty(kind, uuid) SELECT ?, uuid FROM $kind WHERE uuid IS NOT NULL', [kind]);
    }
  }

  Future<int> unsent() async {
    final a = Sqflite.firstIntValue(await db.rawQuery('SELECT COUNT(*) FROM sync_dirty')) ?? 0;
    final b = Sqflite.firstIntValue(await db.rawQuery('SELECT COUNT(*) FROM sync_tombstones')) ?? 0;
    return a + b;
  }

  // ---------------------------------------------------------------- accounts
  Future<List<Rec>> accounts() => db.query('accounts', orderBy: 'sort, id');

  Future<Rec?> account(String name) async {
    final r = await db.query('accounts', where: 'name=?', whereArgs: [name]);
    return r.isEmpty ? null : r.first;
  }

  Future<List<String>> accountNames() async => (await accounts()).where((a) => a['closed_on'] == null).map((a) => a['name'] as String).toList();

  Future<String?> firstAccountOfKind(String kind) async {
    final r = await db.query('accounts', where: 'kind=? AND closed_on IS NULL', whereArgs: [kind], orderBy: 'sort, id', limit: 1);
    return r.isEmpty ? null : r.first['name'] as String;
  }

  Future<void> addAccount(String name, String kind, double opening, DateTime openedOn) async {
    final n = (await db.rawQuery('SELECT COALESCE(MAX(sort),-1)+1 AS n FROM accounts')).first['n'] as int;
    await _insert(db, 'accounts', {
      'name': name, 'kind': kind, 'opening_balance': opening, 'opened_on': fmtTs(openedOn), 'closed_on': null, 'sort': n,
    });
  }

  Future<void> addDefaultAccounts(List<List<String>> defs) async {
    final today = DateTime.now();
    final day = DateTime(today.year, today.month, today.day);
    for (final d in defs) {
      if (await account(d[0]) == null) await addAccount(d[0], d[1], 0, day);
    }
  }

  Future<void> updateAccount(int id, {required String kind, required double opening, required DateTime openedOn, DateTime? closedOn}) async {
    await _update(db, 'accounts', id, {
      'kind': kind, 'opening_balance': opening, 'opened_on': fmtTs(openedOn), 'closed_on': closedOn == null ? null : fmtTs(closedOn),
    });
  }

  // ---------------------------------------------------------------- balances (same rules as the PC app)
  Future<List<double>> _flows(String name, String lo, String hi) async {
    final t = (await db.rawQuery(
            "SELECT COALESCE(SUM(CASE WHEN type='income' THEN amount END),0) AS i, "
            "COALESCE(SUM(CASE WHEN type='expense' THEN amount END),0) AS e FROM transactions WHERE source=? AND ts>=? AND ts<?",
            [name, lo, hi]))
        .first;
    final i = (await db.rawQuery('SELECT COALESCE(SUM(amount),0) AS v FROM transfers WHERE to_acc=? AND ts>=? AND ts<?', [name, lo, hi])).first['v'];
    final o = (await db.rawQuery('SELECT COALESCE(SUM(amount),0) AS v FROM transfers WHERE from_acc=? AND ts>=? AND ts<?', [name, lo, hi])).first['v'];
    return [num0(t['i']), num0(t['e']), num0(i), num0(o)];
  }

  Future<double> balanceAt(Rec a, String ts) async {
    final opened = a['opened_on'] as String;
    var f = [0.0, 0.0, 0.0, 0.0];
    if (ts.compareTo(opened) > 0) f = await _flows(a['name'] as String, opened, ts);
    return num0(a['opening_balance']) + f[0] - f[1] + f[2] - f[3];
  }

  /// Per-account opening / movements / closing for [start, end), as strings 'yyyy-MM-dd HH:mm:ss'.
  Future<List<Rec>> monthSummary(String start, String end) async {
    final out = <Rec>[];
    for (final a in await accounts()) {
      final opened = a['opened_on'] as String;
      final closed = a['closed_on'] as String?;
      if (opened.compareTo(end) >= 0) continue;
      if (closed != null && closed.compareTo(start) < 0) continue;
      final lo = start.compareTo(opened) > 0 ? start : opened;
      final f = await _flows(a['name'] as String, lo, end);
      final opening = start.compareTo(opened) > 0 ? await balanceAt(a, start) : num0(a['opening_balance']);
      out.add({
        ...a,
        'opening': opening, 'income': f[0], 'expense': f[1], 'tin': f[2], 'tout': f[3],
        'closing': opening + f[0] - f[1] + f[2] - f[3],
      });
    }
    return out;
  }

  // ---------------------------------------------------------------- transactions / transfers
  Future<bool> codeExists(String code) async {
    final a = await db.query('transactions', columns: ['id'], where: 'mpesa_code=?', whereArgs: [code], limit: 1);
    if (a.isNotEmpty) return true;
    final b = await db.query('transfers', columns: ['id'], where: 'code=?', whereArgs: [code], limit: 1);
    return b.isNotEmpty;
  }

  Future<int> addTx(String ts, String type, double amount, String category, String description, String source,
      {String? code, double fee = 0, double? balance}) async {
    return db.transaction((t) async {
      final id = await _insert(t, 'transactions', {
        'ts': ts, 'type': type, 'amount': amount, 'category': category, 'description': description,
        'source': source, 'mpesa_code': (code == null || code.isEmpty) ? null : code, 'balance_after': balance,
      });
      if (fee > 0 && type == 'expense') {
        final d = 'Fee - $description';
        await _insert(t, 'transactions', {
          'ts': ts, 'type': 'expense', 'amount': fee, 'category': 'Transaction Fees',
          'description': d.length > 80 ? d.substring(0, 80) : d, 'source': source,
          'mpesa_code': (code == null || code.isEmpty) ? null : '$code-FEE', 'balance_after': null,
        });
      }
      return id;
    });
  }

  Future<void> updateTx(int id, String ts, String type, double amount, String category, String description, String source) async {
    await _update(db, 'transactions', id, {
      'ts': ts, 'type': type, 'amount': amount, 'category': category, 'description': description, 'source': source,
    });
  }

  Future<void> deleteTx(int id) => db.transaction((t) => _delete(t, 'transactions', id));

  Future<int> addTransfer(String ts, String from, String to, double amount, String note,
      {String? code, double? balance, double fee = 0}) async {
    return db.transaction((t) async {
      final id = await _insert(t, 'transfers', {
        'ts': ts, 'from_acc': from, 'to_acc': to, 'amount': amount, 'note': note,
        'code': (code == null || code.isEmpty) ? null : code, 'balance_after': balance,
      });
      if (fee > 0) {
        final d = 'Fee - ${note.isEmpty ? 'transfer' : note}';
        await _insert(t, 'transactions', {
          'ts': ts, 'type': 'expense', 'amount': fee, 'category': 'Transaction Fees',
          'description': d.length > 80 ? d.substring(0, 80) : d, 'source': from,
          'mpesa_code': (code == null || code.isEmpty) ? null : '$code-FEE', 'balance_after': null,
        });
      }
      return id;
    });
  }

  Future<void> updateTransfer(int id, String ts, String from, String to, double amount, String note) async {
    await _update(db, 'transfers', id, {'ts': ts, 'from_acc': from, 'to_acc': to, 'amount': amount, 'note': note});
  }

  Future<void> deleteTransfer(int id) async {
    await db.transaction((t) async {
      final r = await t.query('transfers', columns: ['code'], where: 'id=?', whereArgs: [id]);
      await _delete(t, 'transfers', id);
      final code = r.isEmpty ? null : r.first['code'] as String?;
      if (code != null) {
        final f = await t.query('transactions', columns: ['id'], where: 'mpesa_code=?', whereArgs: ['$code-FEE']);
        for (final row in f) {
          await _delete(t, 'transactions', row['id'] as int);
        }
      }
    });
  }

  /// Transactions and transfers in [start, end), newest first. Each row has 'rowtype': 'tx' | 'tr'.
  Future<List<Rec>> activity(String start, String end) async {
    final tx = await db.query('transactions', where: 'ts>=? AND ts<?', whereArgs: [start, end]);
    final tr = await db.query('transfers', where: 'ts>=? AND ts<?', whereArgs: [start, end]);
    final all = <Rec>[
      for (final r in tx) {...r, 'rowtype': 'tx'},
      for (final r in tr) {...r, 'rowtype': 'tr'},
    ];
    all.sort((a, b) {
      final c = (b['ts'] as String).compareTo(a['ts'] as String);
      return c != 0 ? c : (b['id'] as int).compareTo(a['id'] as int);
    });
    return all;
  }

  // ---------------------------------------------------------------- budgets
  Future<List<Rec>> budgets() => db.query('budgets', orderBy: 'category');

  Future<void> setBudget(String category, double limit) async {
    final r = await db.query('budgets', where: 'category=?', whereArgs: [category]);
    if (r.isEmpty) {
      await _insert(db, 'budgets', {'category': category, 'monthly_limit': limit});
    } else {
      await _update(db, 'budgets', r.first['id'] as int, {'monthly_limit': limit});
    }
  }

  Future<void> removeBudget(String category) async {
    final r = await db.query('budgets', where: 'category=?', whereArgs: [category]);
    if (r.isNotEmpty) await db.transaction((t) => _delete(t, 'budgets', r.first['id'] as int));
  }

  Future<Map<String, double>> spentByCategory(String start, String end) async {
    final r = await db.rawQuery(
        "SELECT category, SUM(amount) AS s FROM transactions WHERE type='expense' AND ts>=? AND ts<? GROUP BY category", [start, end]);
    return {for (final row in r) row['category'] as String: num0(row['s'])};
  }

  // ---------------------------------------------------------------- sync support
  /// Everything changed locally and not yet pushed: deletions first, then edits oldest first.
  Future<List<Rec>> pending() async {
    final dead = <Rec>[];
    for (final r in await db.query('sync_tombstones', orderBy: 'deleted_at')) {
      dead.add({'kind': r['kind'], 'uuid': r['uuid'], 'updated_at': r['deleted_at'], 'deleted': true, 'data': <String, Object?>{}});
    }
    final live = <Rec>[];
    for (final kind in syncCols.keys) {
      final cols = syncCols[kind]!;
      final rows = await db.rawQuery(
          'SELECT uuid, updated_at, ${cols.join(', ')} FROM $kind WHERE uuid IN (SELECT uuid FROM sync_dirty WHERE kind=?)', [kind]);
      for (final r in rows) {
        live.add({
          'kind': kind, 'uuid': r['uuid'], 'updated_at': r['updated_at'], 'deleted': false,
          'data': {for (final c in cols) c: r[c]},
        });
      }
    }
    live.sort((a, b) => (a['updated_at'] as String).compareTo(b['updated_at'] as String));
    return [...dead, ...live];
  }

  /// Forget what the server has acknowledged. A row edited again during the sync keeps its mark.
  Future<void> markPushed(List<Rec> batch) async {
    await db.transaction((t) async {
      for (final ch in batch) {
        final kind = ch['kind'] as String;
        if (ch['deleted'] == true) {
          await t.delete('sync_tombstones', where: 'kind=? AND uuid=? AND deleted_at=?', whereArgs: [kind, ch['uuid'], ch['updated_at']]);
        } else {
          await t.rawDelete(
              'DELETE FROM sync_dirty WHERE kind=? AND uuid=? AND EXISTS (SELECT 1 FROM $kind WHERE uuid=? AND updated_at=?)',
              [kind, ch['uuid'], ch['uuid'], ch['updated_at']]);
        }
      }
    });
  }

  /// Write changes pulled from the server (last edit wins, natural-key duplicates are joined). Returns rows changed.
  Future<int> applyChanges(List<dynamic> changes) async {
    var n = 0;
    await db.transaction((t) async {
      for (final c in changes) {
        n += await _applyOne(t, Map<String, Object?>.from(c as Map));
      }
    });
    return n;
  }

  Future<int> _applyOne(Transaction t, Rec ch) async {
    final kind = ch['kind'] as String?;
    if (kind == null || !syncCols.containsKey(kind)) return 0;
    final cols = syncCols[kind]!;
    final uuid = ch['uuid'] as String;
    final ua = ch['updated_at'] as String;
    final local = await t.query(kind, columns: ['id', 'updated_at'], where: 'uuid=?', whereArgs: [uuid]);
    if (ch['deleted'] == true) {
      if (local.isNotEmpty && (local.first['updated_at'] as String).compareTo(ua) <= 0) {
        await t.delete(kind, where: 'uuid=?', whereArgs: [uuid]);
        await t.delete('sync_dirty', where: 'kind=? AND uuid=?', whereArgs: [kind, uuid]);
        return 1;
      }
      return 0;
    }
    final data = Map<String, Object?>.from((ch['data'] as Map?) ?? {});
    final vals = <String, Object?>{for (final c in cols) c: data[c]};
    try {
      if (local.isNotEmpty) {
        if ((local.first['updated_at'] as String).compareTo(ua) >= 0) return 0; // ours is newer or identical
        await t.update(kind, {...vals, 'updated_at': ua}, where: 'uuid=?', whereArgs: [uuid]);
        await t.delete('sync_dirty', where: 'kind=? AND uuid=?', whereArgs: [kind, uuid]);
        return 1;
      }
      final nk = data[syncNkey[kind]];
      if (nk != null && nk != '') {
        final other = await t.query(kind, where: '${syncNkey[kind]}=?', whereArgs: [nk]);
        if (other.isNotEmpty) {
          // same real-world record created on two devices: adopt the server's identity
          final oldUuid = other.first['uuid'];
          if (kind == 'accounts' && num0(data['opening_balance']) == 0 && num0(other.first['opening_balance']) != 0) {
            await t.update(kind, {'uuid': uuid, 'updated_at': utcNow()}, where: 'id=?', whereArgs: [other.first['id']]);
            await t.delete('sync_dirty', where: 'kind=? AND uuid=?', whereArgs: [kind, oldUuid]);
            await t.insert('sync_dirty', {'kind': kind, 'uuid': uuid}, conflictAlgorithm: ConflictAlgorithm.ignore);
            return 1;
          }
          await t.update(kind, {...vals, 'uuid': uuid, 'updated_at': ua}, where: 'id=?', whereArgs: [other.first['id']]);
          await t.delete('sync_dirty', where: 'kind=? AND uuid=?', whereArgs: [kind, oldUuid]);
          return 1;
        }
      }
      await t.insert(kind, {...vals, 'uuid': uuid, 'updated_at': ua});
      return 1;
    } on DatabaseException {
      return 0;
    }
  }
}
