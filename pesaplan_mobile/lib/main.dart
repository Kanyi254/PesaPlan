import 'dart:async';

import 'package:flutter/material.dart';

import 'parser.dart';
import 'sms.dart';
import 'store.dart';
import 'sync.dart';

// ------------------------------------------------------------------ theme (same palette as the PC app)
const kBg = Color(0xFF0E1117);
const kCard = Color(0xFF161B22);
const kCard2 = Color(0xFF1D2430);
const kBorder = Color(0xFF252E3C);
const kText = Color(0xFFE6EDF3);
const kMuted = Color(0xFF8B98A9);
const kAccent = Color(0xFF2ECC71);
const kIncome = Color(0xFF34D399);
const kExpense = Color(0xFFF87171);
const kWarn = Color(0xFFFBBF24);
const kBlue = Color(0xFF60A5FA);

const _months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const _days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const _kindIcon = {'Bank': Icons.account_balance, 'M-Pesa': Icons.phone_android, 'Cash': Icons.payments, 'Other': Icons.account_balance_wallet};

String kes(double v, {bool signed = false}) {
  final neg = v < 0;
  final parts = v.abs().toStringAsFixed(2).split('.');
  final g = parts[0].replaceAllMapped(RegExp(r'\B(?=(\d{3})+(?!\d))'), (m) => ',');
  final sign = neg ? '-' : (signed && v > 0 ? '+' : '');
  return '${sign}KSh $g.${parts[1]}';
}

String two(int n) => n.toString().padLeft(2, '0');

String niceTime(DateTime d) {
  final h = d.hour % 12 == 0 ? 12 : d.hour % 12;
  return '${_days[d.weekday - 1]} ${d.day} ${_months[d.month - 1].substring(0, 3)}, $h:${two(d.minute)} ${d.hour < 12 ? 'AM' : 'PM'}';
}

String monthStart(DateTime m) => fmtTs(DateTime(m.year, m.month, 1));
String monthEnd(DateTime m) => fmtTs(DateTime(m.year, m.month + 1, 1));

String localFromUtc(String? iso) {
  if (iso == null || iso.isEmpty) return 'never';
  try {
    final d = DateTime.parse(iso).toLocal();
    return '${d.day} ${_months[d.month - 1].substring(0, 3)} ${two(d.hour)}:${two(d.minute)}';
  } catch (_) {
    return iso;
  }
}

// ------------------------------------------------------------------ app state
class AppState extends ChangeNotifier with WidgetsBindingObserver {
  final Store store;
  DateTime month = DateTime(DateTime.now().year, DateTime.now().month, 1);
  bool syncing = false;
  String syncError = '';
  SyncStats? lastStats;
  String smsMessage = '';
  Timer? _debounce;
  Timer? _periodic;

  AppState(this.store);

  void start() {
    WidgetsBinding.instance.addObserver(this);
    _periodic = Timer.periodic(const Duration(minutes: 5), (_) => syncNow());
    refreshFromPhone();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _periodic?.cancel();
    _debounce?.cancel();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState s) {
    if (s == AppLifecycleState.resumed) refreshFromPhone();
  }

  /// Opening the app: pick up any M-Pesa messages, then sync.
  Future<void> refreshFromPhone() async {
    await catchUpSms();
    await syncNow();
  }

  void changed() {
    notifyListeners();
    _debounce?.cancel();
    _debounce = Timer(const Duration(seconds: 5), syncNow);
  }

  void setMonth(DateTime m) {
    month = DateTime(m.year, m.month, 1);
    notifyListeners();
  }

  Future<bool> signedIn() async => (await store.getSetting('sync_url') ?? '').isNotEmpty && (await store.getSetting('sync_token') ?? '').isNotEmpty;

  Future<void> syncNow() async {
    if (syncing) return;
    final url = await store.getSetting('sync_url') ?? '';
    final tok = await store.getSetting('sync_token') ?? '';
    if (url.isEmpty || tok.isEmpty) return;
    syncing = true;
    notifyListeners();
    try {
      final st = await syncOnce(store, url, tok);
      lastStats = st;
      syncError = '';
    } on SyncException catch (e) {
      syncError = e.message;
      if (e.message.contains('401')) {
        await store.setSetting('sync_token', '');
        syncError = 'The server signed this phone out. Please sign in again.';
      }
    } catch (e) {
      syncError = 'Sync failed';
    } finally {
      syncing = false;
      notifyListeners();
    }
  }

  Future<void> catchUpSms() async {
    if ((await store.getSetting('sms_auto')) == '0') return;
    try {
      final r = await SmsBridge.catchUp(store);
      if (r.imported > 0) {
        smsMessage = 'Imported ${r.imported} new M-Pesa message${r.imported == 1 ? '' : 's'}';
        notifyListeners();
      }
    } catch (_) {
      // no SMS permission yet, or not on Android
    }
  }

