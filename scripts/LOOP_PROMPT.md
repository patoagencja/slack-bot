# Claude Code Loop — Render Error Monitor

Run this from the slack-bot directory on your local machine:

```bash
claude --loop "$(cat scripts/LOOP_PROMPT.md)"
```

Or manually with /loop skill inside Claude Code session:

---

## Prompt for Claude to execute each loop iteration:

1. Run: `python3 scripts/check_render_logs.py --minutes 6`
2. If exit code is 0 (no errors) → done, wait for next iteration
3. If exit code is 1 (errors found):
   - Read the error output carefully
   - Identify which file/function is causing the error (search the codebase)
   - Fix the root cause
   - Run: `git add -A && git commit -m "fix: <short description of error fixed>"`
   - Run: `git push -u origin claude/push-changes-H9Q0z`
   - Print a summary of what was fixed
