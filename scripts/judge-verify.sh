#!/bin/bash
# Judge verification script
# Usage: ./judge-verify.sh {PLANE_ISSUE_ID} {PR_BASELINE_PASS_COUNT}
# Runs after MR merge. Posts result. Does not fix anything.
#
# IMPORTANT: This script parses the JSON compliance output, NOT the text output.
# grep -c "PASS" on text double-counts lines with multiple matches.
# The JSON is authoritative.

set -euo pipefail

ISSUE_ID="${1:?Usage: judge-verify.sh ISSUE_ID BASELINE_PASS_COUNT}"
BASELINE_PASS="${2:?Usage: judge-verify.sh ISSUE_ID BASELINE_PASS_COUNT}"
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
JSON_PATH="${HOME}/sentinel-cache/config-cache/nist-compliance-latest.json"

echo "=== JUDGE RUN === $TIMESTAMP"
echo "Issue: $ISSUE_ID | Baseline pass count: $BASELINE_PASS"

# Check if we have a fresh compliance JSON (the cron runs on iac-control)
if [ ! -f "$JSON_PATH" ]; then
    echo "ERROR: No compliance JSON found at $JSON_PATH"
    echo "The compliance check runs on iac-control (192.168.12.210), not this machine."
    echo "Ensure sentinel-cache is synced or run the check on iac-control first."
    exit 2
fi

# Check freshness — warn if > 6 hours old
JSON_AGE=$(( $(date +%s) - $(stat -c %Y "$JSON_PATH") ))
if [ "$JSON_AGE" -gt 21600 ]; then
    echo "WARNING: Compliance JSON is $(( JSON_AGE / 3600 )) hours old. Consider re-running the check."
fi

# Parse JSON — the authoritative source
CURRENT_PASS=$(python3 -c "
import json
with open('$JSON_PATH') as f:
    d = json.load(f)
print(sum(1 for c in d['checks'] if c['status'] == 'PASS'))
")
CURRENT_FAIL=$(python3 -c "
import json
with open('$JSON_PATH') as f:
    d = json.load(f)
print(sum(1 for c in d['checks'] if c['status'] == 'FAIL'))
")
CURRENT_WARN=$(python3 -c "
import json
with open('$JSON_PATH') as f:
    d = json.load(f)
print(sum(1 for c in d['checks'] if c['status'] == 'WARN'))
")
TOTAL=$(python3 -c "
import json
with open('$JSON_PATH') as f:
    d = json.load(f)
print(len(d['checks']))
")
CHECK_TIMESTAMP=$(python3 -c "
import json
with open('$JSON_PATH') as f:
    d = json.load(f)
print(d.get('timestamp', 'UNKNOWN'))
")
OVERALL=$(python3 -c "
import json
with open('$JSON_PATH') as f:
    d = json.load(f)
print(d.get('overall_status', 'UNKNOWN'))
")

echo "Check timestamp: $CHECK_TIMESTAMP"
echo "Current state: $CURRENT_PASS pass, $CURRENT_FAIL fail, $CURRENT_WARN warn (of $TOTAL)"
echo "Overall: $OVERALL"
echo "Change from baseline: $((CURRENT_PASS - BASELINE_PASS)) pass delta"

# Get failure/warn details from JSON
ISSUES_DETAIL=$(python3 -c "
import json
with open('$JSON_PATH') as f:
    d = json.load(f)
for c in d['checks']:
    if c['status'] in ('FAIL', 'WARN'):
        ctrl = c.get('control_id', '?')
        chk = c.get('check_id', '?')
        detail = c.get('detail', '')[:100]
        print(f\"{c['status']}: {ctrl} — {chk} — {detail}\")
")

# Write result for Plane comment
RESULT_FILE="/tmp/judge-result-${ISSUE_ID}.md"
cat > "$RESULT_FILE" << EOF
## Judge Verification — ${TIMESTAMP}
**Issue:** ${ISSUE_ID}
**Check ran:** ${CHECK_TIMESTAMP}
**Pass:** ${CURRENT_PASS} of ${TOTAL} (was ${BASELINE_PASS}, delta: $((CURRENT_PASS - BASELINE_PASS)))
**Fail:** ${CURRENT_FAIL}
**Warn:** ${CURRENT_WARN}
**Overall:** ${OVERALL}

\`\`\`
${ISSUES_DETAIL}
\`\`\`

**Verdict:** $([ "$CURRENT_FAIL" -eq 0 ] && echo "PASS — no failures" || echo "FAIL — $CURRENT_FAIL checks failing")
$([ "$JSON_AGE" -gt 21600 ] && echo "**WARNING:** Compliance data is $(( JSON_AGE / 3600 )) hours old. Re-run check for authoritative result." || true)
EOF

cat "$RESULT_FILE"

# Exit code signals verdict
[ "$CURRENT_FAIL" -eq 0 ] && exit 0 || exit 1
