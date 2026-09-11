from datetime import datetime, timezone
from flask_login import UserMixin
from app import db, bcrypt


# ============================================================
# USER
# ============================================================

class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    email = db.Column(
        db.String(120),
        unique=True,
        nullable=False,
        index=True
    )

    username = db.Column(
        db.String(50),
        unique=True,
        nullable=False,
        index=True
    )

    password_hash = db.Column(
        db.String(255),
        nullable=False
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    # All clubs that this user belongs to
    memberships = db.relationship(
        "ClubMembership",
        back_populates="user",
        cascade="all, delete-orphan"
    )

    # Invitations created by this user
    sent_invitations = db.relationship(
        "Invitation",
        foreign_keys="Invitation.invited_by_user_id",
        back_populates="invited_by"
    )

    # --------------------------------------------------------
    # Password functions
    # --------------------------------------------------------

    def set_password(self, password):
        self.password_hash = bcrypt.generate_password_hash(
            password
        ).decode("utf-8")

    def check_password(self, password):
        return bcrypt.check_password_hash(
            self.password_hash,
            password
        )

    def __repr__(self):
        return f"<User {self.username}>"


# ============================================================
# CLUB
# ============================================================

class Club(db.Model):
    __tablename__ = "clubs"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    name = db.Column(
        db.String(120),
        nullable=False
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    # Users belonging to this club
    memberships = db.relationship(
        "ClubMembership",
        back_populates="club",
        cascade="all, delete-orphan"
    )

    # Invitations belonging to this club
    invitations = db.relationship(
        "Invitation",
        back_populates="club",
        cascade="all, delete-orphan"
    )

    # Published results belonging to this club
    published_sessions = db.relationship(
        "PublishedSession",
        back_populates="club",
        cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Club {self.name}>"


# ============================================================
# CLUB MEMBERSHIP
#
# Connects a User to a Club.
#
# A user can belong to multiple clubs and can have a different
# role in each club.
#
# Roles:
#   owner
#   admin
#   staff
# ============================================================

class ClubMembership(db.Model):
    __tablename__ = "club_memberships"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False
    )

    club_id = db.Column(
        db.Integer,
        db.ForeignKey("clubs.id"),
        nullable=False
    )

    role = db.Column(
        db.String(20),
        nullable=False
    )

    joined_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    user = db.relationship(
        "User",
        back_populates="memberships"
    )

    club = db.relationship(
        "Club",
        back_populates="memberships"
    )

    # Prevent the same user being added to the same club twice
    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "club_id",
            name="unique_user_club"
        ),

        # Only allow the three valid roles
        db.CheckConstraint(
            "role IN ('owner', 'admin', 'staff')",
            name="valid_club_role"
        ),
    )

    # --------------------------------------------------------
    # Basic role checks
    # --------------------------------------------------------

    def is_owner(self):
        return self.role == "owner"

    def is_admin(self):
        return self.role == "admin"

    def is_staff(self):
        return self.role == "staff"

    # --------------------------------------------------------
    # Player/client database permissions
    # --------------------------------------------------------

    def can_view_players(self):
        # All club members can view players
        return self.role in ["owner", "admin", "staff"]

    def can_add_player(self):
        # Owner, Admin and Staff can add new players
        return self.role in ["owner", "admin", "staff"]

    def can_edit_player(self):
        # All three roles can edit existing player information
        return self.role in ["owner", "admin", "staff"]

    def can_delete_player(self):
        # Staff cannot delete existing player data
        return self.role in ["owner", "admin"]

    # --------------------------------------------------------
    # Staff management permissions
    # --------------------------------------------------------

    def can_invite_staff(self):
        # Owner and Admin can invite Staff
        return self.role in ["owner", "admin"]

    def can_remove_staff(self):
        # Owner and Admin can remove Staff
        return self.role in ["owner", "admin"]

    # --------------------------------------------------------
    # Admin management permissions
    # --------------------------------------------------------

    def can_invite_admin(self):
        # Only Owner can create/invite Admins
        return self.role == "owner"

    def can_remove_admin(self):
        # Only Owner can remove Admins
        return self.role == "owner"

    # --------------------------------------------------------
    # Owner permissions
    # --------------------------------------------------------

    def can_manage_club(self):
        # Only Owner has full club management permission
        return self.role == "owner"

    def can_change_roles(self):
        # Only Owner can promote/demote Admins
        return self.role == "owner"

    # --------------------------------------------------------
    # Result permissions
    # --------------------------------------------------------

    def can_manage_results(self):
        return self.role in ["owner", "admin"]

    def __repr__(self):
        return (
            f"<ClubMembership "
            f"user_id={self.user_id} "
            f"club_id={self.club_id} "
            f"role={self.role}>"
        )


# ============================================================
# INVITATION
#
# Owner:
#   - can invite Admin
#   - can invite Staff
#
# Admin:
#   - can invite Staff
#
# Staff:
#   - cannot invite anyone
#
# The actual permission check will happen in the route.
# ============================================================

class Invitation(db.Model):
    __tablename__ = "invitations"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    club_id = db.Column(
        db.Integer,
        db.ForeignKey("clubs.id"),
        nullable=False
    )

    invited_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False
    )

    email = db.Column(
        db.String(120),
        nullable=False,
        index=True
    )

    # Role the invited user will receive
    role = db.Column(
        db.String(20),
        nullable=False
    )

    # Random secret token used in invitation URL
    token = db.Column(
        db.String(255),
        unique=True,
        nullable=False,
        index=True
    )

    # pending / accepted / revoked
    status = db.Column(
        db.String(20),
        default="pending",
        nullable=False
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    expires_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True
    )

    club = db.relationship(
        "Club",
        back_populates="invitations"
    )

    invited_by = db.relationship(
        "User",
        foreign_keys=[invited_by_user_id],
        back_populates="sent_invitations"
    )

    __table_args__ = (
        db.CheckConstraint(
            "role IN ('admin', 'staff')",
            name="valid_invitation_role"
        ),

        db.CheckConstraint(
            "status IN ('pending', 'accepted', 'revoked')",
            name="valid_invitation_status"
        ),
    )

    def is_pending(self):
        return self.status == "pending"

    def is_expired(self):
        if self.expires_at is None:
            return False

        return datetime.now(timezone.utc) > self.expires_at

    def is_valid(self):
        return self.is_pending() and not self.is_expired()

    def __repr__(self):
        return (
            f"<Invitation "
            f"email={self.email} "
            f"role={self.role}>"
        )


# ============================================================
# PUBLISHED SESSION
#
# Used for protected result / QR access.
#
# Instead of exposing:
#
#     /results/12
#
# the application can expose something like:
#
#     /results/a8f392ab...
#
# using access_token.
# ============================================================

class PublishedSession(db.Model):
    __tablename__ = "published_sessions"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    club_id = db.Column(
        db.Integer,
        db.ForeignKey("clubs.id"),
        nullable=False
    )

    access_token = db.Column(
        db.String(255),
        unique=True,
        nullable=False,
        index=True
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    expires_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True
    )

    is_active = db.Column(
        db.Boolean,
        default=True,
        nullable=False
    )

    club = db.relationship(
        "Club",
        back_populates="published_sessions"
    )

    def is_expired(self):
        if self.expires_at is None:
            return False

        return datetime.now(timezone.utc) > self.expires_at

    def can_access(self):
        return self.is_active and not self.is_expired()

    def __repr__(self):
        return f"<PublishedSession {self.id}>"