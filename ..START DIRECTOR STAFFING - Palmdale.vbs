Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = appDir
If shell.AppActivate("Director Staffing Dashboard") Then
    WScript.Sleep 100
    shell.SendKeys "% x"
    WScript.Quit 0
End If
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File " & Chr(34) & appDir & "\setup_director_staffing.ps1" & Chr(34) & " -DirectorSchool " & Chr(34) & "Palmdale" & Chr(34)
shell.Run command, 0, False
