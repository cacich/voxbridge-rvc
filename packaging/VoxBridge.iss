#define AppName "VoxBridge"
#ifndef AppVersion
#define AppVersion "0.2.0"
#endif
#define AppPublisher "VoxBridge contributors"
#define AppExe "VoxBridge.exe"

[Setup]
AppId={{F9983AB3-2C1C-4A6A-A3D9-891F927A39B0}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName=VoxBridge {#AppVersion} beta
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\VoxBridge
DefaultGroupName=VoxBridge
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=VoxBridge-{#AppVersion}-beta-win64-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}
DisableProgramGroupPage=yes
SetupLogging=yes
LicenseFile=..\LICENSE
SetupIconFile=..\src\voxbridge\resources\voxbridge.ico

[Files]
Source: "..\dist\VoxBridge\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\VoxBridge"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\VoxBridge"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "建立桌面捷徑"; GroupDescription: "其他選項:"

[Run]
Filename: "{app}\{#AppExe}"; Description: "啟動 VoxBridge"; Flags: nowait postinstall skipifsilent
