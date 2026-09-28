; Inno Setup script for the Claude Profiles installer.
;
; Build (after PyInstaller has produced dist\Claude Profiles):
;   iscc /DAppVersion=0.1.0 installer\ClaudeProfiles.iss
; Result:
;   release\ClaudeProfiles-Setup-0.1.0.exe
;
; A per-user install: no administrator prompt, nothing outside the user's own
; profile, and the same %LOCALAPPDATA%\Programs\Claude Profiles folder the app
; already treats as its installed home (see services/autostart.py).
;
; claude-swap is bundled in the cswap subfolder, so this installer is the only
; thing a user needs besides Claude Code itself.

#ifndef AppVersion
  #error Pass the version: iscc /DAppVersion=X.Y.Z installer\ClaudeProfiles.iss
#endif

#define AppName "Claude Profiles"
#define AppExe "ClaudeProfiles.exe"
#define AppUrl "https://github.com/kevinabouhanna/claude-profiles-windows"
#ifndef SourceDir
  #define SourceDir "..\dist\Claude Profiles"
#endif

[Setup]
; Never change AppId: it is how an upgrade finds the previous installation.
AppId={{74EA9998-C0F5-4A2A-AEE7-69EDC9C5B58A}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Kevin Abouhanna
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases
AppCopyright=Copyright (c) 2026 Kevin Abouhanna
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup

PrivilegesRequired=lowest
DefaultDirName={userpf}\{#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

LicenseFile=..\LICENSE
SetupIconFile=..\build\claude_profiles.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern

OutputDir=..\release
OutputBaseFilename=ClaudeProfiles-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes

; A running tray instance holds its files open. Close it before replacing
; them, and do not restart it behind the user's back afterwards - the
; finish page offers to launch the new version instead.
CloseApplications=force
RestartApplications=no
; Only relevant when the optional PATH task is ticked.
ChangesEnvironment=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "cswappath"; Description: "Add the bundled claude-swap (cswap) to my PATH, to use it from a terminal"; GroupDescription: "Optional:"; Flags: unchecked

[InstallDelete]
; PyInstaller's library folder changes between versions. Clearing it first
; stops an upgrade leaving stale DLLs beside the new ones.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\cswap\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; The same path the app itself maintains, so there is only ever one entry.
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Monitor and switch Claude Code accounts"

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#AppExe}"; Flags: runhidden; RunOnceId: "StopClaudeProfiles"

[UninstallDelete]
; Created by the app at runtime rather than by this installer.
Type: files; Name: "{userstartup}\{#AppName}.lnk"
Type: files; Name: "{userprograms}\{#AppName}.lnk"
Type: filesandordirs; Name: "{localappdata}\{#AppName}\cache"
Type: dirifempty; Name: "{localappdata}\{#AppName}"
; %LOCALAPPDATA%\ClaudeProfiles (settings and activity history) is left in
; place, as uninstallers conventionally do. The Privacy page can clear it.

[Registry]
; Appends to the user's own PATH only when the task is ticked; the [Code]
; section removes exactly this entry again on uninstall.
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}\cswap"; Tasks: cswappath; Check: NeedsPathEntry(ExpandConstant('{app}\cswap'))

[Code]
function PathHas(const Paths, Dir: string): Boolean;
begin
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Paths) + ';') > 0;
end;

function NeedsPathEntry(Dir: string): Boolean;
var
  Paths: string;
begin
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', Paths) then
    Paths := '';
  Result := not PathHas(Paths, Dir);
end;

procedure RemovePathEntry(Dir: string);
var
  Paths: string;
  P: Integer;
begin
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', Paths) then
    exit;
  Paths := ';' + Paths + ';';
  P := Pos(';' + Uppercase(Dir) + ';', Uppercase(Paths));
  if P = 0 then
    exit;
  Delete(Paths, P, Length(Dir) + 1);
  Paths := Copy(Paths, 2, Length(Paths) - 2);
  RegWriteExpandStringValue(HKCU, 'Environment', 'Path', Paths);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RemovePathEntry(ExpandConstant('{app}\cswap'));
  { taskkill returns before Windows has released the killed process, so the
    folder can still be locked when files are deleted. By this final step it
    is not; RemoveDir only succeeds on an empty folder, so nothing unexpected
    is lost. }
  if CurUninstallStep = usPostUninstall then
    RemoveDir(ExpandConstant('{app}'));
end;