  Future<void> signIn(String url, String email, String pw, String device, bool create) async {
    var u = url.trim();
    if (!u.toLowerCase().startsWith('http')) u = 'https://$u';
    u = u.replaceAll(RegExp(r'/+$'), '');
    if (create) await registerAccount(u, email, pw);
    final tok = await loginToServer(u, email, pw, device);
    await store.setSetting('sync_url', u);
    await store.setSetting('sync_email', email);
    await store.setSetting('sync_token', tok);
    await store.setSetting('sync_device', device);
    await store.setSetting('sync_cursor', '0');
    syncError = '';
    notifyListeners();
    unawaited(syncNow());
  }

  Future<void> signOut() async {
    final url = await store.getSetting('sync_url') ?? '';
    final tok = await store.getSetting('sync_token') ?? '';
    if (url.isNotEmpty && tok.isNotEmpty) unawaited(logoutFromServer(url, tok));
    await store.setSetting('sync_token', '');
    await store.setSetting('sync_cursor', '0');
    await store.markAllDirty();
    syncError = '';
    lastStats = null;
    notifyListeners();
  }
}

/// Reloads its data whenever the app state changes.
abstract class LoadingState<T extends StatefulWidget> extends State<T> {
  AppState get app;
  Future<void> load();

  @override
  void initState() {
    super.initState();
    app.addListener(_reload);
    _reload();
  }

  @override
  void dispose() {
    app.removeListener(_reload);
    super.dispose();
  }

  void _reload() {
    load().then((_) {
      if (mounted) setState(() {});
    });
  }
}

// ------------------------------------------------------------------ main
Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final store = await Store.open();
  final app = AppState(store)..start();
  runApp(PesaApp(app));
}

class PesaApp extends StatelessWidget {
  final AppState app;
  const PesaApp(this.app, {super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'PesaPlan',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        brightness: Brightness.dark,
        scaffoldBackgroundColor: kBg,
        colorScheme: const ColorScheme.dark(primary: kAccent, onPrimary: Color(0xFF04210F), surface: kCard, onSurface: kText, secondary: kAccent),
        appBarTheme: const AppBarTheme(backgroundColor: kBg, foregroundColor: kText, elevation: 0),
        navigationBarTheme: const NavigationBarThemeData(backgroundColor: Color(0xFF0A0D12), indicatorColor: Color(0xFF123524)),
        inputDecorationTheme: InputDecorationTheme(
          filled: true,
          fillColor: kCard2,
          border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: kBorder)),
          enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: kBorder)),
        ),
      ),
      home: Shell(app),
    );
  }
}

class Shell extends StatefulWidget {
  final AppState app;
  const Shell(this.app, {super.key});
  @override
  State<Shell> createState() => _ShellState();
}

class _ShellState extends State<Shell> {
  int tab = 0;

  @override
  Widget build(BuildContext context) {
    final pages = [HomePage(widget.app), ActivityPage(widget.app), BudgetsPage(widget.app), SettingsPage(widget.app)];
    return Scaffold(
      body: SafeArea(child: IndexedStack(index: tab, children: pages)),
      floatingActionButton: tab == 3
          ? null
          : FloatingActionButton.extended(
              backgroundColor: kAccent,
              foregroundColor: const Color(0xFF04210F),
              onPressed: () => showEntrySheet(context, widget.app),
              icon: const Icon(Icons.add),
              label: const Text('Add'),
            ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: tab,
        onDestinationSelected: (i) => setState(() => tab = i),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.dashboard_outlined), selectedIcon: Icon(Icons.dashboard), label: 'Home'),
          NavigationDestination(icon: Icon(Icons.receipt_long_outlined), selectedIcon: Icon(Icons.receipt_long), label: 'Activity'),
          NavigationDestination(icon: Icon(Icons.flag_outlined), selectedIcon: Icon(Icons.flag), label: 'Budgets'),
          NavigationDestination(icon: Icon(Icons.sync_outlined), selectedIcon: Icon(Icons.sync), label: 'Sync'),
        ],
      ),
    );
  }
}

// ------------------------------------------------------------------ shared widgets
class CardBox extends StatelessWidget {
  final Widget child;
  final VoidCallback? onTap;
  final EdgeInsets padding;
  const CardBox({super.key, required this.child, this.onTap, this.padding = const EdgeInsets.all(16)});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Material(
        color: kCard,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16), side: const BorderSide(color: kBorder)),
        child: InkWell(borderRadius: BorderRadius.circular(16), onTap: onTap, child: Padding(padding: padding, child: child)),
      ),
    );
  }
}

class MonthBar extends StatelessWidget {
  final AppState app;
  const MonthBar(this.app, {super.key});

