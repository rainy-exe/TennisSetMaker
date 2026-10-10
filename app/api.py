import unicodedata
import csv
import io
from datetime import datetime, time
from difflib import SequenceMatcher
from flask import Blueprint, jsonify, request, redirect, url_for

from flask import Blueprint, jsonify, request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.errors import ApiError
from app.extensions import db
from app.models import (
    Court,
    Player,
    Round,
    SessionCourt,
    SessionParticipation,
    SessionTemplate,
    SessionTemplateCourt,
    TennisSession,
    utc_now,
)
from app.permissions import (
    Role,
    can_add_player,
    can_delete_player,
    can_manage_skill,
    can_view_skill,
    current_actor,
    require_authenticated,
    require_roles,
)
from app.round_service import apply_override, generate_next_round, next_round_number

api = Blueprint("api", __name__)
MANAGERS = (Role.OWNER, Role.ADMIN)


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("A JSON object is required.")
    return data


def clean_text(value, field, max_length=120):
    if not isinstance(value, str) or not value.strip():
        raise ApiError(f"{field} must be a non-empty string.")
    value = value.strip()
    if len(value) > max_length:
        raise ApiError(f"{field} must be at most {max_length} characters.")
    return value


def positive_int(value, field, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ApiError(f"{field} must be a positive integer.")
    if maximum is not None and value > maximum:
        raise ApiError(f"{field} must be no greater than {maximum}.")
    return value


def optional_text(value, field, max_length=120):
    if value is None or value == "":
        return None
    return clean_text(value, field, max_length)


def player_details(player, data):
    if "notes" in data:
        player.notes = optional_text(data["notes"], "notes", 4000)
    if "court_preference" in data:
        preference = optional_text(data["court_preference"], "court_preference", 20)
        if preference not in {None, "Clay", "Hard", "Grass", "Synthetic"}:
            raise ApiError("court_preference must be Clay, Hard, Grass or Synthetic.")
        player.court_preference = preference


def nonnegative_int(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ApiError(f"{field} must be a non-negative integer.")
    return value


def parse_bool(value, field):
    if not isinstance(value, bool):
        raise ApiError(f"{field} must be true or false.")
    return value


def normalize_name(name):
    folded = unicodedata.normalize("NFKC", name).casefold()
    return " ".join(
        "".join(
            character if character.isalnum() else " " for character in folded
        ).split()
    )


def duplicate_warnings(club_id, name, excluding_id=None):
    normalized = normalize_name(name)
    players = db.session.scalars(select(Player).where(Player.club_id == club_id)).all()
    warnings = []
    for player in players:
        if player.id == excluding_id:
            continue
        score = SequenceMatcher(None, normalized, player.normalized_name).ratio()
        if normalized == player.normalized_name or score >= 0.92:
            warnings.append(
                {
                    "player_id": player.id,
                    "name": player.name,
                    "active": player.active,
                    "similarity": round(score, 2),
                }
            )
    return warnings


def commit_or_conflict(message):
    try:
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        raise ApiError(message, 409, "conflict") from error


def player_for_club(player_id):
    actor = current_actor()
    player = db.session.get(Player, player_id)
    if player is None or player.club_id != actor.club_id:
        raise ApiError("Player not found.", 404, "not_found")
    return player


def court_for_club(court_id):
    actor = current_actor()
    court = db.session.get(Court, court_id)
    if court is None or court.club_id != actor.club_id:
        raise ApiError("Court not found.", 404, "not_found")
    return court


def template_for_club(template_id):
    actor = current_actor()
    template = db.session.get(SessionTemplate, template_id)
    if template is None or template.club_id != actor.club_id:
        raise ApiError("Session template not found.", 404, "not_found")
    return template


def session_for_club(session_id):
    actor = current_actor()
    session = db.session.get(TennisSession, session_id)
    if session is None or session.club_id != actor.club_id:
        raise ApiError("Session not found.", 404, "not_found")
    return session


def round_for_club(round_id):
    actor = current_actor()
    round_ = db.session.get(Round, round_id)
    if round_ is None or round_.session.club_id != actor.club_id:
        raise ApiError("Round not found.", 404, "not_found")
    return round_


def serialize_player(player):
    result = {
        "id": player.id,
        "name": player.name,
        "gender": player.gender,
        "active": player.active,
        "skill_ready": player.skill is not None,
        "notes": player.notes,
        "court_preference": player.court_preference,
        "created_at": player.created_at.isoformat(),
        "updated_at": player.updated_at.isoformat(),
    }
    if can_view_skill():
        result["skill"] = player.skill
    return result


def serialize_court(court):
    return {
        "id": court.id,
        "number": court.number,
        "court_type": court.court_type,
        "active": court.active,
        "priority": court.priority,
        "cover": court.cover,
    }


def serialize_template(template):
    return {
        "id": template.id,
        "name": template.name,
        "number_of_rounds": template.number_of_rounds,
        "session_duration_minutes": template.session_duration_minutes,
        "round_duration_minutes": template.round_duration_minutes,
        "start_time": template.start_time.isoformat(timespec="minutes"),
        "round_format": template.round_format,
        "strict_format": template.strict_format,
        "tea_break_after_round": template.tea_break_after_round,
        "active": template.active,
        "court_ids": [
            link.court_id
            for link in sorted(template.courts, key=lambda item: item.priority_order)
        ],
    }


def serialize_round(round_):
    versions = {"generated": [], "final": []}
    for version in versions:
        outcomes = [item for item in round_.outcomes if item.version == version]
        courts = {}
        sit_outs = []
        for outcome in outcomes:
            if outcome.outcome == "sit_out":
                sit_outs.append(outcome.player_id)
                continue
            entry = courts.setdefault(
                outcome.court_id,
                {
                    "court_id": outcome.court_id,
                    "team_a_player_ids": [],
                    "team_b_player_ids": [],
                },
            )
            key = "team_a_player_ids" if outcome.team == 1 else "team_b_player_ids"
            entry[key].append((outcome.slot, outcome.player_id))
        assignments = []
        priority = {
            link.court_id: link.priority_order for link in round_.session.courts
        }
        for entry in sorted(
            courts.values(), key=lambda item: priority.get(item["court_id"], 10_000)
        ):
            for key in ("team_a_player_ids", "team_b_player_ids"):
                entry[key] = [player_id for _, player_id in sorted(entry[key])]
            assignments.append(entry)
        versions[version] = {
            "assignments": assignments,
            "sit_out_player_ids": sorted(sit_outs),
        }
    return {
        "id": round_.id,
        "session_id": round_.session_id,
        "number": round_.number,
        "status": round_.status,
        "override_revision": round_.override_revision,
        "generated": versions["generated"],
        "final": versions["final"],
        "eligibility": [
            {"player_id": item.player_id, "status": item.status, "reason": item.reason}
            for item in round_.eligibility
        ],
        "completed_at": round_.completed_at.isoformat()
        if round_.completed_at
        else None,
    }


def serialize_session(session, include_rounds=True):
    payload = {
        "id": session.id,
        "name": session.name,
        "template_id": session.template_id,
        "starts_at": session.starts_at.isoformat(),
        "number_of_rounds": session.number_of_rounds,
        "session_duration_minutes": session.session_duration_minutes,
        "round_duration_minutes": session.round_duration_minutes,
        "round_format": session.round_format,
        "strict_format": session.strict_format,
        "tea_break_after_round": session.tea_break_after_round,
        "status": session.status,
        "court_ids": [
            link.court_id
            for link in sorted(session.courts, key=lambda item: item.priority_order)
        ],
        "participants": [
            {
                "player_id": item.player_id,
                "joined_round": item.joined_round,
                "unavailable_from_round": item.unavailable_from_round,
                "unavailable_reason": item.unavailable_reason,
            }
            for item in session.participants
        ],
    }
    if include_rounds:
        payload["rounds"] = [
            serialize_round(item)
            for item in sorted(session.rounds, key=lambda r: r.number)
        ]
    return payload


@api.get("/health")
def health():
    return jsonify({"status": "ok"})


@api.get("/players")
@require_authenticated
def list_players():
    actor = current_actor()
    query = select(Player).where(Player.club_id == actor.club_id)
    if request.args.get("include_inactive", "false").lower() != "true":
        query = query.where(Player.active.is_(True))
    players = db.session.scalars(query.order_by(Player.name)).all()
    return jsonify({"players": [serialize_player(player) for player in players]})


@api.post("/players")
@require_authenticated
def create_player():
    actor = current_actor()
    if not can_add_player(actor):
        raise ApiError(
            "You do not have permission to add players.",
            403,
            "forbidden",
        )

    data = body()
    name = clean_text(data.get("name"), "name")
    warnings = duplicate_warnings(actor.club_id, name)
    if warnings and data.get("confirm_duplicate") is not True:
        raise ApiError(
            "A player with the same or a similar name already exists. Confirm to continue.",
            409,
            "duplicate_confirmation_required",
            warnings,
        )
    gender = clean_text(data.get("gender"), "gender", 20).upper()
    if gender not in {"M", "F"}:
        raise ApiError("gender must be M or F.")
    skill = data.get("skill")
    if not can_manage_skill(actor):
        if "skill" in data:
            raise ApiError(
                "You don't have permission to set player skill.", 403, "forbidden"
            )
        skill = None
    elif skill is not None:
        skill = positive_int(skill, "skill", 10)
    player = Player(
        club_id=actor.club_id,
        name=name,
        normalized_name=normalize_name(name),
        gender=gender,
        skill=skill,
    )
    player_details(player, data)
    db.session.add(player)
    db.session.commit()
    return jsonify(
        {"player": serialize_player(player), "duplicate_warnings": warnings}
    ), 201

@api.post("/players/import")
@require_roles(*MANAGERS)
def import_players_csv():
    actor = current_actor()

    def go_back(**params):
        return redirect(url_for("main.players", **params))

    uploaded_file = request.files.get("csv_file")

    if not uploaded_file or not uploaded_file.filename:
        return go_back(import_error="Please select a CSV file.")

    if not uploaded_file.filename.lower().endswith(".csv"):
        return go_back(import_error="Only CSV files are supported.")

    # Limit uploaded files to 2 MB.
    content = uploaded_file.read(2 * 1024 * 1024 + 1)

    if len(content) > 2 * 1024 * 1024:
        return go_back(import_error="CSV file must be under 2 MB.")

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return go_back(import_error="CSV file must use UTF-8 encoding.")

    try:
        reader = csv.DictReader(io.StringIO(text, newline=""))

        if not reader.fieldnames:
            return go_back(import_error="CSV file is empty.")

        columns = [column.strip().lower()
                   for column in reader.fieldnames]

        if len(columns) != len(set(columns)):
            return go_back(import_error="Duplicate CSV column names.")

        required = {"name", "gender", "rating"}

        if not required.issubset(columns):
            return go_back(
                import_error="CSV must contain Name, Gender and Rating columns."
            )

        # Store existing names to detect duplicates.
        existing_players = db.session.scalars(
            select(Player).where(Player.club_id == actor.club_id)
        ).all()

        known_names = [
            player.normalized_name for player in existing_players
        ]

        imported = 0
        skipped = 0
        new_players = []

        for row_number, original_row in enumerate(reader, start=2):
            if row_number > 1001:
                return go_back(
                    import_error="CSV supports a maximum of 1000 players."
                )

            if None in original_row:
                skipped += 1
                continue

            row = {
                key.strip().lower(): value.strip()
                for key, value in original_row.items()
            }

            try:
                name = clean_text(row.get("name"), "name")

                normalized = normalize_name(name)

                if not normalized:
                    skipped += 1
                    continue

                # Same duplicate rule as regular player creation.
                duplicate = any(
                    normalized == known
                    or SequenceMatcher(None, normalized, known).ratio() >= 0.92
                    for known in known_names
                )

                if duplicate:
                    skipped += 1
                    continue

                gender_input = clean_text(
                    row.get("gender"), "gender", 20
                ).upper()

                gender_mapping = {
                    "MALE": "M",
                    "FEMALE": "F",
                    "M": "M",
                    "F": "F",
                }

                gender = gender_mapping.get(gender_input)

                if gender is None:
                    skipped += 1
                    continue

                rating = positive_int(
                    int(row.get("rating", "")), "rating", 10
                )

                player = Player(
                    club_id=actor.club_id,
                    name=name,
                    normalized_name=normalized,
                    gender=gender,
                    skill=rating,
                )

                player_details(player, {
                    "notes": row.get("notes") or None,
                    "court_preference": row.get("court preference") or None,
                })

                new_players.append(player)
                known_names.append(normalized)
                imported += 1

            except (ApiError, ValueError, TypeError, AttributeError):
                skipped += 1
                continue

    except csv.Error:
        return go_back(import_error="Could not parse the CSV file.")

    if new_players:
        db.session.add_all(new_players)
        try:
            commit_or_conflict("Could not import the players.")
        except ApiError:
            return go_back(
                import_error="Database error while importing players."
            )

    return go_back(imported=imported, skipped=skipped)

@api.get("/players/<player_id>")
@require_authenticated
def get_player(player_id):
    return jsonify({"player": serialize_player(player_for_club(player_id))})


@api.patch("/players/<player_id>")
@require_authenticated
def update_player(player_id):
    actor = current_actor()
    player = player_for_club(player_id)
    data = body()
    if "skill" in data and not can_manage_skill(actor):
        raise ApiError(
            "You don't have permission to set player skill.", 403, "forbidden"
        )
    warnings = []
    if "name" in data:
        name = clean_text(data["name"], "name")
        warnings = duplicate_warnings(actor.club_id, name, player.id)
        if warnings and data.get("confirm_duplicate") is not True:
            raise ApiError(
                "A player with the same or a similar name already exists. Confirm to continue.",
                409,
                "duplicate_confirmation_required",
                warnings,
            )
        player.name = name
        player.normalized_name = normalize_name(name)
    if "gender" in data:
        gender = clean_text(data["gender"], "gender", 20).upper()
        if gender not in {"M", "F"}:
            raise ApiError("gender must be M or F.")
        player.gender = gender
    if "skill" in data:
        player.skill = (
            None if data["skill"] is None else positive_int(data["skill"], "skill", 10)
        )
    if "active" in data:
        if not can_delete_player(actor):
            raise ApiError(
                "You do not have permission to activate or deactivate players.",
                403,
                "forbidden",
            )
        player.active = parse_bool(data["active"], "active")
    player_details(player, data)
    db.session.commit()
    return jsonify({"player": serialize_player(player), "duplicate_warnings": warnings})


@api.delete("/players/<player_id>")
@require_roles(Role.OWNER, Role.ADMIN)
def deactivate_player(player_id):
    player = player_for_club(player_id)
    player.active = False
    db.session.commit()
    return "", 204


@api.get("/courts")
@require_authenticated
def list_courts():
    actor = current_actor()
    courts = db.session.scalars(
        select(Court)
        .where(Court.club_id == actor.club_id)
        .order_by(Court.priority, Court.number)
    ).all()
    return jsonify({"courts": [serialize_court(court) for court in courts]})


@api.post("/courts")
@require_roles(*MANAGERS)
def create_court():
    actor = current_actor()
    data = body()
    court = Court(
        club_id=actor.club_id,
        number=positive_int(data.get("number"), "number"),
        court_type=clean_text(data.get("court_type"), "court_type", 80),
        active=parse_bool(data.get("active", True), "active"),
        priority=nonnegative_int(data.get("priority", 0), "priority"),
        cover=optional_text(data.get("cover"), "cover"),
    )
    db.session.add(court)
    commit_or_conflict("That court number already exists in this club.")
    return jsonify({"court": serialize_court(court)}), 201


@api.patch("/courts/<court_id>")
@require_roles(*MANAGERS)
def update_court(court_id):
    court = court_for_club(court_id)
    data = body()
    if "number" in data:
        court.number = positive_int(data["number"], "number")
    if "court_type" in data:
        court.court_type = clean_text(data["court_type"], "court_type", 80)
    if "active" in data:
        court.active = parse_bool(data["active"], "active")
    if "priority" in data:
        court.priority = nonnegative_int(data["priority"], "priority")
    if "cover" in data:
        court.cover = optional_text(data["cover"], "cover")
    commit_or_conflict("That court number already exists in this club.")
    return jsonify({"court": serialize_court(court)})


@api.delete("/courts/<court_id>")
@require_roles(*MANAGERS)
def deactivate_court(court_id):
    court = court_for_club(court_id)
    court.active = False
    db.session.commit()
    return "", 204


def apply_template_fields(target, data, partial=False):
    fields = {
        "number_of_rounds": lambda value: positive_int(value, "number_of_rounds", 50),
        "session_duration_minutes": lambda value: positive_int(
            value, "session_duration_minutes", 1440
        ),
        "round_duration_minutes": lambda value: positive_int(
            value, "round_duration_minutes", 240
        ),
        "strict_format": lambda value: parse_bool(value, "strict_format"),
        "active": lambda value: parse_bool(value, "active"),
    }
    for field, validator in fields.items():
        if field in data:
            setattr(target, field, validator(data[field]))
        elif not partial and not hasattr(target, field):
            raise ApiError(f"{field} is required.")
    if "round_format" in data:
        value = clean_text(data["round_format"], "round_format", 20).lower()
        if value not in {"open", "mixed"}:
            raise ApiError("round_format must be open or mixed.")
        target.round_format = value
    if "start_time" in data:
        try:
            target.start_time = time.fromisoformat(data["start_time"])
        except (TypeError, ValueError) as error:
            raise ApiError("start_time must be an ISO time.") from error
    if "tea_break_after_round" in data:
        value = data["tea_break_after_round"]
        target.tea_break_after_round = (
            None if value is None else positive_int(value, "tea_break_after_round")
        )


def validate_break(target):
    if (
        target.tea_break_after_round is not None
        and target.tea_break_after_round >= target.number_of_rounds
    ):
        raise ApiError("tea_break_after_round must be before the last round.")


def replace_template_courts(template, court_ids):
    if not isinstance(court_ids, list) or len(court_ids) != len(set(court_ids)):
        raise ApiError("court_ids must be a list of unique IDs.")
    courts = [court_for_club(court_id) for court_id in court_ids]
    if any(not court.active for court in courts):
        raise ApiError("Only active courts can be saved in a template.")
    existing = {link.court_id: link for link in template.courts}
    for link in list(template.courts):
        if link.court_id not in court_ids:
            template.courts.remove(link)
    for index, court in enumerate(
        sorted(courts, key=lambda item: (item.priority, item.number))
    ):
        link = existing.get(court.id)
        if link is None:
            link = SessionTemplateCourt(court=court)
            template.courts.append(link)
        link.priority_order = index


@api.get("/session-templates")
@require_authenticated
def list_templates():
    actor = current_actor()
    templates = db.session.scalars(
        select(SessionTemplate)
        .where(SessionTemplate.club_id == actor.club_id)
        .order_by(SessionTemplate.name)
    ).all()
    return jsonify(
        {"session_templates": [serialize_template(item) for item in templates]}
    )


@api.post("/session-templates")
@require_roles(*MANAGERS)
def create_template():
    actor = current_actor()
    data = body()
    template = SessionTemplate(
        club_id=actor.club_id,
        name=clean_text(data.get("name"), "name"),
        number_of_rounds=6,
        session_duration_minutes=180,
        round_duration_minutes=30,
        start_time=time(13, 0),
        round_format="open",
    )
    apply_template_fields(template, data, partial=True)
    validate_break(template)
    if "court_ids" in data:
        replace_template_courts(template, data["court_ids"])
    db.session.add(template)
    commit_or_conflict("A template with that name already exists in this club.")
    return jsonify({"session_template": serialize_template(template)}), 201


@api.patch("/session-templates/<template_id>")
@require_roles(*MANAGERS)
def update_template(template_id):
    template = template_for_club(template_id)
    data = body()
    if "name" in data:
        template.name = clean_text(data["name"], "name")
    apply_template_fields(template, data, partial=True)
    validate_break(template)
    if "court_ids" in data:
        replace_template_courts(template, data["court_ids"])
    commit_or_conflict("A template with that name already exists in this club.")
    return jsonify({"session_template": serialize_template(template)})


@api.delete("/session-templates/<template_id>")
@require_roles(*MANAGERS)
def deactivate_template(template_id):
    template = template_for_club(template_id)
    template.active = False
    db.session.commit()
    return "", 204


@api.get("/sessions")
@require_authenticated
def list_sessions():
    actor = current_actor()
    sessions = db.session.scalars(
        select(TennisSession)
        .where(TennisSession.club_id == actor.club_id)
        .order_by(TennisSession.starts_at.desc())
    ).all()
    return jsonify(
        {
            "sessions": [
                serialize_session(item, include_rounds=False) for item in sessions
            ]
        }
    )


@api.post("/sessions")
@require_authenticated
def create_session():
    actor = current_actor()
    data = body()
    template = (
        template_for_club(data["template_id"]) if data.get("template_id") else None
    )
    if template is not None and not template.active:
        raise ApiError("An inactive template cannot be used for a new session.")
    court_ids = data.get(
        "court_ids",
        [link.court_id for link in template.courts] if template is not None else None,
    )
    player_ids = data.get("player_ids")
    if (
        not isinstance(court_ids, list)
        or not court_ids
        or len(court_ids) != len(set(court_ids))
    ):
        raise ApiError("court_ids must be a non-empty list of unique IDs.")
    if (
        not isinstance(player_ids, list)
        or len(player_ids) < 4
        or len(player_ids) != len(set(player_ids))
    ):
        raise ApiError("player_ids must contain at least four unique player IDs.")

    courts = [court_for_club(court_id) for court_id in court_ids]
    if any(not court.active for court in courts):
        raise ApiError("Only active courts can be selected.")
    players = [player_for_club(player_id) for player_id in player_ids]
    if any(not player.active for player in players):
        raise ApiError("Only active players can join a new session.")

    try:
        starts_at = datetime.fromisoformat(data["starts_at"])
    except (KeyError, TypeError, ValueError) as error:
        raise ApiError("starts_at must be an ISO date and time.") from error
    source = template
    number_of_rounds = data.get(
        "number_of_rounds", source.number_of_rounds if source else None
    )
    duration = data.get(
        "round_duration_minutes", source.round_duration_minutes if source else None
    )
    session_duration = data.get(
        "session_duration_minutes",
        source.session_duration_minutes if source else None,
    )
    round_format = data.get("round_format", source.round_format if source else "open")
    strict_format = data.get("strict_format", source.strict_format if source else False)
    tea_break = data.get(
        "tea_break_after_round", source.tea_break_after_round if source else None
    )
    number_of_rounds = positive_int(number_of_rounds, "number_of_rounds", 50)
    duration = positive_int(duration, "round_duration_minutes", 240)
    if session_duration is None:
        session_duration = number_of_rounds * duration
    session_duration = positive_int(session_duration, "session_duration_minutes", 1440)
    if round_format not in {"open", "mixed"}:
        raise ApiError("round_format must be open or mixed.")
    strict_format = parse_bool(strict_format, "strict_format")
    if tea_break is not None:
        tea_break = positive_int(tea_break, "tea_break_after_round")
        if tea_break >= number_of_rounds:
            raise ApiError("tea_break_after_round must be before the last round.")

    session = TennisSession(
        club_id=actor.club_id,
        template=template,
        name=clean_text(data.get("name"), "name"),
        starts_at=starts_at,
        number_of_rounds=number_of_rounds,
        session_duration_minutes=session_duration,
        round_duration_minutes=duration,
        round_format=round_format,
        strict_format=strict_format,
        tea_break_after_round=tea_break,
        created_by=actor.actor_id,
    )
    ordered_courts = sorted(courts, key=lambda court: (court.priority, court.number))
    for index, court in enumerate(ordered_courts):
        session.courts.append(SessionCourt(court=court, priority_order=index))
    for player in players:
        session.participants.append(SessionParticipation(player=player, joined_round=1))
    db.session.add(session)
    db.session.commit()
    return jsonify({"session": serialize_session(session)}), 201


@api.get("/sessions/<session_id>")
@require_authenticated
def get_session(session_id):
    return jsonify({"session": serialize_session(session_for_club(session_id))})


@api.patch("/sessions/<session_id>")
@require_authenticated
def update_session(session_id):
    session = session_for_club(session_id)
    if session.status != "draft" or session.rounds:
        raise ApiError(
            "Session configuration is locked after round generation begins.",
            409,
            "session_configuration_locked",
        )
    data = body()
    if "name" in data:
        session.name = clean_text(data["name"], "name")
    if "starts_at" in data:
        try:
            session.starts_at = datetime.fromisoformat(data["starts_at"])
        except (TypeError, ValueError) as error:
            raise ApiError("starts_at must be an ISO date and time.") from error
    if "number_of_rounds" in data:
        session.number_of_rounds = positive_int(
            data["number_of_rounds"], "number_of_rounds", 50
        )
    if "session_duration_minutes" in data:
        session.session_duration_minutes = positive_int(
            data["session_duration_minutes"], "session_duration_minutes", 1440
        )
    if "round_duration_minutes" in data:
        session.round_duration_minutes = positive_int(
            data["round_duration_minutes"], "round_duration_minutes", 240
        )
    if "round_format" in data:
        round_format = clean_text(data["round_format"], "round_format", 20).lower()
        if round_format not in {"open", "mixed"}:
            raise ApiError("round_format must be open or mixed.")
        session.round_format = round_format
    if "strict_format" in data:
        session.strict_format = parse_bool(data["strict_format"], "strict_format")
    if "tea_break_after_round" in data:
        value = data["tea_break_after_round"]
        session.tea_break_after_round = (
            None if value is None else positive_int(value, "tea_break_after_round")
        )
    if (
        session.tea_break_after_round is not None
        and session.tea_break_after_round >= session.number_of_rounds
    ):
        raise ApiError("tea_break_after_round must be before the last round.")

    if "court_ids" in data:
        court_ids = data["court_ids"]
        if (
            not isinstance(court_ids, list)
            or not court_ids
            or len(court_ids) != len(set(court_ids))
        ):
            raise ApiError("court_ids must be a non-empty list of unique IDs.")
        courts = [court_for_club(court_id) for court_id in court_ids]
        if any(not court.active for court in courts):
            raise ApiError("Only active courts can be selected.")
        existing = {link.court_id: link for link in session.courts}
        ordered = sorted(courts, key=lambda item: (item.priority, item.number))
        for link in list(session.courts):
            if link.court_id not in court_ids:
                session.courts.remove(link)
        for index, court in enumerate(ordered):
            link = existing.get(court.id)
            if link is None:
                link = SessionCourt(court=court)
                session.courts.append(link)
            link.priority_order = index

    if "player_ids" in data:
        player_ids = data["player_ids"]
        if (
            not isinstance(player_ids, list)
            or len(player_ids) < 4
            or len(player_ids) != len(set(player_ids))
        ):
            raise ApiError("player_ids must contain at least four unique player IDs.")
        players = [player_for_club(player_id) for player_id in player_ids]
        if any(not player.active for player in players):
            raise ApiError("Only active players can join a new session.")
        existing = {item.player_id: item for item in session.participants}
        for participation in list(session.participants):
            if participation.player_id not in player_ids:
                session.participants.remove(participation)
        for player in players:
            if player.id not in existing:
                session.participants.append(
                    SessionParticipation(player=player, joined_round=1)
                )

    db.session.commit()
    return jsonify({"session": serialize_session(session)})


@api.post("/sessions/<session_id>/players")
@require_authenticated
def add_late_player(session_id):
    session = session_for_club(session_id)
    if session.status in {"completed", "cancelled"}:
        raise ApiError("Players cannot join a closed session.", 409, "session_closed")
    data = body()
    player = player_for_club(data.get("player_id"))
    if not player.active:
        raise ApiError("An inactive player cannot join a session.")
    if db.session.get(SessionParticipation, (session.id, player.id)):
        raise ApiError("Player is already in this session.", 409, "already_joined")
    joined_round = data.get("joined_round", next_round_number(session))
    joined_round = positive_int(joined_round, "joined_round")
    if (
        joined_round < next_round_number(session)
        or joined_round > session.number_of_rounds
    ):
        raise ApiError("joined_round must be an upcoming configured round.")
    session.participants.append(
        SessionParticipation(player=player, joined_round=joined_round)
    )
    db.session.commit()
    return jsonify({"session": serialize_session(session)}), 201


@api.patch("/sessions/<session_id>/players/<player_id>")
@require_authenticated
def mark_player_unavailable(session_id, player_id):
    session = session_for_club(session_id)
    participation = db.session.get(SessionParticipation, (session.id, player_id))
    if participation is None:
        raise ApiError("Player is not in this session.", 404, "not_found")
    data = body()
    from_round = data.get("unavailable_from_round", next_round_number(session))
    from_round = positive_int(from_round, "unavailable_from_round")
    if from_round < next_round_number(session) or from_round > session.number_of_rounds:
        raise ApiError("unavailable_from_round must be an upcoming configured round.")
    participation.unavailable_from_round = from_round
    participation.unavailable_reason = clean_text(
        data.get("reason", "Unavailable for remainder of session"), "reason", 240
    )
    db.session.commit()
    return jsonify({"session": serialize_session(session)})


@api.post("/sessions/<session_id>/rounds/generate")
@require_authenticated
def generate_round(session_id):
    session = session_for_club(session_id)
    round_ = generate_next_round(session)
    return jsonify({"round": serialize_round(round_)}), 201


@api.get("/rounds/<round_id>")
@require_authenticated
def get_round(round_id):
    return jsonify({"round": serialize_round(round_for_club(round_id))})


@api.put("/rounds/<round_id>/final")
@require_authenticated
def override_round(round_id):
    actor = current_actor()
    round_ = round_for_club(round_id)
    data = body()
    if (
        "expected_revision" in data
        and data["expected_revision"] != round_.override_revision
    ):
        raise ApiError("This round changed. Refresh before saving.", 409, "stale_round")
    round_ = apply_override(
        round_, data.get("assignments"), data.get("sit_out_player_ids"), actor
    )
    return jsonify({"round": serialize_round(round_)})


@api.post("/rounds/<round_id>/complete")
@require_authenticated
def complete_round(round_id):
    round_ = round_for_club(round_id)
    if round_.status == "completed":
        raise ApiError("Round is already completed.", 409, "round_locked")
    round_.status = "completed"
    round_.completed_at = utc_now()
    if round_.number == round_.session.number_of_rounds:
        round_.session.status = "completed"
    db.session.commit()
    return jsonify({"round": serialize_round(round_)})


@api.post("/rounds/<round_id>/reopen")
@require_roles(*MANAGERS)
def reopen_round(round_id):
    round_ = round_for_club(round_id)
    if round_.status != "completed":
        raise ApiError(
            "Only completed rounds can be reopened.", 409, "round_not_completed"
        )
    later_round = db.session.scalar(
        select(Round).where(
            Round.session_id == round_.session_id,
            Round.number > round_.number,
        )
    )
    if later_round is not None:
        raise ApiError(
            "A round cannot be reopened after a later round has been generated.",
            409,
            "later_round_exists",
        )
    round_.status = "generated"
    round_.completed_at = None
    round_.reopened_at = utc_now()
    round_.session.status = "active"
    db.session.commit()
    return jsonify({"round": serialize_round(round_)})
