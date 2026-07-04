"""add created and updated timestamps

Revision ID: 87abbf3efc3a
Revises: 8dfa8ce6e866
Create Date: 2026-07-04 12:36:55.897565

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '87abbf3efc3a'
down_revision = '8dfa8ce6e866'
branch_labels = None
depends_on = None


# On SQLite, batch_alter_table rebuilds the table (copy + rename), which drops
# any triggers attached to it. The song_fts sync triggers must be recreated
# after every batch operation, in both directions.
_FTS_TRIGGERS = [
    """
    CREATE TRIGGER song_ai AFTER INSERT ON song BEGIN
        INSERT INTO song_fts(rowid, search_blob) VALUES (new.id, new.search_blob);
    END
    """,
    """
    CREATE TRIGGER song_ad AFTER DELETE ON song BEGIN
        INSERT INTO song_fts(song_fts, rowid, search_blob)
        VALUES ('delete', old.id, old.search_blob);
    END
    """,
    """
    CREATE TRIGGER song_au AFTER UPDATE ON song BEGIN
        INSERT INTO song_fts(song_fts, rowid, search_blob)
        VALUES ('delete', old.id, old.search_blob);
        INSERT INTO song_fts(rowid, search_blob) VALUES (new.id, new.search_blob);
    END
    """,
]


def _recreate_fts_triggers():
    for name in ("song_ai", "song_ad", "song_au"):
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    for ddl in _FTS_TRIGGERS:
        op.execute(ddl)


def upgrade():
    with op.batch_alter_table('song', schema=None) as batch_op:
        batch_op.add_column(sa.Column('created', sa.DateTime(), server_default=sa.text("'2026-01-01 00:00:00'"), nullable=False))
        batch_op.add_column(sa.Column('updated', sa.DateTime(), server_default=sa.text("'2026-01-01 00:00:00'"), nullable=False))

    _recreate_fts_triggers()


def downgrade():
    with op.batch_alter_table('song', schema=None) as batch_op:
        batch_op.drop_column('updated')
        batch_op.drop_column('created')

    _recreate_fts_triggers()