  @override
  Widget build(BuildContext context) {
    final m = app.month;
    return Row(children: [
      IconButton(onPressed: () => app.setMonth(DateTime(m.year, m.month - 1, 1)), icon: const Icon(Icons.chevron_left)),
      Expanded(
        child: Text('${_months[m.month - 1]} ${m.year}', textAlign: TextAlign.center, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
      ),
      IconButton(onPressed: () => app.setMonth(DateTime(m.year, m.month + 1, 1)), icon: const Icon(Icons.chevron_right)),
    ]);
  }
}

class SyncPill extends StatelessWidget {
  final AppState app;
  const SyncPill(this.app, {super.key});

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: app,
      builder: (context, _) {
        final bad = app.syncError.isNotEmpty;
        final txt = app.syncing ? 'Syncing...' : (bad ? 'Not synced' : 'Synced');
        final col = app.syncing ? kBlue : (bad ? kExpense : kIncome);
        return GestureDetector(
          onTap: app.syncNow,
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
            decoration: BoxDecoration(color: kCard2, borderRadius: BorderRadius.circular(20), border: Border.all(color: kBorder)),
            child: Row(mainAxisSize: MainAxisSize.min, children: [
              Icon(Icons.circle, size: 8, color: col),
              const SizedBox(width: 6),
              Text(txt, style: const TextStyle(fontSize: 12, color: kMuted)),
            ]),
          ),
        );
      },
    );
  }
}

// ------------------------------------------------------------------ home
class HomePage extends StatefulWidget {
  final AppState app;
  const HomePage(this.app, {super.key});
  @override
  State<HomePage> createState() => _HomeState();
}

class _HomeState extends LoadingState<HomePage> {
  @override
  AppState get app => widget.app;
  List<Rec> accts = [];
  double opening = 0, closing = 0, income = 0, expense = 0;
  bool loaded = false;

  @override
  Future<void> load() async {
    final m = app.month;
    final rows = await app.store.monthSummary(monthStart(m), monthEnd(m));
    var o = 0.0, c = 0.0, i = 0.0, e = 0.0;
    for (final r in rows) {
      o += num0(r['opening']);
      c += num0(r['closing']);
      i += num0(r['income']);
      e += num0(r['expense']);
    }
    accts = rows;
    opening = o;
    closing = c;
    income = i;
    expense = e;
    loaded = true;
  }

  @override
  Widget build(BuildContext context) {
    return ListView(padding: const EdgeInsets.fromLTRB(16, 8, 16, 96), children: [
      Row(children: [
        const Text('PesaPlan', style: TextStyle(fontSize: 22, fontWeight: FontWeight.w800)),
        const Spacer(),
        SyncPill(app),
      ]),
      MonthBar(app),
      if (loaded && accts.isEmpty) _empty(context),
      if (accts.isNotEmpty) ...[
        CardBox(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Text('TOTAL CLOSING BALANCE', style: TextStyle(fontSize: 11, color: kMuted, fontWeight: FontWeight.w700)),
            const SizedBox(height: 4),
            Text(kes(closing), style: const TextStyle(fontSize: 28, fontWeight: FontWeight.w800)),
            const SizedBox(height: 2),
            Text('Opening ${kes(opening)}', style: const TextStyle(color: kMuted)),
            const SizedBox(height: 12),
            Row(children: [
              Expanded(child: _flow('Income', income, kIncome)),
              Expanded(child: _flow('Spent', expense, kExpense)),
            ]),
          ]),
        ),
        for (final a in accts) _accountCard(context, a),
      ],
      if (app.smsMessage.isNotEmpty)
        Padding(padding: const EdgeInsets.only(top: 4), child: Text(app.smsMessage, style: const TextStyle(color: kMuted, fontSize: 12))),
    ]);
  }

  Widget _flow(String label, double v, Color c) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(label, style: const TextStyle(color: kMuted, fontSize: 12)),
        Text(kes(v), style: TextStyle(color: c, fontWeight: FontWeight.w700, fontSize: 16)),
      ]);

  Widget _empty(BuildContext context) {
    return CardBox(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Text('No accounts yet', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
        const SizedBox(height: 6),
        const Text('If you already use PesaPlan on your PC, open the Sync tab and sign in: your accounts and history will arrive. '
            'Or start fresh here with the usual six accounts (everything begins at KSh 0).', style: TextStyle(color: kMuted)),
        const SizedBox(height: 12),
        FilledButton(
          onPressed: () async {
            await app.store.addDefaultAccounts(defaultAccounts);
            app.changed();
          },
          child: const Text('Add M-Pesa, Cash and 4 bank accounts'),
        ),
      ]),
    );
  }

  Widget _accountCard(BuildContext context, Rec a) {
    final kind = (a['kind'] as String?) ?? 'Other';
    return CardBox(
      onTap: () => showAccountSheet(context, app, a),
      child: Row(children: [
        Container(
          width: 42,
          height: 42,
          decoration: BoxDecoration(color: kCard2, borderRadius: BorderRadius.circular(12)),
          child: Icon(_kindIcon[kind] ?? Icons.account_balance_wallet, color: kAccent),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(a['name'] as String, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15)),
            const SizedBox(height: 2),
            Text('Opening ${kes(num0(a['opening']))}', style: const TextStyle(color: kMuted, fontSize: 12)),
            Text(
              'In ${kes(num0(a['income']) + num0(a['tin']))}  ·  Out ${kes(num0(a['expense']) + num0(a['tout']))}',
              style: const TextStyle(color: kMuted, fontSize: 12),
            ),
          ]),
        ),
        Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
          const Text('Closing', style: TextStyle(color: kMuted, fontSize: 11)),
          Text(kes(num0(a['closing'])), style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 16)),
        ]),
      ]),
    );
  }
}

