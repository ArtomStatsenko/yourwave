-- Personal Radio: starts the radio page (radio ui) in the background if it is not running, then opens it.
-- Apps start without the Terminal PATH, so Homebrew (python3, liquidsoap, ffmpeg) and uv are added explicitly.
set radioURL to "http://127.0.0.1:8765/"
set logFile to "$HOME/Library/Application Support/PersonalRadioClaude/logs/ui.log"
do shell script "export PATH=/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH; " & ¬
	"if ! curl -s -o /dev/null " & radioURL & "; then " & ¬
	"BROWSER=true nohup \"$HOME/PersonalRadio/bin/radio\" ui > \"" & logFile & "\" 2>&1 & " & ¬
	"for i in $(seq 1 50); do curl -s -o /dev/null " & radioURL & " && break; sleep 0.2; done; fi"
-- An open radio tab in Chrome is reloaded and shown (a restarted radio has a new page token) instead of a new tab.
if application id "com.google.Chrome" is running then
	tell application id "com.google.Chrome"
		repeat with w in windows
			set i to 0
			repeat with t in tabs of w
				set i to i + 1
				if URL of t starts with radioURL then
					tell t to reload
					set active tab index of w to i
					set index of w to 1
					activate
					return
				end if
			end repeat
		end repeat
	end tell
end if
open location radioURL
