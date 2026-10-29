; Inno Setup script for the Curvis Windows installer.
; Build: iscc /DAppVersion=0.1.0 packaging\windows\curvis.iss  (after PyInstaller)
; Output: dist\Curvis-<version>-setup.exe

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6B3C2A0E-8E7D-4B6A-9C1F-3D5E7A9B2C41}
AppName=Curvis
AppVersion={#AppVersion}
AppPublisher=Curvis developers
AppPublisherURL=https://github.com/curvis-labs/curvis
DefaultDirName={autopf}\Curvis
DefaultGroupName=Curvis
DisableProgramGroupPage=yes
; Install for the current user without admin rights, or for everyone.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ChangesAssociations=yes
OutputDir=..\..\dist
OutputBaseFilename=Curvis-{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\..\build\icons\curvis.ico
UninstallDisplayIcon={app}\Curvis.exe
LicenseFile=..\..\LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\..\dist\Curvis\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Curvis"; Filename: "{app}\Curvis.exe"
Name: "{autodesktop}\Curvis"; Filename: "{app}\Curvis.exe"; Tasks: desktopicon

[Registry]
; .cvp files open in Curvis. HKA is HKLM for all-users installs and HKCU otherwise.
Root: HKA; Subkey: "Software\Classes\.cvp"; ValueType: string; ValueName: ""; ValueData: "Curvis.Project"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.cvp\OpenWithProgids"; ValueType: string; ValueName: "Curvis.Project"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Curvis.Project"; ValueType: string; ValueName: ""; ValueData: "Curvis project"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Curvis.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\Curvis.exe,0"
Root: HKA; Subkey: "Software\Classes\Curvis.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\Curvis.exe"" ""%1"""
Root: HKA; Subkey: "Software\Classes\Applications\Curvis.exe\SupportedTypes"; ValueType: string; ValueName: ".cvp"; ValueData: ""; Flags: uninsdeletekey

[Run]
Filename: "{app}\Curvis.exe"; Description: "{cm:LaunchProgram,Curvis}"; Flags: nowait postinstall skipifsilent