// ------------------------------------------------------------------ activity
class ActivityPage extends StatefulWidget {
  final AppState app;
  const ActivityPage(this.app, {super.key});
  @override
  State<ActivityPage> createState() => _ActivityState();
}

class _ActivityState extends LoadingState<ActivityPage> {
  @override
  AppState get app => widget.app;
  List<Rec> rows = [];

  @override
  Future<void> load() async {
    rows = await app.store.activity(monthStart(app.month), monthEnd(app.month));
  }

  @override
  Widget build(BuildContext context) {
    final children = <Widget>[
      Row(children: [
        const Text('Activity', style: TextStyle(fontSize: 22, fontWeight: FontWeight.w800)),
        const Spacer(),
        SyncPill(app),
      ]),
      MonthBar(app),
    ];
    if (rows.isEmpty) {
      children.add(const Padding(padding: EdgeInsets.all(32), child: Center(child: Text('Nothing recorded this month', style: TextStyle(color: kMuted)))));
    }
    String? lastDay;
    for (final r in rows) {
      final d = parseTs(r['ts'] as String);
      final day = '${_days[d.weekday - 1]} ${d.day} ${_months[d.month - 1]}';
      if (day != lastDay) {
        lastDay = day;
        children.add(Padding(padding: const EdgeInsets.only(top: 10, bottom: 6), child: Text(day.toUpperCase(), style: const TextStyle(fontSize: 11, color: kMuted, fontWeight: FontWeight.w700))));
      }
      children.add(_tile(context, r, d));
    }
    return ListView(padding: const EdgeInsets.fromLTRB(16, 8, 16, 96), children: children);
  }

  Widget _tile(BuildContext context, Rec r, DateTime d) {
    final isTr = r['rowtype'] == 'tr';
    final isIncome = !isTr && r['type'] == 'income';
    final amount = num0(r['amount']);
    final title = isTr ? '${r['from_acc']} → ${r['to_acc']}' : ((r['description'] as String?)?.isNotEmpty == true ? r['description'] as String : r['category'] as String);
    final sub = isTr ? 'Transfer · ${niceTime(d)}' : '${r['category']} · ${r['source']} · ${niceTime(d)}';
    final color = isTr ? kBlue : (isIncome ? kIncome : kExpense);
    final txt = isTr ? kes(amount) : (isIncome ? '+${kes(amount)}' : '-${kes(amount)}');
    return CardBox(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      onTap: () => showEntrySheet(context, app, edit: r),
      child: Row(children: [
        Icon(isTr ? Icons.swap_horiz : (isIncome ? Icons.south_west : Icons.north_east), color: color),
        const SizedBox(width: 12),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(title, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w600)),
            Text(sub, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(color: kMuted, fontSize: 12)),
          ]),
        ),
        const SizedBox(width: 8),
        Text(txt, style: TextStyle(color: color, fontWeight: FontWeight.w800)),
      ]),
    );
  }
}

// ------------------------------------------------------------------ budgets
class BudgetsPage extends StatefulWidget {
  final AppState app;
  const BudgetsPage(this.app, {super.key});
  @override
  State<BudgetsPage> createState() => _BudgetsState();
}

class _BudgetsState extends LoadingState<BudgetsPage> {
  @override
  AppState get app => widget.app;
  List<Rec> budgets = [];
  Map<String, double> spent = {};

  @override
  Future<void> load() async {
    budgets = await app.store.budgets();
    spent = await app.store.spentByCategory(monthStart(app.month), monthEnd(app.month));
  }

