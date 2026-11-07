#!/bin/bash
# One-shot macOS setup: dependencies, the "Viber MCP" launcher app, Claude registration.
#
#   ./setup_macos.sh
#
# Builds ~/Applications/Viber MCP.app (macos/launcher.c) and registers it as the `viber` MCP server
# with Claude Code and Claude Desktop. Starting the server through the app makes macOS ask for
# Accessibility and Screen Recording for "Viber MCP" only (started directly, the grant would go to
# `uv`, and with it to every script uv runs). Re-run after moving the repo; the app is only
# rebuilt when it's missing, the launcher or the checkout's path changed, or its signature is
# broken or of the old kind — otherwise nothing in it is touched. Its signature says "any build
# with this bundle id", so a rebuild keeps the permissions too.
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
APP="${VIBER_MCP_APP:-$HOME/Applications/Viber MCP.app}"
UV="$(command -v uv || true)"
[ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
if [ -z "$UV" ]; then
    echo "uv isn't installed: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
fi

echo "== Dependencies"
"$UV" --directory "$REPO" sync

echo "== Launcher app: $APP"
# Nothing may be written into the bundle after it's signed: that voids the signature, and macOS
# then no longer matches the app to its Accessibility grant, so Claude Desktop asks for it again
# on every start although "Viber MCP" is allowed in System Settings. So the bundle is only touched
# when it has to be rebuilt, and then signed last.
#
# The ad-hoc signature's designated requirement is the bundle id alone. macOS stores that
# requirement with each grant and checks the app against it, so a rebuild (new launcher, moved
# checkout) keeps Accessibility and Screen Recording. A default ad-hoc signature's requirement is
# its exact hash: every rebuild was a new app that had to be allowed again, with the old entry
# still shown as on. Trade-off: another program signed with this id would match the grant too.
BUNDLE_ID="com.alphapremier.viber-mcp"
REQ="designated => identifier \"$BUNDLE_ID\""
stable_signature() {  # not a pipe into grep -q: under pipefail codesign's SIGPIPE would fail it
    case "$(codesign -d -r- "$APP" 2>&1)" in *"$REQ"*) return 0 ;; esac
    return 1
}
EXE="$APP/Contents/MacOS/viber-mcp"
RES="$APP/Contents/Resources"
STAMP="$RES/launcher.sha256"
CMDFILE="$RES/command"
SUM="$(shasum -a 256 "$REPO/macos/launcher.c" | cut -d' ' -f1)"
CMD="$(printf '%s\n' "$UV" "--directory" "$REPO" "run" "server.py")"
WHY=""
if [ ! -x "$EXE" ]; then
    WHY="new"
elif [ "$(cat "$STAMP" 2>/dev/null)" != "$SUM" ]; then
    WHY="the launcher changed"
elif [ "$(cat "$CMDFILE" 2>/dev/null)" != "$CMD" ]; then
    WHY="the checkout or uv moved"
elif ! codesign --verify "$APP" >/dev/null 2>&1; then
    WHY="its signature was broken (an older setup_macos.sh wrote into it after signing)"
elif ! stable_signature; then
    WHY="it was signed the old way, which tied the permissions to that one build"
