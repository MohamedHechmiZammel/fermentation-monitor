"""
Run once to create the first admin user.
Usage:
    python3 seed_admin.py
    python3 seed_admin.py --username admin --password yourpassword
"""

import argparse
import getpass
import os
import sqlite3
import time

from auth import hash_password
from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "data/fermentation.db")


def main():
    parser = argparse.ArgumentParser(description="Seed the first admin user")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="", help="Leave blank to prompt securely")
    args = parser.parse_args()

    password = args.password or getpass.getpass(f"Password for '{args.username}': ")
    if len(password) < 8:
        print("Password must be at least 8 characters.")
        return

    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL CHECK(role IN ('viewer','operator','admin')),
            is_active     INTEGER NOT NULL DEFAULT 1,
            created_at    INTEGER NOT NULL
        )
    """)
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, role, is_active, created_at) VALUES (?,?,?,1,?)",
            (args.username, hash_password(password), "admin", int(time.time())),
        )
        conn.commit()
        print(f"Admin user '{args.username}' created.")
    except sqlite3.IntegrityError:
        print(f"User '{args.username}' already exists.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