  Future<void> _edit(BuildContext context, {String? category, double? limit}) async {
    var cat = category ?? expenseCats.firstWhere((c) => !budgets.any((b) => b['category'] == c), orElse: () => expenseCats.first);
    final ctl = TextEditingController(text: limit == null ? '' : limit.toStringAsFixed(0));
    final res = await showDialog<String>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setS) => AlertDialog(
          backgroundColor: kCard,
          title: Text(category == null ? 'New budget' : 'Edit budget'),
          content: Column(mainAxisSize: MainAxisSize.min, children: [
            DropdownButton<String>(
              isExpanded: true,
              value: cat,
              items: [for (final c in expenseCats) DropdownMenuItem(value: c, child: Text(c))],
              onChanged: category == null ? (v) => setS(() => cat = v ?? cat) : null,
            ),
            const SizedBox(height: 8),
            TextField(controller: ctl, keyboardType: const TextInputType.numberWithOptions(decimal: true), decoration: const InputDecoration(labelText: 'Monthly limit (KSh)')),
          ]),
          actions: [
            if (category != null) TextButton(onPressed: () => Navigator.pop(ctx, 'remove'), child: const Text('Remove', style: TextStyle(color: kExpense))),
            TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(ctx, 'save'), child: const Text('Save')),
          ],
        ),
      ),
    );
    if (res == 'save') {
      final v = double.tryParse(ctl.text.replaceAll(',', '').trim());
      if (v != null && v > 0) {
        await app.store.setBudget(cat, v);
        app.changed();
      }
    } else if (res == 'remove' && category != null) {
      await app.store.removeBudget(category);
      app.changed();
    }
  }

  @override
  Widget build(BuildContext context) {
    final kids = <Widget>[
      Row(children: [
        const Text('Budgets', style: TextStyle(fontSize: 22, fontWeight: FontWeight.w800)),
        const Spacer(),
        IconButton(onPressed: () => _edit(context), icon: const Icon(Icons.add_circle_outline, color: kAccent)),
      ]),
      MonthBar(app),
    ];
    if (budgets.isEmpty) {
      kids.add(const Padding(padding: EdgeInsets.all(32), child: Center(child: Text('No budgets yet. Tap + to set a monthly limit.', style: TextStyle(color: kMuted)))));
    }
    for (final b in budgets) {
      final cat = b['category'] as String;
      final limit = num0(b['monthly_limit']);
      final s = spent[cat] ?? 0;
      final ratio = limit <= 0 ? 0.0 : (s / limit);
      final col = ratio >= 1 ? kExpense : (ratio >= 0.8 ? kWarn : kAccent);
      kids.add(CardBox(
        onTap: () => _edit(context, category: cat, limit: limit),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Expanded(child: Text(cat, style: const TextStyle(fontWeight: FontWeight.w700))),
            Text('${kes(s)} / ${kes(limit)}', style: TextStyle(color: col, fontSize: 12, fontWeight: FontWeight.w600)),
          ]),
          const SizedBox(height: 8),
          ClipRRect(
            borderRadius: BorderRadius.circular(6),
            child: LinearProgressIndicator(value: ratio > 1 ? 1.0 : ratio, minHeight: 8, color: col, backgroundColor: kCard2),
          ),
          const SizedBox(height: 4),
          Text(ratio >= 1 ? 'Over by ${kes(s - limit)}' : '${kes(limit - s)} left', style: const TextStyle(color: kMuted, fontSize: 12)),
        ]),
      ));
    }
    return ListView(padding: const EdgeInsets.fromLTRB(16, 8, 16, 96), children: kids);
  }
}

// ------------------------------------------------------------------ add / edit sheet
Future<void> showEntrySheet(BuildContext context, AppState app, {Rec? edit}) async {
  final names = await app.store.accountNames();
  if (!context.mounted) return;
  if (names.isEmpty) {
    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Add an account first (Home tab).')));
    return;
  }
  await showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    backgroundColor: kCard,
    shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
    builder: (ctx) => Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(ctx).viewInsets.bottom),
      child: EntrySheet(app: app, names: names, edit: edit),
    ),
  );
}

class EntrySheet extends StatefulWidget {
  final AppState app;
  final List<String> names;
  final Rec? edit;
  const EntrySheet({super.key, required this.app, required this.names, this.edit});
  @override
  State<EntrySheet> createState() => _EntryState();
}

class _EntryState extends State<EntrySheet> {
  String mode = 'expense'; // expense | income | transfer
  final amount = TextEditingController();
  final desc = TextEditingController();
  late String category;
  late String account;
  late String toAccount;
  DateTime when = DateTime.now();
  String error = '';

  bool get editing => widget.edit != null;

  @override
  void initState() {
    super.initState();
    final names = widget.names;
    account = names.first;
    toAccount = names.length > 1 ? names[1] : names.first;
    category = expenseCats.first;
    final e = widget.edit;
    if (e != null) {
      when = parseTs(e['ts'] as String);
      amount.text = num0(e['amount']).toString().replaceAll(RegExp(r'\.0$'), '');
      if (e['rowtype'] == 'tr') {
        mode = 'transfer';
        desc.text = (e['note'] as String?) ?? '';
        account = _pick(e['from_acc'] as String, names);
        toAccount = _pick(e['to_acc'] as String, names);
      } else {
        mode = e['type'] as String;
        desc.text = (e['description'] as String?) ?? '';
        category = e['category'] as String;
        account = _pick(e['source'] as String? ?? names.first, names);
      }
    } else {
      final cash = widget.names.contains('M-Pesa') ? 'M-Pesa' : names.first;
      account = cash;
    }
  }

  String _pick(String n, List<String> names) => names.contains(n) ? n : names.first;

  List<String> get cats {
    final base = mode == 'income' ? incomeCats : expenseCats;
    // keep an unusual category (e.g. made on the PC) selectable while editing
    return (editing && !base.contains(category) && mode != 'transfer') ? [...base, category] : base;
  }

  Future<void> _pickDate() async {
    final d = await showDatePicker(context: context, initialDate: when, firstDate: DateTime(2020), lastDate: DateTime(2100));
    if (d == null || !mounted) return;
    final t = await showTimePicker(context: context, initialTime: TimeOfDay(hour: when.hour, minute: when.minute));
    if (!mounted) return;
    setState(() => when = DateTime(d.year, d.month, d.day, t?.hour ?? when.hour, t?.minute ?? when.minute));
  }

