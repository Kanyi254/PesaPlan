// Talks to the PesaPlan sync server. Port of sync_once / login in pesaplan_sync.py.
import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'store.dart';

class SyncException implements Exception {
  final String message;
  SyncException(this.message);
  @override
  String toString() => message;
}

class SyncStats {
  int pushed = 0, pulled = 0, rounds = 0;
}

Future<Map<String, dynamic>> _post(String base, String path, Map<String, Object?> payload, {String token = ''}) async {
  final uri = Uri.parse(base.replaceAll(RegExp(r'/+$'), '') + path);
  http.Response r;
  try {
    r = await http
        .post(uri,
            headers: {'Content-Type': 'application/json', if (token.isNotEmpty) 'Authorization': 'Bearer $token'},
            body: jsonEncode(payload))
        .timeout(const Duration(seconds: 30));
  } on TimeoutException {
    throw SyncException("The server didn't answer in time");
  } catch (e) {
    throw SyncException("Can't reach the sync server");
  }
  Map<String, dynamic> body = {};
  try {
    final d = jsonDecode(utf8.decode(r.bodyBytes));
    if (d is Map<String, dynamic>) body = d;
  } catch (_) {}
  if (r.statusCode != 200) {
    final msg = body['error'] is String ? ': ${body['error']}' : '';
    throw SyncException('Server said ${r.statusCode}$msg');
  }
  return body;
}

Future<void> registerAccount(String url, String email, String password) async {
  await _post(url, '/api/auth/register', {'email': email, 'password': password});
}

Future<String> loginToServer(String url, String email, String password, String device) async {
  final r = await _post(url, '/api/auth/login', {'email': email, 'password': password, 'device_name': device});
  final t = r['token'];
  if (t is! String || t.isEmpty) throw SyncException('The server sent no token');
  return t;
}

Future<void> logoutFromServer(String url, String token) async {
  try {
    await _post(url, '/api/auth/logout', {}, token: token);
  } catch (_) {}
}

/// Push local changes, pull remote ones, repeat until both sides agree.
Future<SyncStats> syncOnce(Store s, String url, String token, {int batch = 500, int maxRounds = 200}) async {
  final stats = SyncStats();
  var cursor = int.tryParse(await s.getSetting('sync_cursor') ?? '0') ?? 0;
  if (cursor == 0) {
    // brand-new device (or after sign-out): look at what the server has before sending anything
    while (true) {
      stats.rounds++;
      final resp = await _post(url, '/api/sync', {'since': cursor, 'changes': <Object>[], 'limit': batch}, token: token);
      stats.pulled += await s.applyChanges((resp['changes'] as List?) ?? []);
      cursor = (resp['cursor'] as num).toInt();
      await s.setSetting('sync_cursor', '$cursor');
      if (resp['more'] != true) break;
    }
  }
  var todo = await s.pending();
  while (true) {
    stats.rounds++;
    if (stats.rounds > maxRounds) throw SyncException('Sync did not settle - try again');
    final chunk = todo.take(batch).toList();
    todo = todo.skip(batch).toList();
    final resp = await _post(url, '/api/sync', {'since': cursor, 'changes': chunk, 'limit': batch}, token: token);
    if (chunk.isNotEmpty) {
      await s.markPushed(chunk);
      stats.pushed += chunk.length;
    }
    stats.pulled += await s.applyChanges((resp['changes'] as List?) ?? []);
    cursor = (resp['cursor'] as num).toInt();
    await s.setSetting('sync_cursor', '$cursor');
    if (resp['more'] != true && todo.isEmpty) break;
  }
  await s.setSetting('sync_last_ok', utcNow());
  return stats;
}
