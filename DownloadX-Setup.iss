
#define AppName "DownloadX"
#define AppVersion "0.2.0"

[Setup]
AppId={{A19C44D7-54F5-4A0E-8BC1-D0A0D10A0001}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\DownloadX
DefaultGroupName=DownloadX
OutputDir=build_output
OutputBaseFilename=DownloadX-Setup
UninstallDisplayIcon={app}\DownloadX\DownloadX.exe
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Dirs]
Name: "{app}\data"
Name: "{app}\native_host"

[Files]
; Main application
Source: "dist\DownloadX\*"; DestDir: "{app}\DownloadX"; Flags: recursesubdirs ignoreversion

; FFmpeg and FFprobe
Source: "ffmpeg\bin\ffmpeg.exe"; DestDir: "{app}\DownloadX\ffmpeg\bin"; Flags: ignoreversion
Source: "ffmpeg\bin\ffprobe.exe"; DestDir: "{app}\DownloadX\ffmpeg\bin"; Flags: ignoreversion

; Native messaging host
Source: "dist\downloadx_host.exe"; DestDir: "{app}\native_host"; Flags: ignoreversion
Source: "native_host\downloadx_host.bat"; DestDir: "{app}\native_host"; Flags: ignoreversion

; Chrome extension
Source: "extension\*"; DestDir: "{app}\extension"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\DownloadX"; Filename: "{app}\DownloadX\DownloadX.exe"; WorkingDir: "{app}\DownloadX"
Name: "{autodesktop}\DownloadX"; Filename: "{app}\DownloadX\DownloadX.exe"; WorkingDir: "{app}\DownloadX"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Google\Chrome\NativeMessagingHosts\com.downloadx.host"; ValueType: string; ValueName: ""; ValueData: "{app}\native_host\com.downloadx.host.json"; Flags: uninsdeletekey

[UninstallDelete]
Type: files; Name: "{app}\native_host\com.downloadx.host.json"

[Code]
function JsonEscape(const S: String): String;
begin
  Result := S;
  StringChangeEx(Result, '\', '\\', True);
  StringChangeEx(Result, '"', '\"', True);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ManifestPath: String;
  LauncherPath: String;
  JsonText: String;
begin
  if CurStep = ssPostInstall then
  begin
    ManifestPath := ExpandConstant(
      '{app}\native_host\com.downloadx.host.json'
    );

    LauncherPath := ExpandConstant(
      '{app}\native_host\downloadx_host.bat'
    );

    JsonText :=
      '{' + #13#10 +
      '  "name": "com.downloadx.host",' + #13#10 +
      '  "description": "DownloadX native helper",' + #13#10 +
      '  "path": "' + JsonEscape(LauncherPath) + '",' + #13#10 +
      '  "type": "stdio",' + #13#10 +
      '  "allowed_origins": ["chrome-extension://nobiidalhdhibgabpdlodcfcefojejnc/"]' + #13#10 +
      '}' + #13#10;

    if not SaveStringToFile(ManifestPath, JsonText, False) then
      RaiseException(
        'Could not create the Chrome native host manifest.'
      );
  end;
end;

[Run]
Filename: "{app}\DownloadX\DownloadX.exe"; Description: "Launch DownloadX"; Flags: postinstall nowait skipifsilent
