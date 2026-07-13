Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = appDir
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File " & Chr(34) & appDir & "\setup_and_run.ps1" & Chr(34) & " -UiMode pyside"
shell.Run command, 0, False
