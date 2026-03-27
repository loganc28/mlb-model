# --- ONLY SHOWING MODIFIED + SAFE VERSION ---
# KEEP EVERYTHING ABOVE YOUR fetch_odds() THE SAME
# REPLACE fetch_odds() WITH THIS VERSION

def fetch_odds():
    if not ODDS_API_KEY:
        return {}

    import statistics

    SHARP_BOOKS = {"draftkings", "fanduel", "betmgm", "caesars"}

    try:
        r = requests.get(
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/",
            params={
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": "h2h,totals",
                "oddsFormat": "american",
                "dateFormat": "iso"
            },
            timeout=10
        )
        r.raise_for_status()

        odds_map = {}

        for event in r.json():
            home = event.get("home_team", "")
            away = event.get("away_team", "")

            ml_prices = {}
            totals_list = []

            for bm in event.get("bookmakers", []):
                if bm.get("key") not in SHARP_BOOKS:
                    continue

                for market in bm.get("markets", []):

                    # MONEYLINE
                    if market["key"] == "h2h":
                        for o in market["outcomes"]:
                            ml_prices[o["name"]] = o["price"]

                    # TOTALS
                    elif market["key"] == "totals":
                        over = next((o for o in market["outcomes"] if o["name"] == "Over"), None)
                        under = next((o for o in market["outcomes"] if o["name"] == "Under"), None)

                        if over and under:
                            line_o = over.get("point")
                            line_u = under.get("point")

                            if line_o and line_o == line_u:
                                totals_list.append({
                                    "line": float(line_o),
                                    "over": over.get("price", -110),
                                    "under": under.get("price", -110)
                                })

            # SAFE DEFAULT (prevents crashes)
            total = {
                "line": 8.5,
                "over": -110,
                "under": -110
            }

            if totals_list:
                try:
                    lines = [t["line"] for t in totals_list if t["line"]]
                    if lines:
                        median_line = statistics.median(lines)

                        best = min(
                            totals_list,
                            key=lambda x: abs(x["line"] - median_line)
                        )

                        total = {
                            "line": median_line,
                            "over": best["over"],
                            "under": best["under"]
                        }

                        if median_line < 6:
                            print(f"⚠️ BAD TOTAL: {away}@{home} → {median_line}")
                            print("RAW:", totals_list)

                except Exception as e:
                    print("Totals calc error:", e)

            print(f"{away}@{home} totals →", total)

            odds_map[f"{away}@{home}"] = {
                "moneyline": ml_prices or {},
                "total": total
            }

        return odds_map

    except Exception as e:
        print("Odds error:", e)
        return {}