  Future<void> _save() async {
    final v = double.tryParse(amount.text.replaceAll(',', '').trim());
    if (v == null || v <= 0) {
      setState(() => error = 'Enter an amount above 0');
      return;
    }
    final store = widget.app.store;
    final ts = fmtTs(DateTime(when.year, when.month, when.day, when.hour, when.minute, editing ? when.second : 0));
    try {
      if (mode == 'transfer') {
        if (account == toAccount) {
          setState(() => error = 'Pick two different accounts');
          return;
        }
        if (editing) {
          await store.updateTransfer(widget.edit!['id'] as int, ts, account, toAccount, v, desc.text.trim());
        } else {
          await store.addTransfer(ts, account, toAccount, v, desc.text.trim());
        }
      } else {
        final cat = cats.contains(category) ? category : cats.first;
        final d = desc.text.trim();
        if (editing) {
          await store.updateTx(widget.edit!['id'] as int, ts, mode, v, cat, d, account);
        } else {
          await store.addTx(ts, mode, v, cat, d, account);
        }
      }
    } catch (e) {
      setState(() => error = 'Could not save ($e)');
      return;
    }
    widget.app.changed();
    if (mounted) Navigator.pop(context);
  }

  Future<void> _delete() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: kCard,
        title: const Text('Delete this entry?'),
        content: const Text('It will also disappear from your other devices.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          FilledButton(style: FilledButton.styleFrom(backgroundColor: kExpense), onPressed: () => Navigator.pop(ctx, true), child: const Text('Delete')),
        ],
      ),
    );
    if (ok != true) return;
    final e = widget.edit!;
    if (e['rowtype'] == 'tr') {
      await widget.app.store.deleteTransfer(e['id'] as int);
    } else {
      await widget.app.store.deleteTx(e['id'] as int);
    }
    widget.app.changed();
    if (mounted) Navigator.pop(context);
  }

  Widget _drop(String label, String value, List<String> items, void Function(String) on) {
    return InputDecorator(
      decoration: InputDecoration(labelText: label, contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4)),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<String>(
          isExpanded: true,
          value: items.contains(value) ? value : items.first,
          dropdownColor: kCard2,
          items: [for (final i in items) DropdownMenuItem(value: i, child: Text(i))],
          onChanged: (v) {
            if (v != null) on(v);
          },
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final isTr = mode == 'transfer';
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 20),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text(editing ? 'Edit entry' : 'New entry', style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
        const SizedBox(height: 12),
        if (!editing)
          SegmentedButton<String>(
            segments: const [
              ButtonSegment(value: 'expense', label: Text('Expense')),
              ButtonSegment(value: 'income', label: Text('Income')),
              ButtonSegment(value: 'transfer', label: Text('Transfer')),
            ],
            selected: {mode},
            onSelectionChanged: (s) => setState(() {
              mode = s.first;
              if (!cats.contains(category)) category = cats.first;
            }),
          ),
        const SizedBox(height: 12),
        TextField(
          controller: amount,
          autofocus: !editing,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w800),
          decoration: const InputDecoration(labelText: 'Amount (KSh)'),
        ),
        const SizedBox(height: 10),
        if (!isTr) _drop('Category', category, cats, (v) => setState(() => category = v)),
        if (!isTr) const SizedBox(height: 10),
        _drop(isTr ? 'From' : 'Account', account, widget.names, (v) => setState(() => account = v)),
        const SizedBox(height: 10),
        if (isTr) _drop('To', toAccount, widget.names, (v) => setState(() => toAccount = v)),
        if (isTr) const SizedBox(height: 10),
        TextField(controller: desc, decoration: InputDecoration(labelText: isTr ? 'Note (optional)' : 'Description (optional)')),
        const SizedBox(height: 10),
        OutlinedButton.icon(onPressed: _pickDate, icon: const Icon(Icons.schedule), label: Text(niceTime(when))),
        if (error.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 8), child: Text(error, style: const TextStyle(color: kExpense))),
        const SizedBox(height: 14),
        Row(children: [
          if (editing) TextButton(onPressed: _delete, child: const Text('Delete', style: TextStyle(color: kExpense))),
          const Spacer(),
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          const SizedBox(width: 8),
          FilledButton(onPressed: _save, child: const Text('Save')),
        ]),
      ]),
    );
  }
}

