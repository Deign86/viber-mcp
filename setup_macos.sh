#!/bin/bash
# One-shot macOS setup: dependencies, the "Viber MCP" launcher app, Claude registration.
#
#   ./setup_macos.sh
#
# Builds ~/Applications/Viber MCP.app (macos/launcher.c) and registers it as the `viber` MCP server
# with Claude Code and Claude Desktop. Starting the server through the app makes macOS ask for
# Accessibility and Screen Recording for "Viber MCP" only (started directly, the grant would go to
# `uv`, and with it to every script uv runs). Re-run after moving the repo; the app is only
# rebuilt when it's missing or the launcher changed, so existing permissions are kept.
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
EXE="$APP/Contents/MacOS/viber-mcp"
STAMP="$APP/Contents/Resources/launcher.sha256"
SUM="$(shasum -a 256 "$REPO/macos/launcher.c" | cut -d' ' -f1)"
if [ ! -x "$EXE" ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$SUM" ]; then
    mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
    rm -f "$APP/Icon"$'\r' && xattr -c "$APP"  # a Finder custom icon (below) breaks codesign
    cp "$REPO/macos/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
    cc -O2 -o "$EXE" "$REPO/macos/launcher.c"
    cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key><string>com.alphapremier.viber-mcp</string>
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
    echo "$SUM" > "$STAMP"
    REBUILT=1
else
    REBUILT=0
fi
printf '%s\n' "$UV" "--directory" "$REPO" "run" "server.py" > "$APP/Contents/Resources/command"
if [ "$REBUILT" = 1 ]; then
    codesign --force --sign - "$APP"  # ad-hoc: macOS keys the permissions to this signature
    echo "   built and signed (a rebuild needs the permissions granted again)"
else
    echo "   up to date (permissions kept)"
    if [ ! -f "$APP/Contents/Resources/AppIcon.icns" ] && [ ! -f "$APP/Icon"$'\r' ]; then
        # Built before it had an icon: set it as a Finder custom icon, which leaves the signed
        # files (and with them the granted permissions) untouched.
        osascript -l JavaScript - "$REPO/macos/AppIcon.icns" "$APP" >/dev/null <<'JS'
ObjC.import("AppKit")
function run(argv) {
    const img = $.NSImage.alloc.initWithContentsOfFile(argv[0])
    return $.NSWorkspace.sharedWorkspace.setIconForFileOptions(img, argv[1], 0)
}
JS
        killall Dock 2>/dev/null || true  # Launchpad picks the icon up on restart
        echo "   icon set"
    fi
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

cat <<EOF

Done. Next:
  1. Quit Claude completely (Cmd+Q) and reopen it. Claude Desktop rewrites its config while it
     runs, so do this right away or the entry above can be lost.
  2. The first time Claude uses Viber, macOS asks to allow "Viber MCP" for Accessibility and
     Screen Recording (System Settings → Privacy & Security). Allow both, then restart Claude.
  3. Put the Drive key at ~/viber-mcp/google_service_account.json, and for uploads
     google_oauth_client.json in $REPO, then ask Claude to run drive_sign_in(write=True).
EOF
