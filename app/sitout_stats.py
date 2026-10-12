"""Sit-out statistics, used to show players that sit-outs are shared evenly.

Everything comes from the saved round results (the "final" version of each
completed round), so no extra data is stored.

Per player, over the last RECENT_ROUNDS rounds they took part in (the same
window the algorithm uses):

- rounds:   rounds they were at (played or sat out)
- sat_out:  times they sat out
- expected: the sit-outs you would expect for those exact rounds. A round with
            26 players and 24 spots gives everyone there 2/26 of a sit-out; the
            player's expected total is the sum over their rounds. This is fairer
            than comparing raw rates, because busy days mean more sit-outs for
            everyone who came.
- balance:  sat_out - expected. Within one sit-out either way counts as even.
- staff_sat_out: sit-outs that came from a manual change, not the algorithm
            (the generated version had them playing).
"""

from collections import Counter, defaultdict

from sqlalchemy import select

from app.extensions import db
from app.models import Player, Round, RoundPlayerOutcome, TennisSession

RECENT_ROUNDS = 50  # same window as the algorithm's sit-out ratio
EVEN_MARGIN = 1.0  # |balance| below this reads as "even"


def _club_rounds(club_id):
    """Final outcomes of every completed round in the club, grouped by round."""
    rows = db.session.execute(
        select(
            RoundPlayerOutcome.round_id,
            RoundPlayerOutcome.player_id,
            RoundPlayerOutcome.outcome,
            RoundPlayerOutcome.court_id,
            RoundPlayerOutcome.team,
            Round.number,
            Round.completed_at,
            TennisSession.id,
            TennisSession.name,
            TennisSession.starts_at,
        )
        .join(Round, Round.id == RoundPlayerOutcome.round_id)
        .join(TennisSession, TennisSession.id == Round.session_id)
        .where(
            TennisSession.club_id == club_id,
            Round.status == "completed",
            RoundPlayerOutcome.version == "final",
        )
    ).all()

    generated_sit_outs = set(
        db.session.execute(
            select(RoundPlayerOutcome.round_id, RoundPlayerOutcome.player_id)
            .join(Round, Round.id == RoundPlayerOutcome.round_id)
            .join(TennisSession, TennisSession.id == Round.session_id)
            .where(
                TennisSession.club_id == club_id,
                Round.status == "completed",
                RoundPlayerOutcome.version == "generated",
                RoundPlayerOutcome.outcome == "sit_out",
            )
        ).all()
    )

    rounds = {}
    for (round_id, player_id, outcome, court_id, team, number, completed_at,
         session_id, session_name, starts_at) in rows:
        info = rounds.setdefault(
            round_id,
            {
                "id": round_id,
                "number": number,
                "completed_at": completed_at,
                "session_id": session_id,
                "session_name": session_name,
                "starts_at": starts_at,
                "outcomes": {},
                "teams": defaultdict(list),
            },
        )
        sat_out = outcome == "sit_out"
        info["outcomes"][player_id] = {
            "sat_out": sat_out,
            "staff": sat_out and (round_id, player_id) not in generated_sit_outs,
        }
        if not sat_out and court_id is not None:
            info["teams"][(court_id, team)].append(player_id)

    for info in rounds.values():
        size = len(info["outcomes"])
        sits = sum(1 for item in info["outcomes"].values() if item["sat_out"])
        info["share"] = sits / size if size else 0.0
    return rounds


def _windows(rounds):
    """Each player's most recent RECENT_ROUNDS rounds, newest first."""
    by_player = defaultdict(list)
    ordered = sorted(
        rounds.values(),
        key=lambda r: (r["completed_at"] or r["starts_at"], r["number"]),
        reverse=True,
    )
    for info in ordered:
        for player_id in info["outcomes"]:
            if len(by_player[player_id]) < RECENT_ROUNDS:
                by_player[player_id].append(info)
    return by_player


def _status(balance, rounds):
    if not rounds:
        return "none"
    if balance >= EVEN_MARGIN:
        return "above"
    if balance <= -EVEN_MARGIN:
        return "below"
    return "even"


def _summary(player_id, window):
    sat_out = sum(1 for r in window if r["outcomes"][player_id]["sat_out"])
    staff = sum(1 for r in window if r["outcomes"][player_id]["staff"])
    expected = sum(r["share"] for r in window)
    balance = sat_out - expected
    return {
        "rounds": len(window),
        "sat_out": sat_out,
        "staff_sat_out": staff,
        "expected": round(expected, 1),
        "balance": round(balance, 1),
        "rate": round(sat_out / len(window), 3) if window else None,
        "status": _status(balance, len(window)),
    }


def _club_rate(windows):
    rounds = sum(len(window) for window in windows.values())
    sits = sum(
        1
        for player_id, window in windows.items()
        for r in window
        if r["outcomes"][player_id]["sat_out"]
    )
    return round(sits / rounds, 3) if rounds else None


def club_summaries(club_id):
    """{player_id: summary} for everyone with history, plus the club rate."""
    windows = _windows(_club_rounds(club_id))
    summaries = {player_id: _summary(player_id, window) for player_id, window in windows.items()}
    return summaries, _club_rate(windows)


def player_report(club_id, player_id):
    """Everything the player detail card and full-screen view show."""
    rounds = _club_rounds(club_id)
    windows = _windows(rounds)
    window = windows.get(player_id, [])
    summary = _summary(player_id, window)

    # Oldest first, so the timeline reads left to right.
    timeline = [
        {
            "date": (r["starts_at"]).date().isoformat(),
            "session": r["session_name"],
            "round": r["number"],
            "sat_out": r["outcomes"][player_id]["sat_out"],
            "staff": r["outcomes"][player_id]["staff"],
        }
        for r in reversed(window)
    ]

    sessions = []
    seen = {}
    for r in window:  # newest first
        entry = seen.get(r["session_id"])
        if entry is None:
            entry = seen[r["session_id"]] = {
                "name": r["session_name"],
                "date": r["starts_at"].date().isoformat(),
                "rounds": 0,
                "sat_out": 0,
                "expected": 0.0,
            }
            sessions.append(entry)
        entry["rounds"] += 1
        entry["sat_out"] += int(r["outcomes"][player_id]["sat_out"])
        entry["expected"] += r["share"]
    for entry in sessions:
        entry["expected"] = round(entry["expected"], 1)

    partners = Counter()
    for r in window:
        for members in r["teams"].values():
            if player_id in members:
                partners.update(other for other in members if other != player_id)
    names = dict(
        db.session.execute(
            select(Player.id, Player.name).where(Player.id.in_(list(partners) or [""]))
        ).all()
    )

    club = []
    for other_id, other_window in windows.items():
        if len(other_window) >= 5 or other_id == player_id:
            other = _summary(other_id, other_window)
            club.append({"rate": other["rate"], "balance": other["balance"], "is_player": other_id == player_id})

    return {
        "window": RECENT_ROUNDS,
        "summary": summary,
        "club_rate": _club_rate(windows),
        "timeline": timeline,
        "sessions": sessions[:8],
        "partners": [
            {"name": names.get(other, "Unknown"), "rounds": count}
            for other, count in partners.most_common(5)
        ],
        "club": club,
    }
