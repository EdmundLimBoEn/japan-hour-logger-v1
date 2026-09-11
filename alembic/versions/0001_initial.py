"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "receivers",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("locator", sa.String(8), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("receiver_id", sa.String(64), sa.ForeignKey("receivers.id"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("instance_id", sa.String(128), nullable=True),
        sa.Column("dial_frequency_hz", sa.Integer, nullable=True),
        sa.Column("band", sa.String(16), nullable=True),
        sa.Column("mode", sa.String(16), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
    )
    op.create_table(
        "stations",
        sa.Column("callsign", sa.String(32), primary_key=True),
        sa.Column("last_grid", sa.String(8), nullable=True),
        sa.Column("grid_source", sa.String(16), nullable=False, server_default="none"),
        sa.Column("country", sa.String(64), nullable=True),
        sa.Column("dxcc", sa.String(16), nullable=True),
        sa.Column("continent", sa.String(8), nullable=True),
        sa.Column("cqz", sa.Integer, nullable=True),
        sa.Column("ituz", sa.Integer, nullable=True),
        sa.Column("first_heard_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heard_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decode_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_snr_db", sa.Float, nullable=True),
        sa.Column("last_distance_km", sa.Float, nullable=True),
    )
    op.create_table(
        "observations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("receiver_id", sa.String(64), sa.ForeignKey("receivers.id"), nullable=False),
        sa.Column("session_id", sa.Integer, sa.ForeignKey("sessions.id"), nullable=True),
        sa.Column("timestamp_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timestamp_local", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dial_frequency_hz", sa.Integer, nullable=True),
        sa.Column("audio_frequency_hz", sa.Integer, nullable=True),
        sa.Column("signal_frequency_hz", sa.Integer, nullable=True),
        sa.Column("band", sa.String(16), nullable=True),
        sa.Column("mode", sa.String(16), nullable=False, server_default="FT8"),
        sa.Column("snr_db", sa.Float, nullable=True),
        sa.Column("dt", sa.Float, nullable=True),
        sa.Column("df", sa.Integer, nullable=True),
        sa.Column("raw_message", sa.Text, nullable=False),
        sa.Column("message_type", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("is_cq", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("tx_callsign", sa.String(32), nullable=True),
        sa.Column("rx_callsign", sa.String(32), nullable=True),
        sa.Column("tx_grid", sa.String(8), nullable=True),
        sa.Column("grid_source", sa.String(16), nullable=False, server_default="none"),
        sa.Column("country", sa.String(64), nullable=True),
        sa.Column("dxcc", sa.String(16), nullable=True),
        sa.Column("continent", sa.String(8), nullable=True),
        sa.Column("cqz", sa.Integer, nullable=True),
        sa.Column("ituz", sa.Integer, nullable=True),
        sa.Column("distance_km", sa.Float, nullable=True),
        sa.Column("bearing_deg", sa.Float, nullable=True),
        sa.Column("low_confidence", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("off_air", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(16), nullable=False, server_default="udp"),
        sa.Column("instance_id", sa.String(128), nullable=True),
        sa.Column("fingerprint", sa.String(256), nullable=False),
        sa.Column("raw_payload", sa.Text, nullable=True),
        sa.Column("is_japan", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "app_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("timestamp_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("level", sa.String(16), nullable=False, server_default="info"),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("payload", sa.Text, nullable=True),
        sa.Column("receiver_id", sa.String(64), nullable=True),
    )
    op.create_index("ix_observations_timestamp_utc", "observations", ["timestamp_utc"])
    op.create_index("ix_observations_receiver_id", "observations", ["receiver_id"])
    op.create_index("ix_observations_tx_callsign", "observations", ["tx_callsign"])
    op.create_index("ix_observations_country", "observations", ["country"])
    op.create_index("ix_observations_dxcc", "observations", ["dxcc"])
    op.create_index("ix_observations_is_japan", "observations", ["is_japan"])
    op.create_index("ix_observations_fingerprint", "observations", ["fingerprint"])
    op.create_index("ix_sessions_receiver_id", "sessions", ["receiver_id"])
    op.create_index("ix_stations_last_heard_utc", "stations", ["last_heard_utc"])
    op.create_index("ix_app_events_timestamp_utc", "app_events", ["timestamp_utc"])
    op.create_index("ix_app_events_event_type", "app_events", ["event_type"])


def downgrade() -> None:
    op.drop_table("app_events")
    op.drop_table("observations")
    op.drop_table("stations")
    op.drop_table("sessions")
    op.drop_table("receivers")
