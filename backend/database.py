
import sqlite3
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit


def database_path() -> Path:
    """Resolve a SQLite DATABASE_URL or use the local project database."""
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        return Path(__file__).resolve().parent / "crystalscout.db"

    parsed = urlsplit(database_url)
    if parsed.scheme.lower() != "sqlite" or parsed.netloc:
        raise RuntimeError(
            "DATABASE_URL must be a SQLite URL; PostgreSQL URLs are not supported by this SQLite backend."
        )
    raw_path = unquote(parsed.path)
    if raw_path.startswith("//"):
        raw_path = raw_path[1:]  # sqlite:////absolute/path.db
    else:
        raw_path = raw_path.lstrip("/")  # sqlite:///relative/path.db
    if not raw_path:
        raise RuntimeError("DATABASE_URL must identify a SQLite database file.")

    path = Path(raw_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    return path


DB_PATH = database_path()


# Connect to the database
def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


# Create the users table
def initialize_database():
    connection = get_connection()
    connection.execute("PRAGMA foreign_keys = ON")

    connection.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS screening_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            source_identifier TEXT,
            formula TEXT NOT NULL,
            predicted_log10 REAL NOT NULL,
            predicted_s_cm REAL NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_screening_history_user_date "
        "ON screening_history(user_id, created_at DESC)"
    )

    connection.commit()
    connection.close()


def save_screening(user_id, source, source_identifier, prediction):
    connection = get_connection()
    try:
        cursor = connection.execute(
            """
            INSERT INTO screening_history
                (user_id, source, source_identifier, formula,
                 predicted_log10, predicted_s_cm)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                source,
                source_identifier,
                prediction["formula"],
                prediction["predicted_log10_conductivity"],
                prediction["predicted_conductivity_s_cm"],
            ),
        )
        connection.commit()
        return cursor.lastrowid
    finally:
        connection.close()


def list_screenings(user_id, limit=100):
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT id, source, source_identifier, formula,
                   predicted_log10, predicted_s_cm, created_at
            FROM screening_history
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()