// ------------------------------------------------------------------ account sheet (opening balance)
Future<void> showAccountSheet(BuildContext context, AppState app, Rec a) async {
  final ctl = TextEditingController(text: num0(a['opening_balance']).toString().replaceAll(RegExp(r'\.0$'), ''));
  var kind = (a['kind'] as String?) ?? 'Bank';
  var opened = parseTs(a['opened_on'] as String);
  await showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    backgroundColor: kCard,
    shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
    builder: (ctx) => StatefulBuilder(
      builder: (ctx, setS) => Padding(
        padding: EdgeInsets.fromLTRB(20, 16, 20, 20 + MediaQuery.of(ctx).viewInsets.bottom),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text(a['name'] as String, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
          const SizedBox(height: 4),
          const Text('Only money moving on or after the "opened on" date counts, so set the opening balance as it was on that day.', style: TextStyle(color: kMuted, fontSize: 12)),
          const SizedBox(height: 12),
          TextField(controller: ctl, keyboardType: const TextInputType.numberWithOptions(decimal: true, signed: true), decoration: const InputDecoration(labelText: 'Opening balance (KSh)')),
          const SizedBox(height: 10),
          InputDecorator(
            decoration: const InputDecoration(labelText: 'Type', contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 4)),
            child: DropdownButtonHideUnderline(
              child: DropdownButton<String>(
                isExpanded: true,
                value: accountKinds.contains(kind) ? kind : 'Other',
                dropdownColor: kCard2,
                items: [for (final k in accountKinds) DropdownMenuItem(value: k, child: Text(k))],
                onChanged: (v) => setS(() => kind = v ?? kind),
              ),
            ),
          ),
          const SizedBox(height: 10),
          OutlinedButton.icon(
            icon: const Icon(Icons.event),
            label: Text('Opened on ${opened.day} ${_months[opened.month - 1]} ${opened.year}'),
            onPressed: () async {
              final d = await showDatePicker(context: ctx, initialDate: opened, firstDate: DateTime(2015), lastDate: DateTime(2100));
              if (d != null) setS(() => opened = DateTime(d.year, d.month, d.day));
            },
          ),
          const SizedBox(height: 14),
          Row(children: [
            const Spacer(),
            TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
            const SizedBox(width: 8),
            FilledButton(
              onPressed: () async {
                final v = double.tryParse(ctl.text.replaceAll(',', '').trim());
                if (v == null) return;
                await app.store.updateAccount(a['id'] as int, kind: kind, opening: v, openedOn: opened);
                app.changed();
                if (ctx.mounted) Navigator.pop(ctx);
              },
              child: const Text('Save'),
            ),
          ]),
        ]),
      ),
    ),
  );
}

// ------------------------------------------------------------------ sync + SMS settings
class SettingsPage extends StatefulWidget {
  final AppState app;
  const SettingsPage(this.app, {super.key});
  @override
  State<SettingsPage> createState() => _SettingsState();
}

class _SettingsState extends LoadingState<SettingsPage> {
  @override
  AppState get app => widget.app;
  final url = TextEditingController();
  final email = TextEditingController();
  final pw = TextEditingController();
  final device = TextEditingController(text: 'My phone');
  bool signed = false;
  bool busy = false;
  String formMsg = '';
  String lastOk = '';
  int unsent = 0;
  bool smsPerm = false;
  bool smsAuto = true;
  String smsAccount = '';
  List<String> accountNames = [];
  String smsResult = '';
  bool urlFilled = false;

  @override
  Future<void> load() async {
    final s = app.store;
    signed = await app.signedIn();
    if (!urlFilled) {
      url.text = await s.getSetting('sync_url') ?? '';
      email.text = await s.getSetting('sync_email') ?? '';
      urlFilled = true;
    }
    lastOk = await s.getSetting('sync_last_ok') ?? '';
    unsent = await s.unsent();
    accountNames = await s.accountNames();
    smsAuto = (await s.getSetting('sms_auto')) != '0';
    smsAccount = await s.getSetting('sms_account') ?? '';
    try {
      smsPerm = await SmsBridge.hasPermission();
    } catch (_) {
      smsPerm = false;
    }
  }

  Future<void> _signIn(bool create) async {
    if (url.text.trim().isEmpty || email.text.trim().isEmpty || pw.text.isEmpty) {
      setState(() => formMsg = 'Fill in the server address, email and password.');
      return;
    }
    setState(() {
      busy = true;
      formMsg = 'Connecting...';
    });
    try {
      await app.signIn(url.text, email.text.trim(), pw.text, device.text.trim().isEmpty ? 'Phone' : device.text.trim(), create);
      pw.clear();
      formMsg = '';
    } on SyncException catch (e) {
      formMsg = e.message;
    } catch (e) {
      formMsg = 'Could not sign in';
    }
    if (mounted) setState(() => busy = false);
  }

