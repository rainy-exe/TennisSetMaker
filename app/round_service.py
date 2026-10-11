from collections import defaultdict
from datetime import timedelta

from flask import current_app
from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from app.algorithm_adapter import (
    ROUND_FORMAT_MODES,
    AlgorithmPlayer,
    format_matches,
    get_algorithm_engine,
)
from app.errors import ApiError
from app.extensions import db
from app.models import (
    Player,
    Round,
    RoundEligibility,
    RoundPlayerOutcome,
    TennisSession,
    utc_now,
)


def next_round_number(session):
    return max((round_.number for round_ in session.rounds), default=0) + 1


# Prototype4's ratio contract uses the last 50 attended rounds, not 50 days.
RECENT_HISTORY_ROUNDS = 50


def _recent_history_for(club_id, player_ids):
    """Count completed final outcomes, including those outside the day window."""
    stats = {player_id: {"sit_outs": 0, "rounds": 0} for player_id in player_ids}
    if not player_ids:
        return stats
    ranked = (
        select(
            RoundPlayerOutcome.player_id,
            RoundPlayerOutcome.outcome,
            func.row_number()
            .over(
                partition_by=RoundPlayerOutcome.player_id,
                order_by=(
                    Round.completed_at.desc(),
                    Round.number.desc(),
                    Round.id.desc(),
                ),
            )
            .label("position"),
        )
        .join(Round, Round.id == RoundPlayerOutcome.round_id)
        .join(TennisSession, TennisSession.id == Round.session_id)
        .where(
            TennisSession.club_id == club_id,
            Round.status == "completed",
            RoundPlayerOutcome.version == "final",
            RoundPlayerOutcome.player_id.in_(player_ids),
        )
        .subquery()
    )
    for player_id, outcome in db.session.execute(
        select(ranked.c.player_id, ranked.c.outcome).where(
            ranked.c.position <= RECENT_HISTORY_ROUNDS
        )
    ):
        stats[player_id]["rounds"] += 1
        stats[player_id]["sit_outs"] += int(outcome == "sit_out")
    return stats


def _history_for(session, eligible_players, round_number):
    cutoff = utc_now() - timedelta(days=current_app.config["HISTORY_WINDOW_DAYS"])
    player_ids = {player.id for player in eligible_players}
    stats = {
        player.id: {
            "sit_out_count": 0,
            "last_sit_out_round": -1,
            "partners": set(),
            "opponents": set(),
            "consecutive_sit_outs": 0,
            "rounds_played": 0,
            "session_sit_outs": 0,
        }
        for player in eligible_players
    }

    prior_rounds = db.session.scalars(
        select(Round)
        .join(TennisSession)
        .where(
            TennisSession.club_id == session.club_id,
            Round.status == "completed",
            or_(Round.completed_at >= cutoff, Round.session_id == session.id),
        )
        .order_by(Round.completed_at, Round.number)
    ).all()
    for prior in prior_rounds:
        in_history_window = prior.completed_at >= cutoff
        final = [item for item in prior.outcomes if item.version == "final"]
        playing_by_court = defaultdict(lambda: defaultdict(list))
        if prior.session_id == session.id:
            # A missed/unavailable round interrupts an actual sit-out streak.
            outcome_ids = {item.player_id for item in final}
            for player_id in player_ids - outcome_ids:
                stats[player_id]["consecutive_sit_outs"] = 0
        for outcome in final:
            player_stats = stats.get(outcome.player_id)
            if outcome.outcome == "sit_out":
                if player_stats is not None:
                    if in_history_window:
                        player_stats["sit_out_count"] += 1
                    if prior.session_id == session.id:
                        player_stats["session_sit_outs"] += 1
                        player_stats["last_sit_out_round"] = prior.number
                        player_stats["consecutive_sit_outs"] += 1
            else:
                if player_stats is not None:
                    if in_history_window:
                        player_stats["rounds_played"] += 1
                    if prior.session_id == session.id:
                        player_stats["consecutive_sit_outs"] = 0
                # Include former partners/opponents even if they are no
                # longer eligible for the upcoming round.
                if in_history_window:
                    playing_by_court[outcome.court_id][outcome.team].append(
                        outcome.player_id
                    )

        for teams in playing_by_court.values():
            team_a = teams.get(1, [])
            team_b = teams.get(2, [])
            for team in (team_a, team_b):
                if len(team) == 2:
                    first, second = team
                    if first in stats:
                        stats[first]["partners"].add(second)
                    if second in stats:
                        stats[second]["partners"].add(first)
            for first in team_a:
                for second in team_b:
                    if first in stats:
                        stats[first]["opponents"].add(second)
                    if second in stats:
                        stats[second]["opponents"].add(first)

    recent = _recent_history_for(session.club_id, player_ids)
    return [
        AlgorithmPlayer(
            id=player.id,
            gender=player.gender,
            rating=player.skill,
            sit_out_count=stats[player.id]["sit_out_count"],
            last_sit_out_round=stats[player.id]["last_sit_out_round"],
            partner_ids=stats[player.id]["partners"],
            opponent_ids=stats[player.id]["opponents"],
            consecutive_sit_outs=stats[player.id]["consecutive_sit_outs"],
            rounds_played=stats[player.id]["rounds_played"],
            session_sit_outs=stats[player.id]["session_sit_outs"],
            recent_sit_outs=recent[player.id]["sit_outs"],
            recent_rounds=recent[player.id]["rounds"],
        )
        for player in eligible_players
    ]


