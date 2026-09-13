"""Persist file input positions alongside observations."""

from alembic import op
import sqlalchemy as sa

revision = "0002_input_cursors"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("input_cursors"):
        columns = {column["name"]: column for column in inspector.get_columns("input_cursors")}
        expected = {
            "receiver_id": sa.String,
            "source_path": sa.Text,
            "device": sa.Integer,
            "inode": sa.Integer,
            "generation": sa.String,
            "offset": sa.Integer,
            "anchor": sa.Text,
        }
        primary_key = inspector.get_pk_constraint("input_cursors")["constrained_columns"]
        if (
            columns.keys() != expected.keys()
            or set(primary_key) != {"receiver_id", "source_path"}
            or any(columns[name]["nullable"] or not isinstance(columns[name]["type"], kind)
                   for name, kind in expected.items())
        ):
            raise RuntimeError("existing input_cursors table does not match the expected schema")
        return
    op.create_table(
        "input_cursors",
        sa.Column("receiver_id", sa.String(64), sa.ForeignKey("receivers.id"), primary_key=True),
        sa.Column("source_path", sa.Text(), primary_key=True),
        sa.Column("device", sa.BigInteger(), nullable=False),
        sa.Column("inode", sa.BigInteger(), nullable=False),
        sa.Column("generation", sa.String(32), nullable=False),
        sa.Column("offset", sa.BigInteger(), nullable=False),
        sa.Column("anchor", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("input_cursors")
