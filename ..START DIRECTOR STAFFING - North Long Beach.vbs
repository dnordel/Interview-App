Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = appDir
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File " & Chr(34) & appDir & "\setup_director_staffing.ps1" & Chr(34) & " -DirectorSchool " & Chr(34) & "North Long Beach" & Chr(34)
shell.Run command, 0, False
