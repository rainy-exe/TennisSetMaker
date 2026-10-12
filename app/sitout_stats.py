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

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import aliased

from app.extensions import db
from app.models import Player, Round, RoundPlayerOutcome, TennisSession

RECENT_ROUNDS = 50  # same window as the algorithm's sit-out ratio
EVEN_MARGIN = 1.0  # |balance| below this reads as "even"
TOP_PARTNERS = 10


def _window_rows(club_id):
    """Each player's last RECENT_ROUNDS completed rounds, filtered in the
    database (like round_service._recent_history_for), so the work stays the
    same size however much history the club has. One row per player-round,
    newest first per player."""
    final = aliased(RoundPlayerOutcome)
    generated = aliased(RoundPlayerOutcome)
    ranked = (
        select(
            final.player_id.label("player_id"),
            final.round_id.label("round_id"),
            final.outcome.label("outcome"),
            func.row_number()
            .over(
                partition_by=final.player_id,
                order_by=(Round.completed_at.desc(), Round.number.desc(), Round.id.desc()),
            )
            .label("position"),
        )
        .join(Round, Round.id == final.round_id)
        .join(TennisSession, TennisSession.id == Round.session_id)
        .where(
            TennisSession.club_id == club_id,
            Round.status == "completed",
            final.version == "final",
        )
        .subquery()
    )
    in_window = select(ranked.c.round_id).where(ranked.c.position <= RECENT_ROUNDS)
    # How many were at each of those rounds, and how many sat out.
    sizes = (
        select(
            RoundPlayerOutcome.round_id.label("round_id"),
            func.count().label("size"),
            func.sum(case((RoundPlayerOutcome.outcome == "sit_out", 1), else_=0)).label("sits"),
        )
        .where(
            RoundPlayerOutcome.version == "final",
            RoundPlayerOutcome.round_id.in_(in_window),
        )
        .group_by(RoundPlayerOutcome.round_id)
        .subquery()
    )
    return db.session.execute(
        select(
            ranked.c.player_id,
            ranked.c.round_id,
            ranked.c.outcome,
            sizes.c.size,
            sizes.c.sits,
            generated.outcome,
            Round.number,
            TennisSession.id,
            TennisSession.name,
            TennisSession.starts_at,
        )
        .select_from(ranked)
        .join(sizes, sizes.c.round_id == ranked.c.round_id)
        .join(Round, Round.id == ranked.c.round_id)
        .join(TennisSession, TennisSession.id == Round.session_id)
        .outerjoin(
            generated,
            and_(
                generated.round_id == ranked.c.round_id,
                generated.player_id == ranked.c.player_id,
                generated.version == "generated",
            ),
        )
        .where(ranked.c.position <= RECENT_ROUNDS)
        .order_by(ranked.c.player_id, ranked.c.position)
    ).all()


def _build_windows(rows):
    """{player_id: [round, ...]} newest first."""
    windows = defaultdict(list)
    for (player_id, round_id, outcome, size, sits, generated_outcome, number,
         session_id, session_name, starts_at) in rows:
        sat_out = outcome == "sit_out"
        windows[player_id].append(
            {
                "round_id": round_id,
                "number": number,
                "session_id": session_id,
                "session_name": session_name,
                "starts_at": starts_at,
                "sat_out": sat_out,
                # The algorithm had them playing; a staff change sat them out.
                "staff": sat_out and generated_outcome == "playing",
                "share": (sits or 0) / size if size else 0.0,
            }
        )
    return windows


def _status(balance, rounds):
    if not rounds:
        return "none"
    if balance >= EVEN_MARGIN:
        return "above"
    if balance <= -EVEN_MARGIN:
        return "below"
    return "even"


def _summary(window):
    sat_out = sum(1 for r in window if r["sat_out"])
    staff = sum(1 for r in window if r["staff"])
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
    sits = sum(1 for window in windows.values() for r in window if r["sat_out"])
    return round(sits / rounds, 3) if rounds else None


def _partner_counts(player_id, team_rows):
    """team_rows: (round_id, player_id, court_id, team) for players who played."""
    teams = defaultdict(list)
    for round_id, other_id, court_id, team in team_rows:
        teams[(round_id, court_id, team)].append(other_id)
    counts = Counter()
    for members in teams.values():
        if player_id in members:
            counts.update(other for other in members if other != player_id)
    return counts


def club_summaries(club_id):
    """{player_id: summary} for everyone with history, plus the club rate."""
    windows = _build_windows(_window_rows(club_id))
    summaries = {player_id: _summary(window) for player_id, window in windows.items()}
    return summaries, _club_rate(windows)


def player_report(club_id, player_id):
    """Everything the player detail card and full-screen view show."""
    windows = _build_windows(_window_rows(club_id))
    window = windows.get(player_id, [])
    summary = _summary(window)

    # Oldest first, so the timeline reads left to right.
    timeline = [
        {
            "date": r["starts_at"].date().isoformat(),
            "session": r["session_name"],
            "round": r["number"],
            "sat_out": r["sat_out"],
            "staff": r["staff"],
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
        entry["sat_out"] += int(r["sat_out"])
        entry["expected"] += r["share"]
    for entry in sessions:
        entry["expected"] = round(entry["expected"], 1)

    partners = Counter()
    round_ids = [r["round_id"] for r in window if not r["sat_out"]]
    if round_ids:
        partners = _partner_counts(
            player_id,
            db.session.execute(
                select(
                    RoundPlayerOutcome.round_id,
                    RoundPlayerOutcome.player_id,
                    RoundPlayerOutcome.court_id,
                    RoundPlayerOutcome.team,
                ).where(
                    RoundPlayerOutcome.round_id.in_(round_ids),
                    RoundPlayerOutcome.version == "final",
                    RoundPlayerOutcome.outcome == "playing",
                )
            ).all(),
        )
    top = partners.most_common(TOP_PARTNERS)
    names = dict(
        db.session.execute(
            select(Player.id, Player.name).where(Player.id.in_([other for other, _ in top] or [""]))
        ).all()
    )

    club = []
    for other_id, other_window in windows.items():
        if len(other_window) >= 5 or other_id == player_id:
            other = _summary(other_window)
            club.append({"rate": other["rate"], "balance": other["balance"], "is_player": other_id == player_id})

    return {
        "window": RECENT_ROUNDS,
        "summary": summary,
        "club_rate": _club_rate(windows),
        "timeline": timeline,
        "sessions": sessions[:8],
        "partners": [{"name": names.get(other, "Unknown"), "rounds": count} for other, count in top],
        "club": club,
    }