fi
if [ -n "$WHY" ]; then
    KEPT=0
    [ -x "$EXE" ] && stable_signature && KEPT=1  # the grants match any build with this id
    mkdir -p "$APP/Contents/MacOS" "$RES"
    rm -f "$APP/Icon"$'\r' && xattr -c "$APP"  # a Finder custom icon breaks codesign
    cp "$REPO/macos/AppIcon.icns" "$RES/AppIcon.icns"
    cc -O2 -o "$EXE" "$REPO/macos/launcher.c"
    sed "s/BUNDLE_ID/$BUNDLE_ID/" > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key><string>BUNDLE_ID</string>
    <key>CFBundleName</key><string>Viber MCP</string>
    <key>CFBundleDisplayName</key><string>Viber MCP</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>CFBundleExecutable</key><string>viber-mcp</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST
    printf '%s\n' "$CMD" > "$CMDFILE"
    echo "$SUM" > "$STAMP"
    codesign --force --sign - --requirements "=$REQ" "$APP"  # last: nothing may follow it
    echo "   built and signed ($WHY)"
    if [ "$KEPT" = 1 ]; then
        echo "   permissions kept (they're tied to the bundle id, not to this build)"
    else
        # The grants macOS has are for the old build: clear them so the new one is asked once.
        tccutil reset Accessibility "$BUNDLE_ID" >/dev/null 2>&1 || true
        tccutil reset ScreenCapture "$BUNDLE_ID" >/dev/null 2>&1 || true
        echo "   macOS asks for Accessibility and Screen Recording one last time: allow \"Viber MCP\""
        echo "   (later rebuilds keep the permissions)"
    fi
else
    echo "   up to date (permissions kept)"
fi

echo "== Claude Code"
CLAUDE="$(command -v claude || true)"
if [ -z "$CLAUDE" ]; then
    CLAUDE="$(ls -d "$HOME"/Library/Application\ Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude 2>/dev/null | tail -1 || true)"
fi
if [ -n "$CLAUDE" ]; then
    "$CLAUDE" mcp remove --scope user viber >/dev/null 2>&1 || true
    "$CLAUDE" mcp add --transport stdio --scope user viber -- "$EXE"
else
    echo "   claude CLI not found; register it yourself:"
    echo "   claude mcp add --transport stdio --scope user viber -- \"$EXE\""
fi

echo "== Claude Desktop"
CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
mkdir -p "$(dirname "$CONFIG")"
[ -f "$CONFIG" ] && cp "$CONFIG" "$CONFIG.bak-viber-$(date +%Y%m%d%H%M%S)"
/usr/bin/python3 - "$CONFIG" "$EXE" <<'PY'
import json, os, sys, tempfile
path, exe = sys.argv[1], sys.argv[2]
data = json.load(open(path)) if os.path.exists(path) else {}
data.setdefault("mcpServers", {})["viber"] = {"command": exe, "args": []}
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
with os.fdopen(fd, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
os.chmod(tmp, 0o600)
os.replace(tmp, path)
PY
echo "   registered in $CONFIG"

echo "== Antigravity"
for AGY_DIR in "$HOME/.gemini/config" "$HOME/.gemini/antigravity"; do
    if [ -d "$AGY_DIR" ] || command -v agy >/dev/null 2>&1 || [ "$AGY_DIR" = "$HOME/.gemini/config" -a -d "$HOME/.gemini" ]; then
        mkdir -p "$AGY_DIR"
        AGY_CONFIG="$AGY_DIR/mcp_config.json"
        [ -f "$AGY_CONFIG" ] && cp "$AGY_CONFIG" "$AGY_CONFIG.bak-viber-$(date +%Y%m%d%H%M%S)"
        /usr/bin/python3 - "$AGY_CONFIG" "$EXE" <<'PY'
import json, os, sys, tempfile
path, exe = sys.argv[1], sys.argv[2]
data = json.load(open(path)) if os.path.exists(path) else {}
data.setdefault("mcpServers", {})["viber"] = {"command": exe, "args": []}
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
with os.fdopen(fd, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
os.chmod(tmp, 0o600)
os.replace(tmp, path)
PY
        echo "   registered in $AGY_CONFIG"
    fi
done

cat <<EOF

Done. Next:
  1. Quit Claude / Antigravity completely (Cmd+Q) and reopen it. Claude Desktop rewrites its config
     while it runs, so do this right away or the entry above can be lost.
  2. The first time Claude or Antigravity uses Viber, macOS asks to allow "Viber MCP" for
     Accessibility and Screen Recording (System Settings → Privacy & Security). Allow both, then
     restart the app.
  3. Put the Drive key at ~/viber-mcp/google_service_account.json, and for uploads
     google_oauth_client.json in $REPO, then ask to run drive_sign_in(write=True).
EOF
