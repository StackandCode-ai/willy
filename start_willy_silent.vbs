' Willy Voice Assistant - Silent Background Launcher
' Starts Willy without showing an empty command prompt window
Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

strScriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = strScriptDir

' Run Willy via pythonw.exe
strCmd = "pythonw.exe """ & strScriptDir & "\main.py"" --mode ui"
WshShell.Run strCmd, 0, False
