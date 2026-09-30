"""master.key rotation tool (P6-05): re-encrypts every EncryptedSecret column, atomically, and never
leaves the database half-rotated."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys

import pytest

from app.core.cipher import SecretCipher

OLD = b"\x11" * 32
NEW = b"\x22" * 32


def _make_db(path, cipher: SecretCipher):
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE clusters (id INTEGER PRIMARY KEY, name TEXT, api_token_secret BLOB);
        CREATE TABLE team_cluster_tokens (id INTEGER PRIMARY KEY, team_id INT, token_secret BLOB);
        CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT);
        """
    )
    con.execute("INSERT INTO clusters VALUES (1,'a',?)", (cipher.encrypt("secret-1"),))
    con.execute("INSERT INTO clusters VALUES (2,'b',?)", (cipher.encrypt("secret-2"),))
    con.execute("INSERT INTO team_cluster_tokens VALUES (1,1,?)", (cipher.encrypt("team-secret"),))
    con.execute("INSERT INTO users VALUES (1,'alice')")
    con.commit()
    con.close()


def _keyfile(tmp_path, name, raw):
    p = tmp_path / name
    p.write_bytes(raw)
    os.chmod(p, 0o400)
    return p


def test_rotation_re_encrypts_every_secret_and_only_the_new_key_reads_them(tmp_path):
    from app.tools.rotate_master_key import rotate

    db = tmp_path / "app.db"
    _make_db(db, SecretCipher(OLD))
    counts = rotate(db, _keyfile(tmp_path, "old", OLD), _keyfile(tmp_path, "new", NEW))
    assert counts == {"clusters.api_token_secret": 2, "team_cluster_tokens.token_secret": 1}
    con = sqlite3.connect(db)
    new = SecretCipher(NEW)
    assert new.decrypt(con.execute("SELECT api_token_secret FROM clusters WHERE id=1").fetchone()[0]) == "secret-1"
    assert new.decrypt(con.execute("SELECT token_secret FROM team_cluster_tokens").fetchone()[0]) == "team-secret"
    with pytest.raises(Exception):  # noqa: B017 — the old key no longer works
        SecretCipher(OLD).decrypt(con.execute("SELECT api_token_secret FROM clusters WHERE id=1").fetchone()[0])
    assert con.execute("SELECT username FROM users").fetchone()[0] == "alice"  # other tables untouched


def test_a_wrong_old_key_changes_nothing(tmp_path):
    from app.tools.rotate_master_key import RotationError, rotate

    db = tmp_path / "app.db"
    _make_db(db, SecretCipher(OLD))
    before = sqlite3.connect(db).execute("SELECT api_token_secret FROM clusters WHERE id=1").fetchone()[0]
    with pytest.raises(RotationError):
        rotate(db, _keyfile(tmp_path, "wrong", b"\x33" * 32), _keyfile(tmp_path, "new", NEW))
    assert sqlite3.connect(db).execute("SELECT api_token_secret FROM clusters WHERE id=1").fetchone()[0] == before


def test_one_undecryptable_row_aborts_the_whole_rotation(tmp_path):
    from app.tools.rotate_master_key import RotationError, rotate

    db = tmp_path / "app.db"
    _make_db(db, SecretCipher(OLD))
    con = sqlite3.connect(db)
    con.execute("UPDATE team_cluster_tokens SET token_secret = ? WHERE id = 1", (b"garbage",))
    con.commit()
    snapshot = con.execute("SELECT id, api_token_secret FROM clusters ORDER BY id").fetchall()
    con.close()
    with pytest.raises(RotationError):
        rotate(db, _keyfile(tmp_path, "old", OLD), _keyfile(tmp_path, "new", NEW))
    assert sqlite3.connect(db).execute("SELECT id, api_token_secret FROM clusters ORDER BY id").fetchall() == snapshot


@pytest.mark.parametrize("size", [0, 16, 31, 33, 64])
def test_keys_must_be_exactly_32_bytes(tmp_path, size):
    from app.tools.rotate_master_key import RotationError, rotate

    db = tmp_path / "app.db"
    _make_db(db, SecretCipher(OLD))
    with pytest.raises(RotationError):
        rotate(db, _keyfile(tmp_path, "old", OLD), _keyfile(tmp_path, "new", b"x" * size))
    with pytest.raises(RotationError):
        rotate(db, _keyfile(tmp_path, "old2", b"x" * size), _keyfile(tmp_path, "new2", NEW))


def test_identical_keys_are_refused(tmp_path):
    from app.tools.rotate_master_key import RotationError, rotate

    db = tmp_path / "app.db"
    _make_db(db, SecretCipher(OLD))
    with pytest.raises(RotationError):
        rotate(db, _keyfile(tmp_path, "old", OLD), _keyfile(tmp_path, "same", OLD))


def test_the_columns_come_from_the_models_not_from_a_hardcoded_list():
    from app.tools.rotate_master_key import encrypted_columns

    assert ("clusters", "api_token_secret") in encrypted_columns()
    assert ("team_cluster_tokens", "token_secret") in encrypted_columns()


def test_cli_reports_counts_and_fails_with_a_nonzero_exit_on_error(tmp_path):
    db = tmp_path / "app.db"
    _make_db(db, SecretCipher(OLD))
    env = {**os.environ, "PROXMOX_GUI_COOKIE_SECURE": "false"}
    ok = subprocess.run([sys.executable, "-m", "app.tools.rotate_master_key", "--db", str(db),
                         "--old", str(_keyfile(tmp_path, "old", OLD)), "--new", str(_keyfile(tmp_path, "new", NEW))],
                        capture_output=True, text=True, env=env, check=False)
    assert ok.returncode == 0, ok.stderr
    assert "3 secret(s) re-encrypted" in ok.stdout
    assert "secret-1" not in ok.stdout + ok.stderr
    bad = subprocess.run([sys.executable, "-m", "app.tools.rotate_master_key", "--db", str(db),
                          "--old", str(_keyfile(tmp_path, "old2", OLD)), "--new", str(_keyfile(tmp_path, "new2", b"n" * 32))],
                         capture_output=True, text=True, env=env, check=False)
    assert bad.returncode != 0  # OLD no longer decrypts (already rotated)
