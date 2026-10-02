#!/bin/zsh
# VB2 probe for PRD-session-independent-services.md (decision D1).
#
# Question: can a LaunchDaemon running as barry-agent, with NO user logged in,
# unlock a dedicated keychain and read an item from it? If yes, D1 = option A
# (dedicated runtime keychain). If no, D1 = option B (secrets file).
#
# Throwaway only: a dummy keychain with one dummy item. No real secret is used.
#
# Steps (each subcommand says who runs it):
#   1. barry-agent:          zsh vb2_keychain_daemon_probe.sh setup-keychain
#   2. barry-admin (sudo):   zsh vb2_keychain_daemon_probe.sh install-daemon
#   3. Wait 2 minutes, then  zsh vb2_keychain_daemon_probe.sh check   (expect OK lines)
#   4. Log out BOTH accounts. Wait 5 minutes. Log back in.
#   5. Reboot. Unlock the disk if FileVault asks. Do NOT log in for 5 minutes.
#   6. Log in, then          zsh vb2_keychain_daemon_probe.sh check
#      PASS = OK lines with timestamps from inside steps 4 and 5 (nobody logged in).
#   7. barry-admin (sudo):   zsh vb2_keychain_daemon_probe.sh teardown
#      then barry-agent:     zsh vb2_keychain_daemon_probe.sh teardown-keychain

set -euo pipefail

AGENT_HOME=/Users/barry-agent
KC="$AGENT_HOME/Library/Keychains/vb2-probe.keychain-db"
PWFILE="$AGENT_HOME/.vb2-probe-keychain-pw"
PROBE="$AGENT_HOME/vb2-probe.sh"
LOG=/Users/Shared/afc-richmond/vb2-probe.log
LABEL=com.aiadaptive.cos.vb2-probe
PLIST=/Library/LaunchDaemons/$LABEL.plist

case "${1:-}" in
setup-keychain)
  [[ "$(id -un)" == barry-agent ]] || { echo "run as barry-agent"; exit 1; }
  pw=$(openssl rand -hex 24)
  umask 077
  print -r -- "$pw" > "$PWFILE"
  chmod 400 "$PWFILE"
  security create-keychain -p "$pw" "$KC"
  security set-keychain-settings "$KC"            # no auto-lock, no lock on sleep
  security add-generic-password -s vb2-probe -a probe -w "dummy-value-ok" \
    -T /usr/bin/security "$KC"
  cat > "$PROBE" <<EOF
#!/bin/zsh
ts=\$(date '+%Y-%m-%d %H:%M:%S')
who=\$(who | wc -l | tr -d ' ')
if security unlock-keychain -p "\$(cat $PWFILE)" "$KC" 2>/dev/null \\
   && v=\$(security find-generic-password -s vb2-probe -w "$KC" 2>/dev/null) \\
   && [[ "\$v" == dummy-value-ok ]]; then
  echo "\$ts OK   user=\$(id -un) sessions_logged_in=\$who" >> $LOG
else
  echo "\$ts FAIL user=\$(id -un) sessions_logged_in=\$who" >> $LOG
fi
EOF
  chmod 700 "$PROBE"
  echo "keychain + probe ready. Next: barry-admin runs install-daemon."
  ;;
install-daemon)
  [[ -x "$PROBE" ]] || { echo "run setup-keychain as barry-agent first"; exit 1; }
  touch "$LOG"; chmod 666 "$LOG"
  sudo tee "$PLIST" >/dev/null <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>UserName</key><string>barry-agent</string>
  <key>GroupName</key><string>staff</string>
  <key>EnvironmentVariables</key>
  <dict><key>HOME</key><string>$AGENT_HOME</string></dict>
  <key>ProgramArguments</key>
  <array><string>/bin/zsh</string><string>$PROBE</string></array>
  <key>StartInterval</key><integer>60</integer>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
EOF
  sudo chown root:wheel "$PLIST"
  sudo chmod 644 "$PLIST"
  plutil -lint "$PLIST"
  sudo launchctl bootstrap system "$PLIST"
  echo "loaded. It writes one line a minute to $LOG."
  ;;
check)
  tail -n 40 "$LOG"
  echo "---"
  echo "OK lines: $(grep -c ' OK ' "$LOG" || true)   FAIL lines: $(grep -c ' FAIL ' "$LOG" || true)"
  echo "OK lines with nobody logged in: $(grep -c ' OK .*sessions_logged_in=0' "$LOG" || true)"
  ;;
teardown)
  sudo launchctl bootout system/$LABEL 2>/dev/null || true
  sudo rm -f "$PLIST"
  echo "daemon removed. Next: barry-agent runs teardown-keychain."
  ;;
teardown-keychain)
  [[ "$(id -un)" == barry-agent ]] || { echo "run as barry-agent"; exit 1; }
  security delete-keychain "$KC" 2>/dev/null || true
  rm -f "$PWFILE" "$PROBE"
  echo "probe keychain removed."
  ;;
*)
  sed -n '2,22p' "$0"
  exit 2
  ;;
esac
