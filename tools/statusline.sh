#!/bin/bash
# Claude Code status line for Desk Buddy.
# Claude Code runs this after each reply with session JSON on stdin; it includes your plan's usage
# limits (rate_limits.five_hour / seven_day: used_percentage, resets_at). This forwards that JSON
# to the bridge (which turns it into Lump's usage-gauge colour) and prints a short status line.
# Installed copy: ~/.desk-buddy/statusline.sh (referenced from ~/.claude/settings.json).
input=$(cat)
printf '%s' "$input" | curl -s -m 1 -o /dev/null -H 'Content-Type: application/json' \
  --data-binary @- http://127.0.0.1:47823/usage >/dev/null 2>&1 &
printf '%s' "$input" | /usr/bin/jq -r '[
    (.model.display_name // empty),
    (.rate_limits.five_hour.used_percentage // empty | "5h \(floor)%"),
    (.rate_limits.seven_day.used_percentage // empty | "week \(floor)%")
  ] | join(" · ")' 2>/dev/null
