#define AppName "Shorekeep"
#ifndef AppVersion
  #define AppVersion "0.5.0"
#endif
#define AppPublisher "Shorekeep"
#define AppExeName "Shorekeep.exe"

[Setup]
AppId={{A6F1D7C2-4E39-4B8A-9C1D-2F5E7B3A8C14}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\Shorekeep
DefaultGroupName={#AppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
MinVersion=10.0
OutputDir=..\..\dist\installer
OutputBaseFilename=Shorekeep-Setup-{#AppVersion}
SetupIconFile=..\..\assets\brand\shorekeep.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "autostart"; Description: "Start Shorekeep when I sign in"; Flags: checkedonce
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\..\dist\Shorekeep\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Classes\shorekeep"; ValueType: string; ValueName: ""; ValueData: "URL:Shorekeep Protocol"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\shorekeep"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\shorekeep\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExeName}"" ""%1"""; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Shorekeep"; ValueData: """{app}\{#AppExeName}"" --minimized"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
