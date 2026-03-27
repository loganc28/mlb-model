def fetch_odds():
    if not ODDS_API_KEY:
        return {}

    import statistics

    # Only use reliable books
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

            # LOOP ALL BOOKS (not just first one)
            for bm in event.get("bookmakers", []):
                if bm.get("key") not in SHARP_BOOKS:
                    continue

                for market in bm.get("markets", []):

                    # ---- MONEYLINE ----
                    if market["key"] == "h2h":
                        for o in market["outcomes"]:
                            ml_prices[o["name"]] = o["price"]

                    # ---- TOTALS ----
                    elif market["key"] == "totals":
                        over = next((o for o in market["outcomes"] if o["name"] == "Over"), None)
                        under = next((o for o in market["outcomes"] if o["name"] == "Under"), None)

                        if over and under:
                            line_o = over.get("point")
                            line_u = under.get("point")

                            # Only accept valid main line (filters alt totals)
                            if line_o and line_o == line_u:
                                totals_list.append({
                                    "line": float(line_o),
                                    "over": over.get("price"),
                                    "under": under.get("price")
                                })

            # ---- AGGREGATE TOTALS ----
            total = {}

            if totals_list:
                lines = [t["line"] for t in totals_list if t["line"]]

                if lines:
                    median_line = statistics.median(lines)

                    # Pick book closest to consensus for pricing
                    best = min(
                        totals_list,
                        key=lambda x: abs(x["line"] - median_line)
                    )

                    total = {
                        "line": median_line,
                        "over": best["over"],
                        "under": best["under"]
                    }

                    # HARD GUARDRAIL (prevents garbage like 4.5)
                    if median_line < 6:
                        print(f"⚠️ Suspicious low total detected: {away}@{home} → {median_line}")
                        print("Raw totals:", totals_list)

            # DEBUG LOG (keep this while testing)
            print(f"{away}@{home} totals from books:", totals_list)

            odds_map[f"{away}@{home}"] = {
                "moneyline": ml_prices,
                "total": total
            }

        return odds_map

    except Exception as e:
        print("Odds error: " + str(e))
        return {}