  Future<void> _pasteDialog(BuildContext context) async {
    final ctl = TextEditingController();
    final go = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: kCard,
        title: const Text('Paste M-Pesa messages'),
        content: TextField(controller: ctl, maxLines: 8, decoration: const InputDecoration(hintText: 'Paste one or many messages')),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Import')),
        ],
      ),
    );
    if (go != true) return;
    final r = await importMessages(app.store, [ctl.text]);
    setState(() => smsResult = r.error ?? 'Imported ${r.imported}, skipped ${r.duplicates} already saved, ${r.unrecognised.length} not recognised');
    if (r.imported > 0) app.changed();
  }

  @override
  Widget build(BuildContext context) {
    final kids = <Widget>[
      const Text('Sync & SMS', style: TextStyle(fontSize: 22, fontWeight: FontWeight.w800)),
      const SizedBox(height: 12),
    ];
    if (!signed) {
      kids.add(CardBox(
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          const Text('Connect to your server', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
          const SizedBox(height: 4),
          const Text('Use the https address from your home-server setup (needs the Tailscale app running on this phone).', style: TextStyle(color: kMuted, fontSize: 12)),
          const SizedBox(height: 12),
          TextField(controller: url, keyboardType: TextInputType.url, autocorrect: false, decoration: const InputDecoration(labelText: 'Server address', hintText: 'https://pesaserver.your-tailnet.ts.net')),
          const SizedBox(height: 10),
          TextField(controller: email, keyboardType: TextInputType.emailAddress, autocorrect: false, decoration: const InputDecoration(labelText: 'Email')),
          const SizedBox(height: 10),
          TextField(controller: pw, obscureText: true, decoration: const InputDecoration(labelText: 'Password')),
          const SizedBox(height: 10),
          TextField(controller: device, decoration: const InputDecoration(labelText: 'Name of this device')),
          const SizedBox(height: 12),
          Row(children: [
            Expanded(child: FilledButton(onPressed: busy ? null : () => _signIn(false), child: const Text('Sign in'))),
            const SizedBox(width: 10),
            Expanded(child: OutlinedButton(onPressed: busy ? null : () => _signIn(true), child: const Text('Create account'))),
          ]),
          if (formMsg.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 8), child: Text(formMsg, style: TextStyle(color: formMsg == 'Connecting...' ? kMuted : kExpense))),
        ]),
      ));
    } else {
      final bad = app.syncError.isNotEmpty;
      kids.add(CardBox(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(app.syncing ? 'Syncing...' : (bad ? 'Not synced: ${app.syncError}' : (unsent == 0 ? 'Up to date' : 'Changes waiting to send')),
              style: TextStyle(fontWeight: FontWeight.w800, fontSize: 16, color: app.syncing ? kBlue : (bad ? kExpense : kIncome))),
          const SizedBox(height: 6),
          Text('Last synced: ${localFromUtc(lastOk)}', style: const TextStyle(color: kMuted)),
          if (app.lastStats != null) Text('Last run: sent ${app.lastStats!.pushed}, received ${app.lastStats!.pulled}', style: const TextStyle(color: kMuted)),
          const SizedBox(height: 12),
          Row(children: [
            FilledButton(onPressed: app.syncing ? null : app.syncNow, child: const Text('Sync now')),
            const SizedBox(width: 10),
            TextButton(onPressed: app.signOut, child: const Text('Sign out')),
          ]),
        ]),
      ));
    }

    kids.add(CardBox(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Text('M-Pesa SMS', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
        const SizedBox(height: 4),
        Text(
          smsPerm
              ? 'PesaPlan reads M-Pesa messages as they arrive, even when the app is closed, and when you open it.'
              : 'Allow SMS access so PesaPlan can record M-Pesa messages automatically. Messages never leave your phone except to your own server.',
          style: const TextStyle(color: kMuted, fontSize: 12),
        ),
        const SizedBox(height: 10),
        if (!smsPerm)
          FilledButton(
            onPressed: () async {
              try {
                await SmsBridge.requestPermission();
              } catch (_) {}
              await app.catchUpSms();
              app.changed();
            },
            child: const Text('Allow SMS access'),
          ),
        if (smsPerm)
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Import automatically'),
            value: smsAuto,
            onChanged: (v) async {
              await app.store.setSetting('sms_auto', v ? '1' : '0');
              setState(() => smsAuto = v);
            },
          ),
        if (accountNames.isNotEmpty)
          InputDecorator(
            decoration: const InputDecoration(labelText: 'Save M-Pesa messages into', contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 4)),
            child: DropdownButtonHideUnderline(
              child: DropdownButton<String>(
                isExpanded: true,
                dropdownColor: kCard2,
                value: accountNames.contains(smsAccount) ? smsAccount : accountNames.first,
                items: [for (final n in accountNames) DropdownMenuItem(value: n, child: Text(n))],
                onChanged: (v) async {
                  if (v == null) return;
                  await app.store.setSetting('sms_account', v);
                  setState(() => smsAccount = v);
                },
              ),
            ),
          ),
        const SizedBox(height: 10),
        Wrap(spacing: 10, children: [
          OutlinedButton(
            onPressed: () async {
              try {
                final r = await SmsBridge.catchUp(app.store);
                setState(() => smsResult = r.error ?? 'Imported ${r.imported}, skipped ${r.duplicates} already saved');
                if (r.imported > 0) app.changed();
              } catch (e) {
                setState(() => smsResult = 'Could not read messages (allow SMS access first)');
              }
            },
            child: const Text('Import now'),
          ),
          OutlinedButton(onPressed: () => _pasteDialog(context), child: const Text('Paste messages')),
        ]),
        if (smsResult.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 8), child: Text(smsResult, style: const TextStyle(color: kMuted, fontSize: 12))),
      ]),
    ));

    return AnimatedBuilder(
      animation: app,
      builder: (context, _) => ListView(padding: const EdgeInsets.fromLTRB(16, 8, 16, 32), children: kids),
    );
  }
}
