#define MyAppName "PesaPlan"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "PesaPlan"
#define MyAppExeName "PesaPlan.exe"

[Setup]
AppId={{D4A6D6C1-7F4B-4C1A-9A3D-6C8F2A51B710}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\PesaPlan
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer-output
OutputBaseFilename=PesaPlan_Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "dist\PesaPlan\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\PesaPlan"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\PesaPlan"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch PesaPlan"; Flags: nowait postinstall skipifsilent
