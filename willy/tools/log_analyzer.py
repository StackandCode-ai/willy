"""
Interaction Log Analyzer for Willy.
Inspects logs/interactions.jsonl to discover what queries the user asked,
which actions succeeded or failed, and what capabilities should be upgraded.
"""

import os
import json
from typing import Dict, Any, List

LOG_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "logs", "interactions.jsonl"))


def analyze_interaction_logs(limit: int = 20) -> Dict[str, Any]:
    """
    Reads the user interaction logs and returns structured diagnostics:
    - Recent queries asked by the user
    - Failed or errored actions
    - Actions taken vs queries without tools
    """
    if not os.path.exists(LOG_FILE):
        return {"success": False, "error": f"Log file not found at {LOG_FILE}"}

    records: List[Dict[str, Any]] = []
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except Exception as e:
        return {"success": False, "error": f"Error reading log file: {str(e)}"}

    total_logs = len(records)
    recent_records = records[-limit:] if total_logs > limit else records

    summary_items = []
    failed_queries = []

    for r in recent_records:
        heard = r.get("heard", "")
        action = r.get("action")
        spoke = r.get("spoke", "")
        duration = r.get("duration_sec", 0)

        tool_name = action.get("tool_name") if isinstance(action, dict) else None
        tool_result = action.get("tool_result") if isinstance(action, dict) else None
        is_success = tool_result.get("success", True) if isinstance(tool_result, dict) else True

        if not is_success or (tool_result and "error" in str(tool_result).lower()):
            failed_queries.append({
                "heard": heard,
                "tool": tool_name,
                "result": tool_result,
            })

        summary_items.append({
            "heard": heard,
            "tool": tool_name,
            "spoke": spoke[:100],
            "success": is_success,
        })

    return {
        "success": True,
        "total_interactions_logged": total_logs,
        "recent_count": len(summary_items),
        "recent_queries": summary_items,
        "failed_or_struggled": failed_queries,
    }
