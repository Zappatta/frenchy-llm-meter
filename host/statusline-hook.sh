#!/bin/bash
#
# frenchy-llm-meter statusline shim.
#
# Claude Code hands its statusline command a JSON blob on stdin that already
# contains the authoritative plan usage:
#
#   .rate_limits.five_hour.used_percentage   0..100
#   .rate_limits.five_hour.resets_at         unix epoch seconds
#   .rate_limits.seven_day.used_percentage
#   .rate_limits.seven_day.resets_at
#
# That is the same figure /usage reports — no API call, no OAuth token, no
# calibration. This shim captures it for the daemon and hands the untouched
# payload to your real statusline, so the terminal looks exactly the same.
#
# Design constraints, from the statusline docs:
#
#   * The script runs on every render, debounced to 300ms. That is up to three
#     times a second during active work, so the capture is throttled rather
#     than written every time.
#   * "If a new update triggers while your script is still running, Claude Code
#     cancels the in-flight script." So the real statusline is printed FIRST
#     and the capture happens after. A cancellation can then only cost one
#     capture — which the next render redoes 300ms later — and can never cost
#     the statusline itself.
#
# Install by pointing statusLine.command here and setting
# FRENCHY_INNER_STATUSLINE to whatever it used to point at.

set -uo pipefail

STATE_DIR="${FRENCHY_STATE_DIR:-$HOME/.local/state/frenchy-llm-meter}"
STATE_FILE="$STATE_DIR/usage.json"
TMP_FILE="$STATE_DIR/.usage.partial"
INNER="${FRENCHY_INNER_STATUSLINE:-$HOME/.claude/statusline-command.sh}"

# Seconds between captures. The meter refreshes every few seconds and the plan
# percentage moves slowly, so there is nothing to gain from writing faster.
THROTTLE_S="${FRENCHY_CAPTURE_THROTTLE:-10}"

input=$(cat)

# --- 1. The statusline, first and unconditionally ---------------------------
# Nothing above this point can fail in a way that costs output, and anything
# below it is expendable.

self=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/$(basename "${BASH_SOURCE[0]}")

# INNER is a command line, not necessarily a path. Treating it as a bare path
# silently replaced anyone's statusline the moment it carried an argument, a
# leading ~, or was something like `npx ccusage statusline`: none of those pass
# -x or -f, so they fell through to the directory-name fallback and the real
# statusline just stopped rendering.
run_inner() {
    if [ -x "$INNER" ]; then
        printf '%s' "$input" | "$INNER"
    elif [ -f "$INNER" ]; then
        printf '%s' "$input" | bash "$INNER"
    else
        # Arguments, a tilde to expand, a command on PATH — hand it to a shell.
        printf '%s' "$input" | sh -c "$INNER"
    fi
}

if [ -z "$INNER" ]; then
    # Nothing to wrap: better a plain directory than an empty status bar.
    printf '%s\n' "$(basename "$(pwd)")"
elif [ "$INNER" = "$self" ] || case "$INNER" in *"$self"*) true ;; *) false ;; esac; then
    # Would recurse forever and hang the statusline. Say so rather than spin.
    printf 'frenchy-llm-meter: FRENCHY_INNER_STATUSLINE points at the shim itself\n'
else
    inner_out=$(run_inner 2>/dev/null)
    if [ -n "$inner_out" ]; then
        printf '%s\n' "$inner_out"
    else
        printf '%s\n' "$(basename "$(pwd)")"
    fi
fi

# --- 2. The capture, throttled and entirely best-effort ----------------------
{
    now=$(date +%s)
    command -v jq >/dev/null 2>&1 || exit 0

    old=$(jq -c . "$STATE_FILE" 2>/dev/null) || old=null
    [ -n "$old" ] || old=null

    sid=$(printf '%s' "$input" | jq -r '.session_id // empty' 2>/dev/null)

    # Throttle per session, not globally: every open session runs this shim, so
    # a global throttle would let one chatty session starve the others of an
    # update to their own context reading.
    if [ -n "$sid" ]; then
        last=$(printf '%s' "$old" | jq -r --arg s "$sid" '.sessions[$s].at // 0' 2>/dev/null) || last=0
    else
        last=$(stat -f %m "$STATE_FILE" 2>/dev/null || stat -c %Y "$STATE_FILE" 2>/dev/null || echo 0)
    fi
    [ -n "$last" ] || last=0

    if [ $((now - last)) -ge "$THROTTLE_S" ]; then
        # Two different merges happen here.
        #
        # rate_limits is account-level, and every session caches its own copy,
        # so plain last-writer-wins lets a session that has not re-rendered
        # since the window rolled over clobber a fresher reading. Merge per
        # window: a later resets_at is a newer window, and within one window
        # usage only ever climbs, so the max is the newest.
        #
        # context_window is per session, so it is stored under the session id
        # and each session only ever updates its own entry.
        usage=$(printf '%s' "$input" | jq -c \
            --argjson at "$now" \
            --argjson old "$old" \
            --arg sid "$sid" \
            'def newer(a; b):
               if b == null then a
               elif a == null then b
               elif (a.resets_at // 0) > (b.resets_at // 0) then a
               elif (a.resets_at // 0) < (b.resets_at // 0) then b
               elif (a.used_percentage // 0) >= (b.used_percentage // 0) then a
               else b end;
             select(.rate_limits != null or .context_window != null)
             | . as $in
             | ($old.rate_limits) as $o
             | {captured_at: $at,
                rate_limits: (
                  if $in.rate_limits then {
                    five_hour: newer($in.rate_limits.five_hour; $o.five_hour),
                    seven_day: newer($in.rate_limits.seven_day; $o.seven_day)
                  } else $old.rate_limits end),
                sessions: (
                  # Drop sessions not heard from in 30 minutes so the file does
                  # not accumulate every session ever opened.
                  (($old.sessions // {})
                     | with_entries(select((.value.at // 0) > ($at - 1800))))
                  + (if ($sid != "" and $in.context_window.remaining_percentage != null)
                     then {($sid): {ctx: $in.context_window.remaining_percentage,
                                    at: $at}}
                     else {} end)
                )}' 2>/dev/null)

        # jq's select() emits nothing when the key is absent, which is what we
        # want: plans without rate limits must not overwrite a good reading
        # with a null one.
        if [ -n "$usage" ]; then
            mkdir -p "$STATE_DIR"
            # A fixed temp name, not mktemp: if this script is cancelled
            # mid-write the leftover is a single file that the next run
            # overwrites, rather than an unbounded pile of mktemp droppings.
            if printf '%s\n' "$usage" > "$TMP_FILE"; then
                mv -f "$TMP_FILE" "$STATE_FILE"
            fi
        fi
    fi
} >/dev/null 2>&1

exit 0
