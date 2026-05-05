#!/usr/bin/env bash
# =============================================================================
# echook - Quick Unsetup (Lite Tier Removal)
# Removes the Lite mode hooks from ~/.claude/settings.json
#
# Usage:
#   curl -sL https://raw.githubusercontent.com/ChanMeng666/echook/master/scripts/quick-unsetup.sh | bash
#   # or
#   bash scripts/quick-unsetup.sh
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()    { printf "${BLUE}[INFO]${NC} %s\n" "$1"; }
success() { printf "${GREEN}[OK]${NC} %s\n" "$1"; }
warn()    { printf "${YELLOW}[WARN]${NC} %s\n" "$1"; }
error()   { printf "${RED}[ERROR]${NC} %s\n" "$1"; }

main() {
    printf "\n${BOLD}${CYAN}echook - Quick Unsetup${NC}\n"
    printf "%s\n\n" "========================================"

    local settings_file="$HOME/.claude/settings.json"
    local hooks_mode_file="$HOME/.claude/.hooks_mode"

    if [ ! -f "$settings_file" ]; then
        warn "No settings.json found at $settings_file"
        warn "Nothing to remove."
        exit 0
    fi

    # Backup before modifying
    local backup_file="${settings_file}.backup.$(date +%Y%m%d_%H%M%S)"
    cp "$settings_file" "$backup_file"
    info "Backed up settings to $(basename "$backup_file")"

    # Remove hooks using python/node
    local python_cmd=""
    if command -v python3 &>/dev/null; then
        python_cmd="python3"
    elif command -v python &>/dev/null; then
        python_cmd="python"
    fi

    if [ -n "$python_cmd" ]; then
        "$python_cmd" -c "
import json, sys

settings_file = sys.argv[1]
try:
    with open(settings_file, 'r', encoding='utf-8') as f:
        settings = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    print('Could not parse settings.json')
    sys.exit(1)

hooks = settings.get('hooks', {})
removed = []
for key in ['Stop', 'Notification', 'SubagentStop', 'PermissionRequest']:
    if key in hooks:
        del hooks[key]
        removed.append(key)

if not hooks:
    del settings['hooks']

with open(settings_file, 'w', encoding='utf-8') as f:
    json.dump(settings, f, indent=2)
    f.write('\n')

for r in removed:
    print(f'Removed: {r}')
" "$settings_file"
    elif command -v node &>/dev/null; then
        node -e "
const fs = require('fs');
const f = process.argv[1];
let s = {};
try { s = JSON.parse(fs.readFileSync(f, 'utf8')); } catch(e) { process.exit(1); }
const hooks = s.hooks || {};
['Stop','Notification','SubagentStop','PermissionRequest'].forEach(k => {
    if (hooks[k]) { delete hooks[k]; console.log('Removed: ' + k); }
});
if (Object.keys(hooks).length === 0) delete s.hooks;
fs.writeFileSync(f, JSON.stringify(s, null, 2) + '\n');
" "$settings_file"
    else
        error "No Python or Node found. Cannot safely edit JSON."
        error "Please manually remove Stop, Notification, SubagentStop, and PermissionRequest hooks from:"
        error "  $settings_file"
        exit 1
    fi

    # Clean up mode marker
    if [ -f "$hooks_mode_file" ]; then
        rm -f "$hooks_mode_file"
    fi

    printf "\n"
    success "Quick Setup hooks removed from settings.json"
    printf "\n"
    printf "${BOLD}Next steps:${NC}\n"
    printf "  1. Restart Claude Code for changes to take effect\n"
    printf "  2. Your backup is at: %s\n" "$backup_file"
    printf "\n"
}

main "$@"
