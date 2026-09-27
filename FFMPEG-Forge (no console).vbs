' Double-click this once setup has run at least once.
' It launches FFMPEG-Forge with no console window at all.
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = here & "\.venv\Scripts\pythonw.exe"
If fso.FileExists(pyw) Then
    shell.Run """" & pyw & """ """ & here & "\ffmpeg_forge.py""", 0, False
Else
    shell.Run """" & here & "\FFMPEG-Forge.bat""", 1, False
End If
