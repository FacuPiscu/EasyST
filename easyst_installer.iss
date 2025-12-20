; ===================================================================
; Script de Inno Setup para EasySt v1.0 - MÉTODO ALTERNATIVO SIN PREPROCESADOR
; ===================================================================

[Setup]
AppName=EasySt
AppVersion=1.0
AppPublisher=Tu Nombre o Empresa
DefaultDirName={autopf}\EasySt
DefaultGroupName=EasySt
DisableProgramGroupPage=yes
OutputDir=.\installer_output
OutputBaseFilename=Setup_EasySt_v1.0
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile=C:\Users\FacuPiscu\OneDrive\Documentos\Easyst\mesa-de-trabajo.ico
UninstallDisplayIcon={app}\EasySt.exe

; --- ¡NUEVO MÉTODO PARA LA CONTRASEÑA! ---
; Esta es la contraseña que el instalador pedirá al inicio. No depende del preprocesador.
Password=FacuPiscu15

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Copia todos los archivos de la carpeta 'dist\EasySt' que generó PyInstaller
Source: "C:\Users\FacuPiscu\OneDrive\Documentos\Easyst\dist\EasySt\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\EasySt"; Filename: "{app}\EasySt.exe"
Name: "{group}\{cm:UninstallProgram,EasySt}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\EasySt"; Filename: "{app}\EasySt.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\EasySt.exe"; Description: "{cm:LaunchProgram,EasySt}"; Flags: nowait postinstall skipifsilent

; ===================================================================
; SECCIÓN DE CÓDIGO SIMPLIFICADA - Solo para crear el archivo de licencia
; ===================================================================
[Code]
const
  LicenseKeyContent = 'Carp0912'; // Debe ser EXACTAMENTE la misma que en easyst.py

procedure CurStepChanged(CurStep: TSetupStep);
var
  LicenseFilePath: string;
  LicenseFile: TStringList;
begin
  // Esta función se ejecuta después de que los archivos se han copiado.
  // No contiene ninguna referencia a TPasswordEditPage, por lo que no debería fallar.
  if CurStep = ssPostInstall then
  begin
    // Crea el archivo de licencia después de instalar
    LicenseFilePath := ExpandConstant('{app}\license.key');
    LicenseFile := TStringList.Create;
    try
      LicenseFile.Add(LicenseKeyContent);
      LicenseFile.SaveToFile(LicenseFilePath);
    finally
      LicenseFile.Free;
    end;
  end;
end;
