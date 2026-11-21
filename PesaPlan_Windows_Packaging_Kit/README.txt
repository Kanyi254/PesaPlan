PESAPLAN WINDOWS PACKAGING KIT

IMPORTANT
- Copy these files into the PesaPlan project root on your colleague's Windows 10 PC.
- Also copy the CURRENT, TESTED versions of these project files from Manjaro:
    pesaplan.py
    pesaplan_phone.py
    pesaplan_export.py
    pesaplan_sync.py
    pesaplan_syncui.py
    icon.png
- Do NOT copy pesaplan.db, pesaplan.db-wal, or pesaplan.db-shm.
- Do NOT use the older source copies in this kit as replacements for your current source.

WINDOWS BUILD
1. Install 64-bit Python 3.12 from https://www.python.org/downloads/windows/.
   Enable the Python Launcher during installation.
2. Install Inno Setup 6 from https://jrsoftware.org/isinfo.php.
3. Open the project folder in File Explorer.
4. Double-click build_windows.bat and wait for BUILD COMPLETE.
5. Test dist\PesaPlan\PesaPlan.exe before creating the installer.
6. Right-click PesaPlan.iss and choose Compile with Inno Setup, or open it in Inno Setup and click Compile.
7. The installer will be created in installer-output\PesaPlan_Setup.exe.

NOTES
- The build script creates a separate .venv-win environment. It does not use or modify the Manjaro Conda environment.
- The PyInstaller output is a folder-based application (onedir), packaged into one setup installer by Inno Setup.
- The application database should be created under %LOCALAPPDATA%\PesaPlan by the Windows build of pesaplan.py.
- The installer does not bundle or install a database and does not remove the database on uninstall.
- Test on Windows 10 with a standard non-administrator account if possible.
- Unsigned installers may show a Windows SmartScreen warning. Do not bypass warnings on machines you do not trust.
