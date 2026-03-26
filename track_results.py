"""
MLB Model — Results Tracker
Run this after games finish to log results and measure model performance.

Usage:
  python track_results.py

It will load today's picks from output/picks.json and prompt you to enter results.
Appends to output/results_log.json and prints running P/L.
"""

import json, datetime
from pathlib import Path

OUTPUT_DIR = Path("output")
PICKS_FILE = OUTPUT_DIR / "picks.json"
LOG_FILE   = OUTPUT_DIR / "results_log.json"

def load_log():
    if LOG_FILE.exists():
        return json.loads(LOG_FILE.read_text())
    return []

def save_log(log):
    LOG_FILE.write_text(json.dumps(log, indent=2))

def american_to_decimal(american):
    """Convert American odds to decimal for P/L calculation."""
    try:
        o = int(str(american).replace("+", ""))
        if o > 0:
            return (o / 100) + 1
        else:
            return (100 / abs(o)) + 1
    except:
        return 1.909  # default -110

def calc_pl(units, odds_str, won):
    decimal = american_to_decimal(odds_str)
    if won:
        return round(units * (decimal - 1), 2)
    else:
        return round(-units, 2)

def main():
    if not PICKS_FILE.exists():
        print("No picks.json found. Run generate_picks.py first.")
        return

    data = json.loads(PICKS_FILE.read_text())
    picks = [p for p in data.get("picks", []) if p.get("tier") != "SKIP"]

    if not picks:
        print("No active picks found in today's file.")
        return

    log = load_log()
    today = data["date"]

    # Check if today already logged
    already_logged = [e for e in log if e["date"] == today]
    if already_logged:
        print(f"Results for {today} already logged. Showing summary.\n")
    else:
        print(f"\n=== Log Results for {today} ===\n")
        entries = []
        for i, p in enumerate(picks):
            print(f"[{i+1}] {p['pick']} ({p['game']}) · {p['line']} · {p['units']}u")
            result = input("    Result? (w=win / l=loss / p=push / skip): ").strip().lower()
            if result == "skip":
                continue
            won = result == "w"
            push = result == "p"
            pl = 0 if push else calc_pl(p["units"], p["line"], won)
            entries.append({
                "date": today,
                "game": p["game"],
                "pick": p["pick"],
                "bet_type": p["bet_type"],
                "line": p["line"],
                "tier": p["tier"],
                "units": p["units"],
                "result": "win" if won else ("push" if push else "loss"),
                "pl": pl,
            })
            print(f"    P/L: {'+' if pl >= 0 else ''}{pl}u\n")

        log.extend(entries)
        save_log(log)
        print(f"Logged {len(entries)} results.")

    # Print running stats
    print("\n=== Season Summary ===")
    wins   = [e for e in log if e["result"] == "win"]
    losses = [e for e in log if e["result"] == "loss"]
    pushes = [e for e in log if e["result"] == "push"]
    total_pl = round(sum(e["pl"] for e in log), 2)
    total_bet = sum(e["units"] for e in log if e["result"] != "push")
    roi = round((total_pl / total_bet * 100), 1) if total_bet else 0

    print(f"Record : {len(wins)}-{len(losses)}-{len(pushes)}")
    print(f"Units  : {'+' if total_pl >= 0 else ''}{total_pl}u")
    print(f"ROI    : {'+' if roi >= 0 else ''}{roi}%")
    print(f"Bets   : {len(wins) + len(losses)}")

    # Break down by tier
    print("\nBy Tier:")
    for tier in ["A", "B", "C"]:
        tier_entries = [e for e in log if e["tier"] == tier and e["result"] != "push"]
        if not tier_entries:
            continue
        tier_wins = len([e for e in tier_entries if e["result"] == "win"])
        tier_pl = round(sum(e["pl"] for e in tier_entries), 2)
        tier_roi = round(tier_pl / sum(e["units"] for e in tier_entries) * 100, 1)
        print(f"  Tier {tier}: {tier_wins}-{len(tier_entries)-tier_wins} · {'+' if tier_pl>=0 else ''}{tier_pl}u · ROI {'+' if tier_roi>=0 else ''}{tier_roi}%")

if __name__ == "__main__":
    main()
