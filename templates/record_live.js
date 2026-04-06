<script>
// Live record settlement — updates W/L display when games go Final
// Reads pick data embedded in the page and checks MLB API for final scores

var PICK_DATA = [];

function parsePicksFromPage() {
    var rows = document.querySelectorAll("tbody tr");
    rows.forEach(function(row, idx) {
        var cells = row.querySelectorAll("td");
        if (cells.length < 9) return;
        var pick = cells[1] ? cells[1].textContent.trim() : "";
        var game = cells[2] ? cells[2].textContent.trim() : "";
        var tier = cells[3] ? cells[3].textContent.trim() : "";
        var line = cells[4] ? cells[4].textContent.trim() : "";
        var resultCell = cells[8];
        var unitsCell = cells[9];
        var resultSpan = resultCell ? resultCell.querySelector("span") : null;
        if (resultSpan && resultSpan.textContent.trim() === "PENDING") {
            PICK_DATA.push({idx:idx,pick:pick,game:game,tier:tier,line:line,resultSpan:resultSpan,unitsCell:unitsCell,row:row});
        }
    });
}

function americanToDecimal(odds) {
    odds = parseFloat(odds);
    if (isNaN(odds)) return 1.909;
    if (odds < 0) return 1 + (100 / Math.abs(odds));
    return 1 + (odds / 100);
}

function calcUnitsResult(result, line, units) {
    units = parseFloat(units) || 1.0;
    if (result === "W") return Math.round((units * (americanToDecimal(line) - 1)) * 100) / 100;
    if (result === "L") return -units;
    return 0;
}

function settlePick(pick, game, line, scores) {
    var parts = game.split(" @ ");
    if (parts.length !== 2) return null;
    var away = parts[0].trim(), home = parts[1].trim();
    var score = null;
    for (var key in scores) {
        if (key.indexOf(away) >= 0 && key.indexOf(home) >= 0) { score = scores[key]; break; }
    }
    if (!score) return null;
    var awayScore = score.away_score, homeScore = score.home_score;
    var total = awayScore + homeScore, pickUp = pick.toUpperCase();
    if (pickUp.indexOf("OVER") >= 0 || pickUp.indexOf("UNDER") >= 0) {
        var m = pick.match(/[0-9.]+/); if (!m) return null;
        var ln = parseFloat(m[0]);
        if (total > ln) return pickUp.indexOf("OVER") >= 0 ? "W" : "L";
        if (total < ln) return pickUp.indexOf("UNDER") >= 0 ? "W" : "L";
        return "P";
    }
    if (pickUp.indexOf("ML") >= 0) {
        var winner = homeScore > awayScore ? home : away;
        for (var t of [away, home]) {
            if (pickUp.indexOf(t.toUpperCase().split(" ").pop()) >= 0) return winner === t ? "W" : "L";
        }
    }
    if (pickUp.indexOf("+1.5") >= 0 || pickUp.indexOf("-1.5") >= 0) {
        var spread = pickUp.indexOf("+1.5") >= 0 ? 1.5 : -1.5;
        for (var t of [away, home]) {
            if (pickUp.indexOf(t.toUpperCase().split(" ").pop()) >= 0) {
                var ts = t === home ? homeScore : awayScore, os = t === home ? awayScore : homeScore;
                var adj = ts - os + spread;
                if (adj > 0) return "W"; if (adj < 0) return "L"; return "P";
            }
        }
    }
    if (pickUp === "NRFI" || pickUp === "YRFI") {
        if (!score.inning1) return null;
        var r1 = (score.inning1.away || 0) + (score.inning1.home || 0);
        if (pickUp === "NRFI") return r1 === 0 ? "W" : "L";
        if (pickUp === "YRFI") return r1 > 0 ? "W" : "L";
    }
    return null;
}

function updateRecordDisplay(scores) {
    PICK_DATA.forEach(function(pd) {
        var result = settlePick(pd.pick, pd.game, pd.line, scores);
        if (!result) return;
        var isWatch = pd.tier === "WATCH";
        var units = isWatch ? 0 : parseFloat(pd.tier === "A" ? 1.5 : pd.tier === "MAX" ? 3.0 : pd.tier === "C" ? 0.5 : 1.0);
        var unitsResult = isWatch ? 0 : calcUnitsResult(result, pd.line, units);
        var color = result === "W" ? "#22D47A" : result === "L" ? "#F04D5A" : "#8A95A8";
        pd.resultSpan.textContent = result === "W" ? "WIN" : result === "L" ? "LOSS" : "PUSH";
        pd.resultSpan.style.color = color;
        if (pd.unitsCell) {
            pd.unitsCell.textContent = (unitsResult >= 0 ? "+" : "") + unitsResult + "u";
            pd.unitsCell.style.color = color;
        }
    });
    var lu = document.getElementById("live_update");
    if (lu) lu.textContent = "Live results updated " + new Date().toLocaleTimeString("en-US", {timeZone:"America/New_York",hour:"numeric",minute:"2-digit"}) + " ET";
}

function fetchScoresForRecord() {
    var dates = new Set();
    dates.add(new Date().toLocaleDateString("en-CA", {timeZone:"America/New_York"}));
    var allScores = {}, promises = [];
    dates.forEach(function(date) {
        if (!date || date.length < 8) return;
        var p = fetch("https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=" + date + "&hydrate=linescore,team")
            .then(function(r) { return r.json(); })
            .then(function(data) {
                (data.dates || []).forEach(function(d) {
                    (d.games || []).forEach(function(g) {
                        if (g.status.abstractGameState !== "Final") return;
                        var away = g.teams.away.team.name, home = g.teams.home.team.name;
                        var innings = (g.linescore && g.linescore.innings) ? g.linescore.innings : [];
                        var inning1 = innings.length > 0 ? {away: innings[0].away ? (innings[0].away.runs||0) : 0, home: innings[0].home ? (innings[0].home.runs||0) : 0} : null;
                        allScores[away + " @ " + home] = {away_score: g.teams.away.score||0, home_score: g.teams.home.score||0, inning1: inning1};
                    });
                });
            }).catch(function() {});
        promises.push(p);
    });
    Promise.all(promises).then(function() {
        if (Object.keys(allScores).length > 0) updateRecordDisplay(allScores);
    });
}

document.addEventListener("DOMContentLoaded", function() {
    parsePicksFromPage();
    if (PICK_DATA.length > 0) { fetchScoresForRecord(); setInterval(fetchScoresForRecord, 120000); }
});
</script>
