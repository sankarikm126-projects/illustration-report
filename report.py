"""
Weekly Illustration WIP Report — 3rd Party + Briefed Items
Posts a summary to Slack every Wednesday via GitHub Actions.

Requires environment variables (set as GitHub Actions secrets):
  MONDAY_API_TOKEN  — Monday.com personal API token
  SLACK_BOT_TOKEN   — Slack Bot token
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ── Configuration ──────────────────────────────────────────────────────────────

MONDAY_API_TOKEN = os.environ["MONDAY_API_TOKEN"]
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "C0BBMP6QBEY")

MONDAY_API_URL = "https://api.monday.com/v2"
SLACK_API_URL = "https://slack.com/api/chat.postMessage"

# All Illustration WIP boards (id, name)
BOARDS = [
    (2010935090, "Biology"),
    (2011611170, "Physics"),
    (2011632126, "History"),
    (2010969375, "Geography"),
    (2010921511, "Economics"),
    (2010957201, "Business"),
    (2011633027, "Computer Science"),
    (2011635945, "Environmental Studies"),
    (2016295873, "Maths"),
    (2055954023, "Psychology/Sociology"),
    (2065372157, "Religious Studies"),
    (2097372783, "English"),
    (5059917247, "MFL"),
    (5091720976, "IB Core"),
    (5088256418, "Politics & Citizenship"),
]

# Labels that count as "3rd party" across boards (case-insensitive)
THIRD_PARTY_LABELS = {"3rd party", "stock image"}


# ── Helpers ────────────────────────────────────────────────────────────────────

def monday_query(query, variables=None):
    """Execute a Monday.com GraphQL query."""
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        MONDAY_API_URL,
        data=payload,
        headers={
            "Authorization": MONDAY_API_TOKEN,
            "Content-Type": "application/json",
            "API-Version": "2024-10",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def get_board_columns(board_id):
    """Return (type_col_id, type_labels, status_col_id, briefed_index) or Nones."""
    q = """query ($id: [ID!]!) {
        boards(ids: $id) {
            columns { id title type settings_str }
        }
    }"""
    data = monday_query(q, {"id": [str(board_id)]})
    cols = data["data"]["boards"][0]["columns"]

    type_col_id = None
    type_labels = {}
    status_col_id = None
    briefed_index = None

    for c in cols:
        settings = json.loads(c["settings_str"]) if c["settings_str"] else {}
        labels = settings.get("labels", {})

        # Detect status column with "Briefed" label
        if c["type"] == "status" and c["title"].lower() == "status":
            status_col_id = c["id"]
            if isinstance(labels, dict):
                for idx, lbl in labels.items():
                    text = lbl if isinstance(lbl, str) else lbl.get("label", "")
                    if text.lower() == "briefed":
                        briefed_index = int(idx)
            elif isinstance(labels, list):
                for lbl in labels:
                    if isinstance(lbl, dict) and lbl.get("label", "").lower() == "briefed":
                        briefed_index = lbl.get("id")

        # Detect type/image-type column with a 3rd-party-like label
        if c["type"] == "status" and c["title"].lower() in (
            "type", "image type", "type of image", "image_type",
        ):
            if isinstance(labels, dict):
                for idx, lbl in labels.items():
                    text = lbl if isinstance(lbl, str) else lbl.get("label", "")
                    type_labels[int(idx)] = text
                    if text.lower() in THIRD_PARTY_LABELS:
                        type_col_id = c["id"]
            elif isinstance(labels, list):
                for lbl in labels:
                    if isinstance(lbl, dict):
                        type_labels[lbl.get("id")] = lbl.get("label", "")
                        if lbl.get("label", "").lower() in THIRD_PARTY_LABELS:
                            type_col_id = c["id"]

    return type_col_id, type_labels, status_col_id, briefed_index


def get_matching_items(board_id, type_col_id, type_index, status_col_id, briefed_index):
    """Query for items matching both filters. Returns list of item names."""
    q = """query {
        boards(ids: [%d]) {
            items_page(
                limit: 500,
                query_params: {
                    rules: [
                        { column_id: "%s", compare_value: [%d] },
                        { column_id: "%s", compare_value: [%d] }
                    ],
                    operator: and
                }
            ) {
                items { id name }
            }
        }
    }""" % (board_id, type_col_id, type_index, status_col_id, briefed_index)

    data = monday_query(q)
    return data["data"]["boards"][0]["items_page"]["items"]


# ── Main ───────────────────────────────────────────────────────────────────────

def build_report():
    """Scan all boards and return (results_list, total_count)."""
    results = []

    for board_id, name in BOARDS:
        try:
            type_col_id, type_labels, status_col_id, briefed_index = get_board_columns(board_id)

            if not type_col_id or briefed_index is None:
                results.append({
                    "board": name,
                    "count": 0,
                    "items": [],
                    "note": "No 3rd-party type column" if not type_col_id else "No Briefed status",
                })
                continue

            # Find the 3rd-party label index
            third_party_index = None
            for idx, lbl in type_labels.items():
                if lbl.lower() in THIRD_PARTY_LABELS:
                    third_party_index = idx
                    break

            if third_party_index is None:
                results.append({"board": name, "count": 0, "items": [], "note": "No 3rd-party label"})
                continue

            items = get_matching_items(board_id, type_col_id, third_party_index, status_col_id, briefed_index)
            results.append({
                "board": name,
                "count": len(items),
                "items": [i["name"] for i in items],
                "note": None,
            })

        except Exception as e:
            results.append({"board": name, "count": 0, "items": [], "note": f"Error: {e}"})

    total = sum(r["count"] for r in results)
    return results, total


def format_slack_message(results, total):
    """Build Slack message text."""
    now = datetime.now(timezone.utc).strftime("%d %b %Y")
    header = f":art: *3rd Party Images \u2014 Briefed Status Report*\n_Week of {now}_"

    has_items = [r for r in results if r["count"] > 0]
    no_items = [r for r in results if r["count"] == 0]

    lines = [header, ""]

    if total == 0:
        lines.append(":white_check_mark: No 3rd party images with *Briefed* status across any board.")
    else:
        lines.append(f"*{total} item(s)* across {len(has_items)} board(s):\n")
        for r in sorted(has_items, key=lambda x: -x["count"]):
            lines.append(f"*{r['board']}* \u2014 {r['count']} item(s)")
            for item_name in r["items"]:
                lines.append(f"    \u2022 {item_name}")
            lines.append("")

    if no_items:
        zero_names = ", ".join(r["board"] for r in no_items)
        lines.append(f"_Zero items:_ {zero_names}")

    noted = [r for r in results if r["note"]]
    if noted:
        lines.append("")
        lines.append("_Notes:_")
        for r in noted:
            lines.append(f"    \u2022 {r['board']}: {r['note']}")

    return "\n".join(lines)


def post_to_slack(message):
    """Post a message to Slack using the Bot token."""
    payload = json.dumps({
        "channel": SLACK_CHANNEL_ID,
        "text": message,
        "unfurl_links": False,
    }).encode()

    req = urllib.request.Request(
        SLACK_API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read().decode())
        if not result.get("ok"):
            print(f"Slack error: {result.get('error', 'unknown')}", file=sys.stderr)
            sys.exit(1)
        return result


def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Scanning {len(BOARDS)} illustration WIP boards...")
    results, total = build_report()

    message = format_slack_message(results, total)
    print(message)

    if "--dry-run" in sys.argv:
        print("\nDry run \u2014 message NOT posted to Slack.")
        return

    print(f"\nPosting to Slack channel {SLACK_CHANNEL_ID}...")
    post_to_slack(message)
    print("Done \u2014 message posted successfully.")


if __name__ == "__main__":
    main()
