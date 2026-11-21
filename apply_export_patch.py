"""
apply_export_patch.py - adds "Download Excel" to pesaplan_phone.py (the phone web page).

Run once, in the folder that holds pesaplan_phone.py:
    python apply_export_patch.py
It keeps a backup (pesaplan_phone.py.bak), checks every spot before touching anything, and is safe to re-run.
"""
import os
import sys

PATH = "pesaplan_phone.py"

EDITS = [
    # 1) route: /api/export streams a file instead of JSON
    ("""                out = self._api(method, path, q)
                self._json(out)
""", """                if method == "GET" and path == "/api/export":
                    return self._export(q)
                out = self._api(method, path, q)
                self._json(out)
"""),
    # 2) the handler method
    ("""        def do_GET(self):
            self._route("GET")
""", """        def _export(self, q):
            try:
                import pesaplan_export
                data, name = pesaplan_export.from_query(ctx.db, q)
            except ImportError:
                raise ApiError(501, "Put pesaplan_export.py next to pesaplan.py on the PC, then restart PesaPlan")
            except pesaplan_export.ExportError as e:
                raise ApiError(400, str(e).replace("\\n", " "))
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % name)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._route("GET")
"""),
    # 3) header button on the web page
    ("""<button class="ibtn" onclick="openSms()" title="Paste M-Pesa SMS">📲</button>""",
     """<button class="ibtn" onclick="openExport()" title="Download Excel">⬇</button><button class="ibtn" onclick="openSms()" title="Paste M-Pesa SMS">📲</button>"""),
    # 4) the download sheet
    ("""function openSms(){
""", """function openExport(){
  const cur=st.month;
  sheet(`<h2>Download Excel</h2><p class="sub">Income, expenses, transfers and account balances as a spreadsheet.</p>
  <label>Period</label><select id="x_rng"><option value="month">${esc(monthLabel(cur))} (month you're viewing)</option><option value="last month">Last month</option><option value="last 30 days">Last 30 days</option><option value="this year">This year</option><option value="last year">Last year</option><option value="all">All time</option><option value="custom">Custom dates…</option></select>
  <div id="x_cust" style="display:none"><label>From</label><input type="date" id="x_from"><label>To</label><input type="date" id="x_to"></div>
  <div class="btns"><button class="btn" id="cancel">Cancel</button><button class="btn p" id="save">Download</button></div>`);
  const d=nowLocal().slice(0,10);$("#x_from").value=d.slice(0,8)+"01";$("#x_to").value=d;
  $("#x_rng").onchange=()=>{$("#x_cust").style.display=$("#x_rng").value==="custom"?"block":"none"};
  $("#cancel").onclick=closeSheet;
  $("#save").onclick=()=>{const r=$("#x_rng").value;
    let u="/api/export?t="+encodeURIComponent(TOKEN)+(r==="month"?"&month="+cur:"&range="+encodeURIComponent(r));
    if(r==="custom")u+="&from="+$("#x_from").value+"&to="+$("#x_to").value;
    const a=document.createElement("a");a.href=u;a.download="";document.body.appendChild(a);a.click();a.remove();closeSheet();toast("Preparing your file…")};
}
function openSms(){
"""),
]


def main():
    if not os.path.exists(PATH):
        sys.exit("Can't find %s here. Run this from the folder that contains it." % PATH)
    src = open(PATH, encoding="utf-8").read()
    if "def _export(self, q):" in src:
        print("Already patched - nothing to do.")
        return
    for i, (old, _) in enumerate(EDITS, 1):
        n = src.count(old)
        if n != 1:
            sys.exit("Edit %d: expected to find its anchor text once but found it %d times. Nothing was changed.\n"
                     "(Your pesaplan_phone.py may differ from the one this patch was written for - send it to me.)" % (i, n))
    for old, new in EDITS:
        src = src.replace(old, new, 1)
    open(PATH + ".bak", "w", encoding="utf-8").write(open(PATH, encoding="utf-8").read())
    open(PATH, "w", encoding="utf-8").write(src)
    print("Patched. Backup saved as %s.bak. Restart PesaPlan and reload the phone page." % PATH)


if __name__ == "__main__":
    main()
