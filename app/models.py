import uuid
from datetime import UTC, datetime

from app.extensions import db


def new_id():
    return str(uuid.uuid4())


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


class TimestampMixin:
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )


class Player(TimestampMixin, db.Model):
    __tablename__ = "players"

    id = db.Column(db.String(36), primary_key=True, default=new_id)
    club_id = db.Column(db.String(128), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    normalized_name = db.Column(db.String(120), nullable=False)
    gender = db.Column(db.String(20), nullable=False)
    skill = db.Column(db.Integer, nullable=True)
    active = db.Column(db.Boolean, nullable=False, default=True)

    __table_args__ = (
        db.CheckConstraint("length(trim(name)) > 0", name="ck_player_name_nonempty"),
        db.CheckConstraint(
            "skill IS NULL OR skill BETWEEN 1 AND 10", name="ck_player_skill"
        ),
        db.Index("ix_player_club_normalized_name", "club_id", "normalized_name"),
    )


class Court(TimestampMixin, db.Model):
    __tablename__ = "courts"

    id = db.Column(db.String(36), primary_key=True, default=new_id)
    club_id = db.Column(db.String(128), nullable=False, index=True)
    number = db.Column(db.Integer, nullable=False)
    court_type = db.Column(db.String(80), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    priority = db.Column(db.Integer, nullable=False, default=0)

    __table_args__ = (
        db.UniqueConstraint("club_id", "number", name="uq_court_club_number"),
        db.CheckConstraint("number > 0", name="ck_court_number_positive"),
        db.CheckConstraint("priority >= 0", name="ck_court_priority_nonnegative"),
        db.CheckConstraint(
            "length(trim(court_type)) > 0", name="ck_court_type_nonempty"
        ),
    )


class SessionTemplate(TimestampMixin, db.Model):
    __tablename__ = "session_templates"

    id = db.Column(db.String(36), primary_key=True, default=new_id)
    club_id = db.Column(db.String(128), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    number_of_rounds = db.Column(db.Integer, nullable=False, default=6)
    session_duration_minutes = db.Column(db.Integer, nullable=False, default=180)
    round_duration_minutes = db.Column(db.Integer, nullable=False, default=30)
    start_time = db.Column(db.Time, nullable=False)
    round_format = db.Column(db.String(20), nullable=False, default="open")
    strict_format = db.Column(db.Boolean, nullable=False, default=False)
    tea_break_after_round = db.Column(db.Integer, nullable=True)
    active = db.Column(db.Boolean, nullable=False, default=True)

    courts = db.relationship(
        "SessionTemplateCourt",
        cascade="all, delete-orphan",
        back_populates="template",
    )

    __table_args__ = (
        db.UniqueConstraint("club_id", "name", name="uq_template_club_name"),
        db.CheckConstraint("number_of_rounds > 0", name="ck_template_rounds_positive"),
        db.CheckConstraint(
            "session_duration_minutes > 0", name="ck_template_session_duration_positive"
        ),
        db.CheckConstraint(
            "round_duration_minutes > 0", name="ck_template_duration_positive"
        ),
        db.CheckConstraint(
            "round_format IN ('open', 'mixed')", name="ck_template_round_format"
        ),
        db.CheckConstraint(
            "tea_break_after_round IS NULL OR "
            "(tea_break_after_round > 0 AND tea_break_after_round < number_of_rounds)",
            name="ck_template_tea_break",
        ),
    )


class SessionTemplateCourt(db.Model):
    __tablename__ = "session_template_courts"

    template_id = db.Column(
        db.String(36), db.ForeignKey("session_templates.id"), primary_key=True
    )
    court_id = db.Column(db.String(36), db.ForeignKey("courts.id"), primary_key=True)
    priority_order = db.Column(db.Integer, nullable=False)

    template = db.relationship("SessionTemplate", back_populates="courts")
    court = db.relationship("Court")

    __table_args__ = (
        db.UniqueConstraint(
            "template_id", "priority_order", name="uq_template_court_priority"
        ),
    )


class TennisSession(TimestampMixin, db.Model):
    __tablename__ = "tennis_sessions"

    id = db.Column(db.String(36), primary_key=True, default=new_id)
    club_id = db.Column(db.String(128), nullable=False, index=True)
    template_id = db.Column(
        db.String(36), db.ForeignKey("session_templates.id"), nullable=True
    )
    name = db.Column(db.String(120), nullable=False)
    starts_at = db.Column(db.DateTime, nullable=False)
    number_of_rounds = db.Column(db.Integer, nullable=False)
    session_duration_minutes = db.Column(db.Integer, nullable=False)
    round_duration_minutes = db.Column(db.Integer, nullable=False)
    round_format = db.Column(db.String(20), nullable=False)
    strict_format = db.Column(db.Boolean, nullable=False, default=False)
    tea_break_after_round = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="draft")
    created_by = db.Column(db.String(128), nullable=False)

    template = db.relationship("SessionTemplate")
    courts = db.relationship(
        "SessionCourt", cascade="all, delete-orphan", back_populates="session"
    )
    participants = db.relationship(
        "SessionParticipation", cascade="all, delete-orphan", back_populates="session"
    )
    rounds = db.relationship(
        "Round", cascade="all, delete-orphan", back_populates="session"
    )

    __table_args__ = (
        db.CheckConstraint("number_of_rounds > 0", name="ck_session_rounds_positive"),
        db.CheckConstraint(
            "session_duration_minutes > 0", name="ck_session_total_duration_positive"
        ),
        db.CheckConstraint(
            "round_duration_minutes > 0", name="ck_session_duration_positive"
        ),
        db.CheckConstraint(
            "round_format IN ('open', 'mixed')", name="ck_session_round_format"
        ),
        db.CheckConstraint(
            "status IN ('draft', 'active', 'completed', 'cancelled')",
            name="ck_session_status",
        ),
    )


class SessionCourt(db.Model):
    __tablename__ = "session_courts"

    session_id = db.Column(
        db.String(36), db.ForeignKey("tennis_sessions.id"), primary_key=True
    )
    court_id = db.Column(db.String(36), db.ForeignKey("courts.id"), primary_key=True)
    priority_order = db.Column(db.Integer, nullable=False)

    session = db.relationship("TennisSession", back_populates="courts")
    court = db.relationship("Court")

    __table_args__ = (
        db.UniqueConstraint(
            "session_id", "priority_order", name="uq_session_court_priority"
        ),
    )


class SessionParticipation(db.Model):
    __tablename__ = "session_participations"

    session_id = db.Column(
        db.String(36), db.ForeignKey("tennis_sessions.id"), primary_key=True
    )
    player_id = db.Column(db.String(36), db.ForeignKey("players.id"), primary_key=True)
    joined_round = db.Column(db.Integer, nullable=False, default=1)
    unavailable_from_round = db.Column(db.Integer, nullable=True)
    unavailable_reason = db.Column(db.String(240), nullable=True)
    added_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    session = db.relationship("TennisSession", back_populates="participants")
    player = db.relationship("Player")

    __table_args__ = (
        db.CheckConstraint("joined_round > 0", name="ck_participation_joined_round"),
        db.CheckConstraint(
            "unavailable_from_round IS NULL OR unavailable_from_round > 0",
            name="ck_participation_unavailable_round",
        ),
    )


class Round(TimestampMixin, db.Model):
    __tablename__ = "rounds"

    id = db.Column(db.String(36), primary_key=True, default=new_id)
    session_id = db.Column(
        db.String(36), db.ForeignKey("tennis_sessions.id"), nullable=False, index=True
    )
    number = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="generated")
    generated_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    completed_at = db.Column(db.DateTime, nullable=True)
    reopened_at = db.Column(db.DateTime, nullable=True)
    override_revision = db.Column(db.Integer, nullable=False, default=0)
    overridden_at = db.Column(db.DateTime, nullable=True)
    overridden_by = db.Column(db.String(128), nullable=True)

    session = db.relationship("TennisSession", back_populates="rounds")
    eligibility = db.relationship(
        "RoundEligibility", cascade="all, delete-orphan", back_populates="round"
    )
    outcomes = db.relationship(
        "RoundPlayerOutcome", cascade="all, delete-orphan", back_populates="round"
    )

    __table_args__ = (
        db.UniqueConstraint("session_id", "number", name="uq_round_session_number"),
        db.CheckConstraint("number > 0", name="ck_round_number_positive"),
        db.CheckConstraint(
            "status IN ('generated', 'completed')", name="ck_round_status"
        ),
    )


class RoundEligibility(db.Model):
    __tablename__ = "round_eligibility"

    round_id = db.Column(db.String(36), db.ForeignKey("rounds.id"), primary_key=True)
    player_id = db.Column(db.String(36), db.ForeignKey("players.id"), primary_key=True)
    status = db.Column(db.String(30), nullable=False)
    reason = db.Column(db.String(240), nullable=True)

    round = db.relationship("Round", back_populates="eligibility")
    player = db.relationship("Player")

    __table_args__ = (
        db.CheckConstraint(
            "status IN ('eligible', 'not_arrived', 'unavailable', 'inactive', 'missing_skill')",
            name="ck_round_eligibility_status",
        ),
    )


class RoundPlayerOutcome(db.Model):
    __tablename__ = "round_player_outcomes"

    id = db.Column(db.String(36), primary_key=True, default=new_id)
    round_id = db.Column(db.String(36), db.ForeignKey("rounds.id"), nullable=False)
    player_id = db.Column(db.String(36), db.ForeignKey("players.id"), nullable=False)
    version = db.Column(db.String(20), nullable=False)
    outcome = db.Column(db.String(20), nullable=False)
    court_id = db.Column(db.String(36), db.ForeignKey("courts.id"), nullable=True)
    team = db.Column(db.Integer, nullable=True)
    slot = db.Column(db.Integer, nullable=True)

    round = db.relationship("Round", back_populates="outcomes")
    player = db.relationship("Player")
    court = db.relationship("Court")

    __table_args__ = (
        db.UniqueConstraint(
            "round_id", "player_id", "version", name="uq_round_player_version"
        ),
        db.UniqueConstraint(
            "round_id",
            "version",
            "court_id",
            "team",
            "slot",
            name="uq_round_version_court_slot",
        ),
        db.CheckConstraint(
            "version IN ('generated', 'final')", name="ck_outcome_version"
        ),
        db.CheckConstraint("outcome IN ('playing', 'sit_out')", name="ck_outcome_type"),
        db.CheckConstraint(
            "(outcome = 'sit_out' AND court_id IS NULL AND team IS NULL AND slot IS NULL) "
            "OR (outcome = 'playing' AND court_id IS NOT NULL AND team IN (1, 2) "
            "AND slot IN (1, 2))",
            name="ck_outcome_assignment_shape",
        ),
    )
