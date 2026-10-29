; Inno Setup script -> builds Tellbird-<version>-Setup.exe
; Version is passed by CI:  ISCC /DAppVersion=1.2.3 packaging\windows\installer.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{A2C5898E-D67A-4EAE-9B7C-B907EA19E812}
AppName=Tellbird
AppPublisherURL=https://tellbird.org
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Tellbird
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
SourceDir=..\..
OutputDir=Output
OutputBaseFilename=Tellbird-{#AppVersion}-Setup
Compression=lzma2
SolidCompression=yes

[Files]
Source: "dist\tellbird\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Tellbird"; Filename: "{app}\tellbird.exe"
Name: "{autodesktop}\Tellbird"; Filename: "{app}\tellbird.exe"

[Run]
Filename: "{app}\tellbird.exe"; Description: "Launch now"; Flags: nowait postinstall skipifsilent
