"""add disc45, disc78 and biography archives

Creates the three new archive tables, each with an FTS5 companion and sync
triggers mirroring song_fts, and rebuilds song.search_blob under NFKD so the
MICRO SIGN folds to Greek mu (see models.fold).

Revision ID: a3f1c9d24e17
Revises: 87abbf3efc3a
Create Date: 2026-08-28 18:30:00.000000

"""
import unicodedata

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'a3f1c9d24e17'
down_revision = '87abbf3efc3a'
branch_labels = None
depends_on = None

_DEFAULT_TIMESTAMP_SQL = "'2026-01-01 00:00:00'"

# Frozen copy of models.Song.SEARCHABLE_FIELDS, in blob order. Duplicated here
# on purpose: a migration must keep behaving the same even if the model changes.
_SONG_SEARCH_FIELDS = (
    "title", "composer", "lyricist", "lyrics", "archive", "bibliography", "notes",
)


def _fold_nfkd(s):
    """Frozen copy of models.fold (NFKD variant)."""
    if not s:
        return ""
    decomposed = unicodedata.normalize("NFKD", s)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.lower()


def _timestamps():
    return (
        sa.Column("created", sa.DateTime(), nullable=False,
                  server_default=sa.text(_DEFAULT_TIMESTAMP_SQL)),
        sa.Column("updated", sa.DateTime(), nullable=False,
                  server_default=sa.text(_DEFAULT_TIMESTAMP_SQL)),
    )


def _create_fts(table, fts, trigger_prefix):
    """Create an external-content FTS5 index over `table`.search_blob + triggers.

    Identical in shape to song_fts: the index covers the pre-folded
    search_blob column and addresses rows by the table's integer primary key.
    """
    op.execute(f"""
        CREATE VIRTUAL TABLE {fts} USING fts5(
            search_blob,
            content='{table}',
            content_rowid='id',
            tokenize='unicode61'
        )
    """)
    op.execute(f"""
        CREATE TRIGGER {trigger_prefix}_ai AFTER INSERT ON {table} BEGIN
            INSERT INTO {fts}(rowid, search_blob) VALUES (new.id, new.search_blob);
        END
    """)
    op.execute(f"""
        CREATE TRIGGER {trigger_prefix}_ad AFTER DELETE ON {table} BEGIN
            INSERT INTO {fts}({fts}, rowid, search_blob)
            VALUES ('delete', old.id, old.search_blob);
        END
    """)
    op.execute(f"""
        CREATE TRIGGER {trigger_prefix}_au AFTER UPDATE ON {table} BEGIN
            INSERT INTO {fts}({fts}, rowid, search_blob)
            VALUES ('delete', old.id, old.search_blob);
            INSERT INTO {fts}(rowid, search_blob) VALUES (new.id, new.search_blob);
        END
    """)


def _drop_fts(fts, trigger_prefix):
    op.execute(f"DROP TRIGGER IF EXISTS {trigger_prefix}_au")
    op.execute(f"DROP TRIGGER IF EXISTS {trigger_prefix}_ad")
    op.execute(f"DROP TRIGGER IF EXISTS {trigger_prefix}_ai")
    op.execute(f"DROP TABLE IF EXISTS {fts}")


def _create_disc_table(name):
    """The shared shape of the two ΜΑΝΙΑΤΗ disc archives.

    `id` is not autoincrement: it carries the source Αναγνωριστικό, which is
    unique but gapped.
    """
    op.create_table(
        name,
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("company", sa.Text(), nullable=False),
        sa.Column("disc_number", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("creators", sa.Text(), nullable=False),
        sa.Column("year", sa.Text(), nullable=False),
        sa.Column("genre", sa.Text(), nullable=False),
        sa.Column("rhythm", sa.Text(), nullable=False),
        sa.Column("search_blob", sa.Text(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table(name, schema=None) as batch_op:
        for col in ("company", "disc_number", "title", "creators", "genre", "rhythm"):
            batch_op.create_index(batch_op.f(f"ix_{name}_{col}"), [col], unique=False)


def _rebuild_song_blobs():
    """Recompute song.search_blob under NFKD, updating only rows that change.

    Before this, U+00B5 MICRO SIGN survived folding, so archive spellings like
    "Ρεµπέτικο" could not be found by a query typed with Greek μ.
    """
    conn = op.get_bind()
    cols = ", ".join(_SONG_SEARCH_FIELDS)
    rows = conn.execute(
        sa.text(f"SELECT id, search_blob, {cols} FROM song")
    ).mappings().all()

    changed = [
        {"id": r["id"], "blob": new}
        for r in rows
        if (new := _fold_nfkd(
            " ".join(r[f] or "" for f in _SONG_SEARCH_FIELDS)
        )) != (r["search_blob"] or "")
    ]
    if changed:
        # The song_au trigger keeps song_fts in step with each update.
        conn.execute(sa.text("UPDATE song SET search_blob = :blob WHERE id = :id"), changed)
    print(f"    rebuilt search_blob for {len(changed)} of {len(rows)} song rows")


def upgrade():
    _create_disc_table("disc45")
    _create_disc_table("disc78")

    op.create_table(
        "biography",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("place_dates", sa.Text(), nullable=False),
        sa.Column("capacity", sa.Text(), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("discography", sa.Text(), nullable=False),
        sa.Column("search_blob", sa.Text(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("biography", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_biography_name"), ["name"], unique=False)
        batch_op.create_index(batch_op.f("ix_biography_capacity"), ["capacity"], unique=False)

    _create_fts("disc45", "disc45_fts", "disc45")
    _create_fts("disc78", "disc78_fts", "disc78")
    _create_fts("biography", "bio_fts", "bio")

    _rebuild_song_blobs()


def downgrade():
    _drop_fts("bio_fts", "bio")
    _drop_fts("disc78_fts", "disc78")
    _drop_fts("disc45_fts", "disc45")

    with op.batch_alter_table("biography", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_biography_capacity"))
        batch_op.drop_index(batch_op.f("ix_biography_name"))
    op.drop_table("biography")

    for name in ("disc78", "disc45"):
        with op.batch_alter_table(name, schema=None) as batch_op:
            for col in ("rhythm", "genre", "creators", "title", "disc_number", "company"):
                batch_op.drop_index(batch_op.f(f"ix_{name}_{col}"))
        op.drop_table(name)

    # song.search_blob is left as rebuilt: reverting to the NFD folding would
    # only re-hide the micro-sign rows from search.
