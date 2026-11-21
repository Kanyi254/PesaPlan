# PesaPlan sync server

A small Flask API that keeps one copy of your PesaPlan records so the desktop app and the phone app stay in step.
It stores records as JSON, resolves conflicts by "last edit wins" per record, relays deletions, and never computes
balances (each device does that itself).

## Try it on your PC (PowerShell)

    cd pesaplan_server
    pip install flask sqlalchemy
    $env:FLASK_APP = "app.py"
    flask run --port 8000

Create your one account (registration closes after the first user):

    Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/auth/register `
      -ContentType "application/json" -Body '{"email":"you@example.com","password":"a long passphrase"}'

Run the tests (needs pesaplan.py and pesaplan_sync.py one folder up):

    $env:PESAPLAN_DIR = ".."
    python test_sync.py

## Put it on a VPS (Linux shell on the server)

    sudo apt install python3-venv postgresql nginx certbot python3-certbot-nginx
    sudo -u postgres createuser pesaplan --pwprompt && sudo -u postgres createdb pesaplan -O pesaplan
    python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
    export DATABASE_URL=postgresql://pesaplan:PASSWORD@localhost/pesaplan
    ./venv/bin/gunicorn -w 2 -b 127.0.0.1:8000 wsgi:app

Then put nginx in front of port 8000 and get a free HTTPS certificate with `certbot --nginx -d your.domain`.
Do not expose it over plain HTTP: the API carries your financial data. Back up the database nightly (`pg_dump`).

## API

| Call | Purpose |
|---|---|
| `POST /api/auth/register` | first account only (set `ALLOW_REGISTER=1` to allow more) |
| `POST /api/auth/login` | email + password + device name -> a per-device token (revocable) |
| `POST /api/auth/logout` | revoke this device's token |
| `POST /api/sync` | push local changes, pull remote ones (`since`, `limit`, `changes[]`) |
| `GET /api/status` | record counts and devices |
| `GET /healthz` | liveness |

Rules the server enforces: tokens are stored hashed; failed logins are throttled; malformed changes are rejected
without affecting the rest; the same M-Pesa code, account name or budget category can only exist once (duplicates
created on two devices collapse into one).

Known limits: conflict resolution uses device clocks (keep phone and PC on automatic time); two edits to the same
record within the same millisecond tie and the server's copy wins.
