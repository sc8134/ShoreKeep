"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-23

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel.sql.sqltypes  # noqa: F401
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "billingevent",
        sa.Column("event_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("event_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("received_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(op.f("ix_billingevent_event_type"), "billingevent", ["event_type"], unique=False)
    op.create_index(op.f("ix_billingevent_received_at"), "billingevent", ["received_at"], unique=False)
    op.create_index(op.f("ix_billingevent_user_id"), "billingevent", ["user_id"], unique=False)
    op.create_table(
        "user",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("email", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("password_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("plan", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_created_at"), "user", ["created_at"], unique=False)
    op.create_index(op.f("ix_user_email"), "user", ["email"], unique=True)
    op.create_table(
        "agentdevice",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("token_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("label", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("last_seen_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agentdevice_token_hash"), "agentdevice", ["token_hash"], unique=True)
    op.create_index(op.f("ix_agentdevice_user_id"), "agentdevice", ["user_id"], unique=False)
    op.create_table(
        "agentpair",
        sa.Column("token_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("expires_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index(op.f("ix_agentpair_expires_at"), "agentpair", ["expires_at"], unique=False)
    op.create_index(op.f("ix_agentpair_user_id"), "agentpair", ["user_id"], unique=False)
    op.create_table(
        "authsession",
        sa.Column("token_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("expires_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("ip_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("user_agent", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index(op.f("ix_authsession_expires_at"), "authsession", ["expires_at"], unique=False)
    op.create_index(op.f("ix_authsession_user_id"), "authsession", ["user_id"], unique=False)
    op.create_table(
        "job",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("url", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("preset", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("client_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("cookies_enc", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("want_subtitles", sa.Boolean(), nullable=False),
        sa.Column("max_filesize_mb", sa.Integer(), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("stage", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("downloaded_bytes", sa.Integer(), nullable=False),
        sa.Column("total_bytes", sa.Integer(), nullable=True),
        sa.Column("speed_bps", sa.Float(), nullable=False),
        sa.Column("eta_seconds", sa.Float(), nullable=True),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("duration", sa.Float(), nullable=True),
        sa.Column("thumbnail", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("engine", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("output_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("output_path", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("output_size", sa.Integer(), nullable=True),
        sa.Column("mime", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("subtitle_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("subtitle_path", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("subtitle_lang", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("chapters", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("playlist_index", sa.Integer(), nullable=True),
        sa.Column("playlist_count", sa.Integer(), nullable=True),
        sa.Column("error", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("error_code", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("worker_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("started_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.Column("finished_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_job_client_hash"), "job", ["client_hash"], unique=False)
    op.create_index(op.f("ix_job_created_at"), "job", ["created_at"], unique=False)
    op.create_index(op.f("ix_job_user_id"), "job", ["user_id"], unique=False)
    op.create_table(
        "subscription",
        sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("provider", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("customer_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("subscription_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("price_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("interval", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("current_period_end", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index(op.f("ix_subscription_customer_id"), "subscription", ["customer_id"], unique=False)
    op.create_index(
        op.f("ix_subscription_subscription_id"), "subscription", ["subscription_id"], unique=True
    )
    op.create_table(
        "desktopjob",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("job_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("device_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("preset", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("host", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("error", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("finished_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["agentdevice.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_desktopjob_created_at"), "desktopjob", ["created_at"], unique=False)
    op.create_index(op.f("ix_desktopjob_device_id"), "desktopjob", ["device_id"], unique=False)
    op.create_index(op.f("ix_desktopjob_job_id"), "desktopjob", ["job_id"], unique=False)
    op.create_index(op.f("ix_desktopjob_user_id"), "desktopjob", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_desktopjob_user_id"), table_name="desktopjob")
    op.drop_index(op.f("ix_desktopjob_job_id"), table_name="desktopjob")
    op.drop_index(op.f("ix_desktopjob_device_id"), table_name="desktopjob")
    op.drop_index(op.f("ix_desktopjob_created_at"), table_name="desktopjob")
    op.drop_table("desktopjob")
    op.drop_index(op.f("ix_subscription_subscription_id"), table_name="subscription")
    op.drop_index(op.f("ix_subscription_customer_id"), table_name="subscription")
    op.drop_table("subscription")
    op.drop_index(op.f("ix_job_user_id"), table_name="job")
    op.drop_index(op.f("ix_job_created_at"), table_name="job")
    op.drop_index(op.f("ix_job_client_hash"), table_name="job")
    op.drop_table("job")
    op.drop_index(op.f("ix_authsession_user_id"), table_name="authsession")
    op.drop_index(op.f("ix_authsession_expires_at"), table_name="authsession")
    op.drop_table("authsession")
    op.drop_index(op.f("ix_agentpair_user_id"), table_name="agentpair")
    op.drop_index(op.f("ix_agentpair_expires_at"), table_name="agentpair")
    op.drop_table("agentpair")
    op.drop_index(op.f("ix_agentdevice_user_id"), table_name="agentdevice")
    op.drop_index(op.f("ix_agentdevice_token_hash"), table_name="agentdevice")
    op.drop_table("agentdevice")
    op.drop_index(op.f("ix_user_email"), table_name="user")
    op.drop_index(op.f("ix_user_created_at"), table_name="user")
    op.drop_table("user")
    op.drop_index(op.f("ix_billingevent_user_id"), table_name="billingevent")
    op.drop_index(op.f("ix_billingevent_received_at"), table_name="billingevent")
    op.drop_index(op.f("ix_billingevent_event_type"), table_name="billingevent")
    op.drop_table("billingevent")
