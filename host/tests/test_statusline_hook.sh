#!/bin/bash
#
# Tests for statusline-hook.sh. The shim is bash + jq, so pytest does not reach
# it — and its merge rule was written in response to a real failure seen on
# hardware, which makes it exactly the sort of thing that needs pinning.
#
#   ./tests/test_statusline_hook.sh
#
set -uo pipefail

HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/statusline-hook.sh"
PASS=0
FAIL=0

setup() {
    STATE=$(mktemp -d)
    export CLAWD_STATE_DIR="$STATE"
    export CLAWD_CAPTURE_THROTTLE=0
    export CLAWD_INNER_STATUSLINE=/dev/null
}

teardown() { rm -rf "$STATE"; }

payload() { # five_pct five_resets seven_pct
    printf '{"workspace":{"current_dir":"/tmp"},"rate_limits":{"five_hour":{"used_percentage":%s,"resets_at":%s},"seven_day":{"used_percentage":%s,"resets_at":9}}}' "$1" "$2" "$3"
}

send() { printf '%s' "$1" | "$HOOK" >/dev/null 2>&1; }

state() { jq -c '[.rate_limits.five_hour.used_percentage,.rate_limits.five_hour.resets_at,.rate_limits.seven_day.used_percentage]' "$STATE/usage.json" 2>/dev/null; }

check() { # description expected actual
    if [ "$2" = "$3" ]; then
        PASS=$((PASS + 1))
        printf '  ok   %s\n' "$1"
    else
        FAIL=$((FAIL + 1))
        printf '  FAIL %s\n       expected %s\n       got      %s\n' "$1" "$2" "$3"
    fi
}

# --- the merge rule ---------------------------------------------------------
# Every Claude Code session caches its own rate_limits and they all write to
# the same file. Last-writer-wins let a session that had not re-rendered since
# the 5-hour window rolled over clobber a fresher reading, which showed up on
# hardware as the display flapping between 45% and 0%.

echo "merge rule:"
setup
send "$(payload 45 100 8)"
check "first reading is stored" '[45,100,8]' "$(state)"

send "$(payload 30 100 8)"
check "stale replay of same window does not lower the figure" '[45,100,8]' "$(state)"

send "$(payload 0 200 14)"
check "window rollover is taken even though the percentage drops" '[0,200,14]' "$(state)"

send "$(payload 45 100 8)"
check "a session still on the old window cannot drag it back" '[0,200,14]' "$(state)"

send "$(payload 12 200 14)"
check "usage climbing within the current window is taken" '[12,200,14]' "$(state)"
teardown

# --- robustness -------------------------------------------------------------
echo "robustness:"
setup
send "$(payload 20 200 14)"
echo 'not json at all' > "$STATE/usage.json"
send "$(payload 25 200 14)"
check "a corrupt state file is replaced rather than wedging the shim" '[25,200,14]' "$(state)"
teardown

setup
send "$(payload 33 100 5)"
send '{"workspace":{"current_dir":"/tmp"}}'
check "a payload with no rate_limits does not clobber a good reading" '[33,100,5]' "$(state)"
teardown

setup
export CLAWD_CAPTURE_THROTTLE=600
send "$(payload 10 100 1)"
send "$(payload 99 100 1)"
check "the throttle suppresses a second write inside the window" '[10,100,1]' "$(state)"
teardown

# --- the statusline itself must always survive ------------------------------
echo "statusline output:"
setup
export CLAWD_INNER_STATUSLINE=/definitely/not/here
out=$(printf '%s' "$(payload 1 1 1)" | "$HOOK" 2>/dev/null)
check "a missing inner script still prints something" "true" "$([ -n "$out" ] && echo true || echo false)"

export CLAWD_INNER_STATUSLINE="$HOOK"
out=$(printf '%s' "$(payload 1 1 1)" | timeout 5 "$HOOK" 2>/dev/null; echo "rc=$?")
check "self-reference is refused rather than hanging" "true" "$(echo "$out" | grep -q 'rc=0' && echo true || echo false)"

# An inner statusline is a command line, not a path. Treating it as a path
# meant anything with an argument, or a leading tilde, silently stopped
# rendering the moment someone installed the shim — replaced by a bare
# directory name, with the capture still working so it looked fine.
inner="$STATE/inner.sh"
printf '#!/bin/bash\nprintf "INNER:%%s\\n" "$1"\n' > "$inner"
chmod +x "$inner"

export CLAWD_INNER_STATUSLINE="$inner --theme dark"
out=$(printf '%s' "$(payload 1 1 1)" | "$HOOK" 2>/dev/null)
check "an inner statusline with arguments still runs" "INNER:--theme" "$out"

export CLAWD_INNER_STATUSLINE="cat >/dev/null; printf 'ONPATH\\n'"
out=$(printf '%s' "$(payload 1 1 1)" | "$HOOK" 2>/dev/null)
check "an inner statusline that is a command, not a file, still runs" "ONPATH" "$out"

export CLAWD_INNER_STATUSLINE="$inner"
out=$(printf '%s' "$(payload 1 1 1)" | "$HOOK" 2>/dev/null)
check "a bare executable path still runs" "INNER:" "$out"
teardown

echo
if [ "$FAIL" -eq 0 ]; then
    echo "$PASS passed"
else
    echo "$PASS passed, $FAIL FAILED"
    exit 1
fi
