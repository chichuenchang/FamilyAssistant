---
description: Launch WeChat bot in new visible terminal window
allowed-tools: PowerShell
---

Run this PowerShell command once, no other steps. Do not wait on bot, do not tail output.

```powershell
Start-Process powershell -WorkingDirectory "C:\Users\slimj\PROJECTS\FamilyAssistant" -ArgumentList '-NoExit','-Command','$Host.UI.RawUI.WindowTitle = ''WeChat Bot''; & "C:\Users\slimj\anaconda3\envs\familyassis310\python.exe" .codewhale/skills/Agent_Runtime/wechat_ilink.py --mode run $ARGUMENTS'
```

Extra flags from `$ARGUMENTS` (e.g. `--relogin`, `--no-debug`) pass through to `wechat_ilink.py`. Reply one line: launched, or exact error.
