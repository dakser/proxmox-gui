"""Rotate the master key: re-encrypt every ``EncryptedSecret`` column with a new key (P6-05).

Run it with the API and worker STOPPED (the wrapper steps are in deploy/README.md):

    python -m app.tools.rotate_master_key --db /var/lib/proxmox-gui/app.db --old OLD.key --new NEW.key

Everything happens in one SQLite transaction: every value is first decrypted with the OLD key (any failure
aborts before a single write), then re-encrypted with the NEW key. The columns are discovered from the ORM
metadata, so a new encrypted column is never forgotten. Plaintexts are never printed.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from app.core.cipher import SecretCipher


class RotationError(Exception):
    """The rotation was refused or aborted; the database was not modified."""


def encrypted_columns() -> list[tuple[str, str]]:
    """(table, column) for every column typed :class:`EncryptedSecret`."""
    import app.models  # noqa: F401 — populate Base.metadata
    from app.models._types import EncryptedSecret
    from app.models.base import Base

    out = []
    for table in Base.metadata.sorted_tables:
        for col in table.columns:
            if isinstance(col.type, EncryptedSecret):
                out.append((table.name, col.name))
    return out


def _load_key(path: Path, label: str) -> SecretCipher:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise RotationError(f"cannot read the {label} key: {exc}") from exc
    if len(raw) != 32:
        raise RotationError(f"the {label} key must be exactly 32 bytes (got {len(raw)})")
    return SecretCipher(raw)


def rotate(db_path: Path, old_key: Path, new_key: Path) -> dict[str, int]:
    old = _load_key(old_key, "old")
    new = _load_key(new_key, "new")
    if Path(old_key).read_bytes() == Path(new_key).read_bytes():
        raise RotationError("the old and new keys are identical")
    con = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        plan: list[tuple[str, str, int, bytes]] = []
        counts: dict[str, int] = {}
        for table, column in encrypted_columns():
            try:
                rows = con.execute(f'SELECT rowid, "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL').fetchall()  # noqa: S608 — identifiers come from the ORM metadata
            except sqlite3.OperationalError:
                continue  # table not present in this database
            for rowid, blob in rows:
                try:
                    plain = old.decrypt(bytes(blob))
                except Exception as exc:  # noqa: BLE001
                    raise RotationError(
                        f"{table}.{column} row {rowid} cannot be decrypted with the old key; nothing was changed"
                    ) from exc
                plan.append((table, column, rowid, new.encrypt(plain)))
            counts[f"{table}.{column}"] = len(rows)
        for table, column, rowid, blob in plan:
            con.execute(f'UPDATE "{table}" SET "{column}" = ? WHERE rowid = ?', (blob, rowid))  # noqa: S608
        con.execute("COMMIT")
        return {k: v for k, v in counts.items() if v}
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--old", required=True, type=Path)
    ap.add_argument("--new", required=True, type=Path)
    args = ap.parse_args(argv)
    try:
        counts = rotate(args.db, args.old, args.new)
    except RotationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"{sum(counts.values())} secret(s) re-encrypted: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
