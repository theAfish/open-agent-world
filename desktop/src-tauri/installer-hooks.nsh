; Bundled Python packages can exceed MAX_PATH at the installed location.
; Windows must also have Win32 long paths enabled; do not change system policy.
ManifestLongPathAware true

!macro NSIS_HOOK_POSTINSTALL
    CopyFiles /SILENT "$INSTDIR\payload\oaw.cmd" "$INSTDIR"
    CopyFiles /SILENT "$INSTDIR\payload\oaw.ps1" "$INSTDIR"
    Push $0
    nsExec::ExecToLog '"$INSTDIR\payload\python\python.exe" -I -B "$INSTDIR\payload\scripts\install-cli.py"'
    Pop $0
    Pop $0
!macroend

!macro NSIS_HOOK_PREUNINSTALL
    Push $0
    nsExec::ExecToLog '"$INSTDIR\payload\python\python.exe" -I -B "$INSTDIR\payload\scripts\install-cli.py" --uninstall'
    Pop $0
    Pop $0
    Delete "$INSTDIR\oaw.cmd"
    Delete "$INSTDIR\oaw.ps1"
!macroend