def _eligibility_status(participation, round_number):
    player = participation.player
    if round_number < participation.joined_round:
        return "not_arrived", "Player has not arrived yet."
    if (
        participation.unavailable_from_round is not None
        and round_number >= participation.unavailable_from_round
    ):
        return "unavailable", participation.unavailable_reason
    if not player.active:
        return "inactive", "Player record is inactive."
    if player.skill is None:
        return "missing_skill", "Skill must be set by an administrator."
    return "eligible", None


def generate_next_round(session):
    if session.status in {"completed", "cancelled"}:
        raise ApiError(
            "This session cannot generate more rounds.", 409, "session_closed"
        )
    if any(round_.status == "generated" for round_ in session.rounds):
        raise ApiError(
            "Complete or discard the current generated round first.",
            409,
            "round_in_progress",
        )

    round_number = next_round_number(session)
    if round_number > session.number_of_rounds:
        raise ApiError("All configured rounds have been generated.", 409, "round_limit")

    available_courts = sorted(
        (link for link in session.courts if link.court.active),
        key=lambda link: (link.priority_order, link.court.priority, link.court.number),
    )
    if not available_courts:
        raise ApiError(
            "No selected court is currently active.", 422, "no_active_courts"
        )

    statuses = []
    eligible_players = []
    for participation in session.participants:
        status, reason = _eligibility_status(participation, round_number)
        statuses.append((participation.player, status, reason))
        if status == "eligible":
            eligible_players.append(participation.player)
    if len(eligible_players) < 4:
        raise ApiError(
            "At least four eligible players with a recorded skill are required.",
            422,
            "insufficient_eligible_players",
        )

    algorithm_players = _history_for(session, eligible_players, round_number)
    engine = get_algorithm_engine()
    result = engine.generate(
        algorithm_players,
        [link.court_id for link in available_courts],
        round_number,
        ROUND_FORMAT_MODES[session.round_format],
        session.strict_format,
    )

    valid_ids = {player.id for player in eligible_players}
    available_court_ids = {link.court_id for link in available_courts}
    returned_courts = [assignment.court_id for assignment in result.assignments]
    expected_playing = (min(len(eligible_players), len(available_courts) * 4) // 4) * 4
    if (
        len(returned_courts) != len(set(returned_courts))
        or not set(returned_courts).issubset(available_court_ids)
        or any(
            len(assignment.team_a) != 2 or len(assignment.team_b) != 2
            for assignment in result.assignments
        )
    ):
        raise ApiError(
            "The algorithm returned an invalid court or team shape.",
            502,
            "invalid_algorithm_result",
        )
    playing_ids = [
        player_id
        for assignment in result.assignments
        for player_id in assignment.team_a + assignment.team_b
    ]
    returned_ids = playing_ids + list(result.sit_out_player_ids)
    if (
        set(returned_ids) != valid_ids
        or len(returned_ids) != len(set(returned_ids))
        or len(playing_ids) != expected_playing
    ):
        raise ApiError(
            "The algorithm returned duplicate or missing players.",
            502,
            "invalid_algorithm_result",
        )
    if session.round_format != "open" and session.strict_format:
        players_by_id = {player.id: player for player in eligible_players}
        if any(
            not format_matches(
                ROUND_FORMAT_MODES[session.round_format],
                assignment.team_a,
                assignment.team_b,
                players_by_id,
            )
            for assignment in result.assignments
        ):
            raise ApiError(
                "No result satisfies the strict round format.",
                422,
                "no_valid_solution",
            )

    round_ = Round(session=session, number=round_number)
    db.session.add(round_)
    for player, status, reason in statuses:
        round_.eligibility.append(
            RoundEligibility(player_id=player.id, status=status, reason=reason)
        )

    generated = []
    for assignment in result.assignments:
        for team_number, team in ((1, assignment.team_a), (2, assignment.team_b)):
            for slot, player_id in enumerate(team, start=1):
                generated.append(
                    RoundPlayerOutcome(
                        player_id=player_id,
                        version="generated",
                        outcome="playing",
                        court_id=assignment.court_id,
                        team=team_number,
                        slot=slot,
                    )
                )
    for player_id in result.sit_out_player_ids:
        generated.append(
            RoundPlayerOutcome(
                player_id=player_id, version="generated", outcome="sit_out"
            )
        )
    round_.outcomes.extend(generated)
    for item in generated:
        round_.outcomes.append(
            RoundPlayerOutcome(
                player_id=item.player_id,
                version="final",
                outcome=item.outcome,
                court_id=item.court_id,
                team=item.team,
                slot=item.slot,
            )
        )
    session.status = "active"
    try:
        db.session.commit()
    except SQLAlchemyError as error:
        db.session.rollback()
        raise ApiError(
            "The generated round could not be saved.",
            500,
            "round_persistence_failed",
        ) from error
    return round_


def validate_override(round_, assignments, sit_out_ids):
    eligible_ids = {
        item.player_id for item in round_.eligibility if item.status == "eligible"
    }
    selected_court_ids = {
        link.court_id for link in round_.session.courts if link.court.active
    }
    seen_players = []
    seen_courts = set()
    normalized = []

    if not isinstance(assignments, list) or not isinstance(sit_out_ids, list):
        raise ApiError("assignments and sit_out_player_ids must be lists.")
    if any(not isinstance(player_id, str) for player_id in sit_out_ids):
        raise ApiError("sit_out_player_ids must contain player ID strings.")
    for assignment in assignments:
        if not isinstance(assignment, dict):
            raise ApiError("Each assignment must be an object.")
        court_id = assignment.get("court_id")
        team_a = assignment.get("team_a_player_ids")
        team_b = assignment.get("team_b_player_ids")
        if not isinstance(court_id, str) or court_id not in selected_court_ids:
            raise ApiError("An assignment uses a court outside this session.")
        if court_id in seen_courts:
            raise ApiError("A court can only appear once in a round.")
        if not isinstance(team_a, list) or not isinstance(team_b, list):
            raise ApiError("Each team must be a list of two player IDs.")
        if len(team_a) != 2 or len(team_b) != 2:
            raise ApiError("Each court must contain two teams of two players.")
        if any(not isinstance(player_id, str) for player_id in team_a + team_b):
            raise ApiError("Teams must contain player ID strings.")
        seen_courts.add(court_id)
        seen_players.extend(team_a + team_b)
        normalized.append((court_id, team_a, team_b))

    all_ids = seen_players + sit_out_ids
    if len(all_ids) != len(set(all_ids)):
        raise ApiError("A player cannot be assigned more than once in a round.")
    if set(all_ids) != eligible_ids:
        raise ApiError(
            "Every eligible player must appear exactly once as playing or sitting out.",
            details={
                "missing": sorted(eligible_ids - set(all_ids)),
                "unexpected": sorted(set(all_ids) - eligible_ids),
            },
        )
    if round_.session.round_format != "open" and round_.session.strict_format:
        players = {
            player.id: player
            for player in db.session.scalars(
                select(Player).where(Player.id.in_(seen_players))
            ).all()
        }
        for _, team_a, team_b in normalized:
            if not format_matches(
                ROUND_FORMAT_MODES[round_.session.round_format], team_a, team_b, players
            ):
                raise ApiError(
                    "Every court must satisfy the session's strict round format."
                )
    return normalized


def apply_override(round_, assignments, sit_out_ids, actor):
    if round_.status == "completed":
        raise ApiError(
            "Completed rounds must be reopened before editing.", 409, "round_locked"
        )
    normalized = validate_override(round_, assignments, sit_out_ids)

    for outcome in list(round_.outcomes):
        if outcome.version == "final":
            db.session.delete(outcome)
    try:
        db.session.flush()
    except SQLAlchemyError as error:
        db.session.rollback()
        raise ApiError(
            "The override could not be saved; the previous final round is unchanged.",
            500,
            "override_persistence_failed",
        ) from error
    for court_id, team_a, team_b in normalized:
        for team_number, team in ((1, team_a), (2, team_b)):
            for slot, player_id in enumerate(team, start=1):
                db.session.add(
                    RoundPlayerOutcome(
                        round_id=round_.id,
                        player_id=player_id,
                        version="final",
                        outcome="playing",
                        court_id=court_id,
                        team=team_number,
                        slot=slot,
                    )
                )
    for player_id in sit_out_ids:
        db.session.add(
            RoundPlayerOutcome(
                round_id=round_.id,
                player_id=player_id,
                version="final",
                outcome="sit_out",
            )
        )
    round_.override_revision += 1
    round_.overridden_at = utc_now()
    round_.overridden_by = actor.actor_id
    try:
        db.session.commit()
    except SQLAlchemyError as error:
        db.session.rollback()
        raise ApiError(
            "The override could not be saved; the previous final round is unchanged.",
            500,
            "override_persistence_failed",
        ) from error
    return round_
