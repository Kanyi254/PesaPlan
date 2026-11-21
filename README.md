# PesaPlan

**Personal finance, made easier.**

PesaPlan is a desktop application for managing personal finances. This
repository contains the application source and packaging resources.

[**Download PesaPlan for Windows --- GitHub
Releases**](https://github.com/Kanyi254/PesaPlan/releases/latest)

> Download the latest Windows installer from GitHub Releases. The direct
> installer download becomes available after `PesaPlan_Setup.exe` is
> uploaded as a release asset.

## Choose how to use PesaPlan

  ------------------------------------------------------------------------------------
  User                                Instructions
  ----------------------------------- ------------------------------------------------
  Windows beginner                    [Windows
                                      installation](#windows-installation-beginners)

  Windows Command Prompt user         [Windows Command
                                      Prompt](#windows-command-prompt)

  Linux user                          [Linux installation](#linux-installation)

  Developer                           [Run from source](#run-from-source)
  ------------------------------------------------------------------------------------

## Windows installation (beginners)

**Requirements:** Windows 10 or later, 64-bit, and `PesaPlan_Setup.exe`
from the [official Releases
page](https://github.com/Kanyi254/PesaPlan/releases).

You do **not** need Python, Conda, pip, or developer tools.

1.  Open the [PesaPlan Releases
    page](https://github.com/Kanyi254/PesaPlan/releases).
2.  Download `PesaPlan_Setup.exe` from the latest release.
3.  Open Downloads and double-click the installer.
4.  Follow the setup wizard. Select the desktop shortcut option if
    desired.
5.  Finish setup and launch PesaPlan from the Start Menu or desktop
    shortcut.

Only use the installer from this repository's official Releases page.
The installer is unsigned and may trigger a Windows SmartScreen warning.
Verify its source before deciding whether to continue.

### Windows data location

The local SQLite database is stored at:

``` text
%LOCALAPPDATA%\PesaPlan\pesaplan.db
```

Press `Win + R`, enter `%LOCALAPPDATA%\PesaPlan`, and press Enter to
open the folder.

The database is separate from program files. The installer is configured
not to remove it during an ordinary uninstall. Back it up before
reinstalling, updating, or moving computers. Do not manually delete this
folder unless you intend to remove your local records.

## Windows Command Prompt

### Launch the installed application

With the default installation location, run:

``` bat
"%LOCALAPPDATA%\Programs\PesaPlan\PesaPlan.exe"
```

Adjust the path if you chose a different installation folder.

### Run from source

For developers and advanced users. Requires Python 3.12 (64-bit).

``` bat
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install PyQt5==5.15.11 openpyxl==3.1.5 "qrcode[pil]==8.2"
python pesaplan.py
```

When finished, run `deactivate`.

## Linux installation

Linux users can run a packaged Linux build when published, or run from
source.

### Packaged Linux build

1.  Download the Linux archive from [PesaPlan
    Releases](https://github.com/Kanyi254/PesaPlan/releases).
2.  Extract it and open a terminal in the extracted folder.
3.  For the current folder-based PyInstaller layout, run:

``` bash
./dist/PesaPlan/PesaPlan
```

If permission is denied:

``` bash
chmod +x ./dist/PesaPlan/PesaPlan
```

Keep the `_internal` directory alongside the executable. Linux builds
are separate from Windows installers.

### Run from source

Requires Python 3 and `venv`.

``` bash
cd /path/to/PesaPlan
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install PyQt5==5.15.11 openpyxl==3.1.5 "qrcode[pil]==8.2"
python pesaplan.py
```

When finished:

``` bash
deactivate
```

If your distribution does not include `venv`, install its Python
virtual-environment package using your package manager.

### Linux data location

In the current source configuration, the SQLite database is stored
beside the source application:

``` text
pesaplan.db
```

Keep this file safe to retain your records.

## Run from source

The main entry point is `pesaplan.py`. Supporting modules:

  File                   Purpose
  ---------------------- --------------------------
  `pesaplan.py`          Main desktop application
  `pesaplan_phone.py`    Phone integration
  `pesaplan_export.py`   Excel export
  `pesaplan_sync.py`     Synchronisation logic
  `pesaplan_syncui.py`   Sync interface

PesaPlan uses SQLite for its local database.

### Developer notes

-   Use a virtual environment to isolate dependencies.
-   Do not commit or distribute a personal `pesaplan.db` file or its
    SQLite `-wal` and `-shm` files.
-   Keep database backups private and secure.
-   Never include personal financial records in bug reports.

### Windows packaging

The [Windows packaging
kit](https://github.com/Kanyi254/PesaPlan/tree/main/PesaPlan_Windows_Packaging_Kit)
contains the PyInstaller specification, Windows build script, and Inno
Setup script. See its README for build instructions. The Windows build
uses Python 3.12 (64-bit).

## Backing up your data

Before updating, reinstalling, moving to another computer, or
troubleshooting:

1.  Close PesaPlan.
2.  Locate the database file.
3.  Copy it to a safe backup location.
4.  Keep the backup private.

Windows:

``` text
%LOCALAPPDATA%\PesaPlan\pesaplan.db
```

Linux source:

``` text
pesaplan.db
```

## Troubleshooting

### PesaPlan does not open on Windows

-   Try launching it from the Start Menu.
-   Confirm the installer came from the official Releases page.
-   Restart Windows and try again.
-   Include your Windows version and exact error message when requesting
    help.

### `python` or `py` is not recognised

Python is required only when running from source. Install Python 3.12
and the Python Launcher. The beginner-friendly installer does not
require Python.

### Linux reports `Permission denied`

Run `chmod +x ./dist/PesaPlan/PesaPlan`, then try again.

### A Python module is missing

For source-code use, activate your virtual environment and install the
dependencies listed above.

### Report a problem

Include your operating system/version, how you installed PesaPlan, what
you were doing, and the complete error message. Never include passwords,
access tokens, or private financial information.

## Releases

Official builds are published at [PesaPlan
Releases](https://github.com/Kanyi254/PesaPlan/releases).

-   **Windows:** attach `PesaPlan_Setup.exe` to the Windows release.
-   **Linux:** publish a separate Linux-specific package when available.

Read the release notes before updating.

------------------------------------------------------------------------

**PesaPlan --- Personal finance, made easier.**
