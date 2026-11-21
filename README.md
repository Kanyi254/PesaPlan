# PesaPlan

**Personal finance, made easier.**

PesaPlan is a desktop application for managing personal finances. It helps you organise accounts, track income and expenses, manage budgets, import M-Pesa transactions, and monitor your financial position.

[**Download PesaPlan for Windows**](https://github.com/Kanyi254/PesaPlan/releases/latest/download/PesaPlan_Setup.exe) · [View all releases](https://github.com/Kanyi254/PesaPlan/releases)

## Features

- **Dashboard:** Review your financial position and visual summaries.
- **Accounts:** Organise and track your financial accounts.
- **Transactions:** Record and manage income and expenses.
- **Budgets:** Set budgets and monitor spending.
- **M-Pesa Import:** Import supported M-Pesa SMS transaction messages.
- **Phone Integration:** Connect supported phone functionality.
- **Excel Export:** Export financial information to Excel.
- **Sync:** Use the application's synchronisation functionality.

## Screenshots

Explore the PesaPlan desktop interface.

### Dashboard

![PesaPlan Dashboard](screenshots/PesaPlan%20-%20Budget%20Tracker%2021_11_2026%2023_03_16.png)

![PesaPlan Dashboard Details](screenshots/PesaPlan%20-%20Budget%20Tracker%2021_11_2026%2023_03_30.png)

### Accounts

![PesaPlan Accounts](screenshots/PesaPlan%20-%20Budget%20Tracker%2021_11_2026%2023_03_41.png)

### Budgets

![PesaPlan Budgets](screenshots/PesaPlan%20-%20Budget%20Tracker%2021_11_2026%2023_04_41.png)

### M-Pesa Import

![PesaPlan M-Pesa Import](screenshots/PesaPlan%20-%20Budget%20Tracker%2021_11_2026%2023_04_49.png)

### Phone Integration

![PesaPlan Phone Integration](screenshots/PesaPlan%20-%20Budget%20Tracker%2021_11_2026%2023_05_15.png)

### Excel Export

![PesaPlan Excel Export](screenshots/PesaPlan%20-%20Budget%20Tracker%2021_11_2026%2023_05_23.png)

### Synchronisation

![PesaPlan Synchronisation](screenshots/PesaPlan%20-%20Budget%20Tracker%2021_11_2026%2023_05_34.png)

## Installation

### Windows (Recommended for beginners)

**Requirements:** Windows 10 or later, 64-bit.

You do not need Python, Conda, pip, or developer tools.

1. Download [PesaPlan_Setup.exe](https://github.com/Kanyi254/PesaPlan/releases/latest/download/PesaPlan_Setup.exe).
2. Open your Downloads folder.
3. Double-click `PesaPlan_Setup.exe`.
4. Follow the installation wizard.
5. Launch PesaPlan from the Start Menu or desktop shortcut.

Only download the installer from the official PesaPlan GitHub Releases page.

**Windows SmartScreen:** The installer is currently unsigned. Windows may display a security warning. Verify that you downloaded it from this official repository before deciding whether to continue.

### Windows data location

PesaPlan stores its local SQLite database at:

```text
%LOCALAPPDATA%\PesaPlan\pesaplan.db
```

To open the folder:

1. Press `Win + R`.
2. Enter `%LOCALAPPDATA%\PesaPlan`.
3. Press Enter.

Your database is separate from the application installation directory. Ordinary uninstall operations are configured not to remove it.

Back up your database before reinstalling, updating, or moving computers.

### Linux

A packaged Linux release is not yet published.

Linux users can currently run PesaPlan from source.

Requirements:

- Python 3
- Python virtual environment support (`venv`)

```bash
cd /path/to/PesaPlan

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip

python -m pip install PyQt5==5.15.11 openpyxl==3.1.5 "qrcode[pil]==8.2"

python pesaplan.py
```

When finished:

```bash
deactivate
```

A separate Linux package will be published when available.

## Run from Source

The main application entry point is:

```text
pesaplan.py
```

### Supporting modules

| File | Purpose |
|---|---|
| `pesaplan.py` | Main desktop application |
| `pesaplan_phone.py` | Phone integration |
| `pesaplan_export.py` | Excel export |
| `pesaplan_sync.py` | Synchronisation logic |
| `pesaplan_syncui.py` | Synchronisation interface |

### Windows development setup

Requires Python 3.12 (64-bit).

```bat
py -3.12 -m venv .venv

.venv\Scripts\activate

python -m pip install --upgrade pip

python -m pip install PyQt5==5.15.11 openpyxl==3.1.5 "qrcode[pil]==8.2"

python pesaplan.py
```

When finished:

```bat
deactivate
```

### Developer notes

- Use a virtual environment to isolate dependencies.
- Do not commit or distribute personal `pesaplan.db` files.
- Do not distribute SQLite `-wal` or `-shm` files.
- Keep database backups private and secure.
- Never include personal financial records in bug reports.

### Windows packaging

The [Windows Packaging Kit](PesaPlan_Windows_Packaging_Kit/) contains the resources used to build the Windows application:

- PyInstaller specification.
- Windows build script.
- Inno Setup installer script.
- Windows requirements file.
- Packaging instructions.

The Windows release is built using Python 3.12 (64-bit).

## Data Backup and Safety

Your financial records are stored locally in an SQLite database.

Before updating, reinstalling, troubleshooting, or moving to another computer:

1. Close PesaPlan.
2. Locate your database file.
3. Copy it to a safe backup location.
4. Keep the backup private.

**Windows database:**

```text
%LOCALAPPDATA%\PesaPlan\pesaplan.db
```

**Linux source database:**

```text
pesaplan.db
```

Do not delete your database unless you intend to remove your local financial records.

## Troubleshooting

### PesaPlan does not open on Windows

- Try launching it from the Start Menu.
- Confirm the installer came from the official Releases page.
- Restart Windows and try again.
- Include your Windows version and exact error message when requesting help.

### Python or py is not recognised

Python is required only when running from source. Install Python 3.12 and the Python Launcher.

The Windows installer does not require a separate Python installation.

### A Python module is missing

Activate your virtual environment and install the dependencies listed in the source installation instructions.

### Report a problem

When reporting a problem, include:

- Your operating system and version.
- How you installed PesaPlan.
- What you were doing when the problem occurred.
- The complete error message.

Never include passwords, access tokens, or private financial information.

## Releases

Official application builds are published on GitHub:

[**PesaPlan Releases**](https://github.com/Kanyi254/PesaPlan/releases)

Current release:

- **Version:** 1.0.0
- **Platform:** Windows 10 or later (64-bit)
- **Installer:** `PesaPlan_Setup.exe`

[Download PesaPlan 1.0.0 for Windows](https://github.com/Kanyi254/PesaPlan/releases/latest/download/PesaPlan_Setup.exe)

Linux packaging is planned as a separate release.

---

**PesaPlan — Personal finance, made easier.**
