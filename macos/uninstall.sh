#!/bin/bash
# Removes what setup_macos.sh installed, so the Claude Desktop extension is the only Viber MCP on
# this Mac:
#   - the `viber` server registered with Claude Code and in Claude Desktop's config
#   - ~/Applications/Viber MCP.app and its Accessibility / Screen Recording permissions
#   - the git checkout it ran from (moved to the Trash, and only when it has no uncommitted or
#     unpushed work; --keep-checkout leaves it)
# Before that, contacts.json, gc_members.json and google_oauth_client.json are copied from the
# checkout to ~/viber-mcp, where the extension finds them. ~/viber-mcp (Drive key, tokens,
# downloads) itself is kept.
#
#   Quit Claude Desktop (Cmd+Q), then:  bash uninstall.sh [--keep-checkout]
#
# Claude Code can't use Claude Desktop extensions: after this it has no Viber tools.
set -euo pipefail

KEEP_CHECKOUT=0
[ "${1:-}" = "--keep-checkout" ] && KEEP_CHECKOUT=1
APP="${VIBER_MCP_APP:-$HOME/Applications/Viber MCP.app}"
DATA="$HOME/viber-mcp"  # viber_config.DATA_DIR on macOS
CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
BUNDLE_ID="com.alphapremier.viber-mcp"

if pgrep -xq Claude; then
    echo "Quit Claude Desktop first (Cmd+Q): it rewrites its config while it runs." >&2
    exit 1
fi

# The checkout: the launcher's command is `uv --directory <checkout> run server.py`.
REPO=""
if [ -f "$APP/Contents/Resources/command" ]; then
    REPO="$(awk 'prev == "--directory" { print; exit } { prev = $0 }' "$APP/Contents/Resources/command")"
fi
if [ -z "$REPO" ] && [ -f "$(dirname "$0")/../server.py" ]; then
    REPO="$(cd "$(dirname "$0")/.." && pwd)"
fi
[ -n "$REPO" ] && [ ! -f "$REPO/server.py" ] && REPO=""

echo "== Your files -> $DATA"
KEEP_REASON=""
if [ -n "$REPO" ]; then
    mkdir -p "$DATA"
    for f in contacts.json gc_members.json google_oauth_client.json; do
        [ -f "$REPO/$f" ] || continue
        if [ ! -e "$DATA/$f" ]; then
            cp -p "$REPO/$f" "$DATA/$f"
            echo "   copied $f"
        elif cmp -s "$REPO/$f" "$DATA/$f"; then
            echo "   $f already there"
        else
            echo "   $DATA/$f exists and differs from the checkout's: left both" >&2
            KEEP_REASON="its $f differs from $DATA/$f"
        fi
    done
else
    echo "   no checkout found; nothing to copy"
fi

echo "== Claude Code"
CLAUDE="$(command -v claude || true)"
if [ -z "$CLAUDE" ]; then
    CLAUDE="$(ls -d "$HOME"/Library/Application\ Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude 2>/dev/null | tail -1 || true)"
fi
if [ -n "$CLAUDE" ]; then
    "$CLAUDE" mcp remove --scope user viber >/dev/null 2>&1 && echo "   removed the viber server" \
        || echo "   no viber server registered"
else
    echo "   claude CLI not found; if it's registered there: claude mcp remove --scope user viber"
fi

echo "== Claude Desktop config"
if [ -f "$CONFIG" ]; then
    /usr/bin/python3 - "$CONFIG" <<'PY'
import json, os, shutil, sys, tempfile, time
path = sys.argv[1]
data = json.load(open(path))
if "viber" not in data.get("mcpServers", {}):
    print("   no viber entry")
    sys.exit(0)
shutil.copy2(path, f"{path}.bak-viber-{time.strftime('%Y%m%d%H%M%S')}")
del data["mcpServers"]["viber"]
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
with os.fdopen(fd, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
os.chmod(tmp, 0o600)
os.replace(tmp, path)
print("   removed the viber entry (backup saved next to it)")
PY
else
    echo "   no config file"
fi

echo "== Launcher app and its permissions"
if [ -d "$APP" ]; then
    rm -rf "$APP"
    echo "   deleted $APP"
else
    echo "   no app at $APP"
fi
tccutil reset Accessibility "$BUNDLE_ID" >/dev/null 2>&1 || true
tccutil reset ScreenCapture "$BUNDLE_ID" >/dev/null 2>&1 || true
echo "   Accessibility and Screen Recording permissions for \"Viber MCP\" reset"

echo "== Checkout"
if [ -z "$REPO" ]; then
    echo "   none found"
elif [ "$KEEP_CHECKOUT" = 1 ]; then
    echo "   kept $REPO (--keep-checkout)"
elif [ "$(cd "$REPO" && pwd -P)" = "$(cd "$DATA" 2>/dev/null && pwd -P)" ]; then
    echo "   kept $REPO: it is also the extension's data folder"
elif [ -n "$KEEP_REASON" ]; then
    echo "   kept $REPO: $KEEP_REASON"
elif [ -d "$REPO/.git" ] && [ -n "$(git -C "$REPO" status --porcelain 2>/dev/null)" ]; then
    echo "   kept $REPO: it has uncommitted changes"
elif [ -d "$REPO/.git" ] && [ -n "$(git -C "$REPO" log --branches --not --remotes --oneline 2>/dev/null)" ]; then
    echo "   kept $REPO: it has commits that aren't pushed"
elif [ -d "$REPO/.git" ] && [ -n "$(git -C "$REPO" stash list 2>/dev/null)" ]; then
    echo "   kept $REPO: it has stashed changes"
else
    TRASHED="$HOME/.Trash/$(basename "$REPO")-$(date +%Y%m%d%H%M%S)"
    mv "$REPO" "$TRASHED"
    echo "   moved $REPO to the Trash"
fi

cat <<EOF

Done. Next:
  1. Open Claude Desktop and install the extension: double-click viber-mcp.mcpb
     (https://github.com/Deign86/viber-mcp/releases/latest/download/viber-mcp.mcpb).
  2. The first time it uses Viber, macOS asks again for Accessibility and Screen Recording, this
     time for the program Claude Desktop starts (uv). Allow both, then restart Claude.
  Claude Code no longer has the Viber tools (extensions are Claude Desktop only).
EOF
