Set fso = CreateObject("Scripting.FileSystemObject")
panelDir = fso.GetParentFolderName(WScript.ScriptFullName)
root = fso.GetParentFolderName(panelDir)
py = root & "\AstrBot\.venv\Scripts\pythonw.exe"
app = panelDir & "\app.py"
Set sh = CreateObject("WScript.Shell")
sh.Run """" & py & """ """ & app & """", 0, False
