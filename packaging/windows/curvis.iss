; Inno Setup script for the Curvis Windows installer.
; Build: iscc /DAppVersion=0.1.0b1 /DNumericVersion=0.1.0 packaging\windows\curvis.iss  (after PyInstaller)
; Output: dist\Curvis-<version>-setup.exe

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
; The file version info takes digits only (0.1.0b1 -> 0.1.0): pass /DNumericVersion=0.1.0.
#ifndef NumericVersion
  #define NumericVersion "0.0.0"
#endif

[Setup]
AppId={{6B3C2A0E-8E7D-4B6A-9C1F-3D5E7A9B2C41}
AppName=Curvis
AppVersion={#AppVersion}
VersionInfoVersion={#NumericVersion}
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
; Installing a newer version over an older one is an upgrade: same AppId, folder and
; install mode (UsePreviousPrivileges, UsePreviousAppDir are on by default). Settings
; live in the user's registry and projects are the user's own files: neither is touched.
; Curvis's updater runs this setup with /SILENT /CLOSEAPPLICATIONS /RELAUNCH=1. Restart
; Manager closes a Curvis that still runs; the [Run] entry below starts the new one.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; The old version's program files go first: a file the new version no longer ships
; (a library PyInstaller left out, a renamed module) must not be loaded by it.
Type: filesandordirs; Name: "{app}\_internal"

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
; After an update from inside Curvis (silent, with /RELAUNCH=1): start the new version.
Filename: "{app}\Curvis.exe"; Flags: nowait runasoriginaluser; Check: RelaunchAfterUpdate

[Code]
function RelaunchAfterUpdate: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:RELAUNCH|0}') = '1');
end;
