"""
Idempotent database migration & security hardening script for EDUGUIDE.
Upgrades both MySQL (if configured & reachable) and local SQLite (eduguide1.db):
  1. Creates audit_logs table
  2. Links faculty / users / admins to universities (multi-university support)
  3. Widens password columns to VARCHAR(255) and hashes all plaintext passwords
  4. Seeds realistic sample items into marketplace_products if empty
"""
import os
import sys
import sqlite3
import pymysql

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import BASE_DIR
from core.security import hash_password, is_password_hashed


def migrate_mysql():
    host = os.getenv("DB_HOST")
    if not host or host.lower() == "localhost":
        print("Skipping MySQL (DB_HOST not set to remote/127.0.0.1).")
        return
    try:
        conn = pymysql.connect(
            host=host,
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASS"),
            database=os.getenv("DB_NAME"),
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
            connect_timeout=5,
        )
    except Exception as e:
        print(f"MySQL unreachable, skipping: {e}", flush=True)
        return

    cur = conn.cursor()
    print("=== Migrating MySQL ===", flush=True)

    # 1. audit_logs
    cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            actor_id VARCHAR(100),
            actor_name VARCHAR(255),
            actor_role VARCHAR(50),
            action VARCHAR(100) NOT NULL,
            target_type VARCHAR(100),
            target_id VARCHAR(100),
            details TEXT,
            ip_address VARCHAR(64),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 2. Multi-university columns
    cur.execute("SHOW COLUMNS FROM faculty LIKE 'university_id'")
    if not cur.fetchone():
        cur.execute("ALTER TABLE faculty ADD COLUMN university_id INT DEFAULT 3")
        print("  Added faculty.university_id")
    cur.execute("UPDATE faculty SET university_id = 3 WHERE university_id IS NULL")
    cur.execute("UPDATE users SET university_id = 3 WHERE university_id IS NULL")
    cur.execute("UPDATE admins SET university_id = 3 WHERE university_id IS NULL")

    # 3. Ensure password columns fit pbkdf2:sha256 hashes (VARCHAR(255)) & hash plaintext
    password_tables = [
        ("system_users", "id"),
        ("admins", "id"),
        ("users", "id"),
        ("facusers", "user_id"),
    ]
    for tbl, pk in password_tables:
        try:
            cur.execute(f"ALTER TABLE `{tbl}` MODIFY COLUMN password VARCHAR(255)")
        except Exception as e:
            print(f"  WARN: Could not widen {tbl}.password: {e}")
        cur.execute(f"SELECT `{pk}` AS pk_val, password FROM `{tbl}`")
        rows = cur.fetchall()
        hashed_count = 0
        for r in rows:
            pw = r["password"]
            if pw and not is_password_hashed(pw):
                cur.execute(f"UPDATE `{tbl}` SET password=%s WHERE `{pk}`=%s", (hash_password(pw), r["pk_val"]))
                hashed_count += 1
        print(f"  Hashed {hashed_count}/{len(rows)} passwords in MySQL.{tbl}")

    # 4. Seed sample marketplace products if empty
    cur.execute("SELECT COUNT(*) as c FROM marketplace_products")
    if cur.fetchone()["c"] == 0:
        samples = [
            ("Financial Accounting Master Summary", "PDF Notes", 0, "Comprehensive lecture notes covering financial statements, balance sheets, and journal entries.", 1, "ACC101"),
            ("Business Statistics & Probability Revision", "Revision Sheets", 50, "Solved exam problems and formula sheets for Midterm and Final prep.", 2, "STA101"),
            ("Corporate Finance Case Studies Pack", "Books", 120, "Real-world Egyptian & international corporate valuation cases.", 3, "FIN201"),
            ("Past 5 Years Final Exams Archive", "Past Exams", 0, "Curated archive of past exams with model answers for Year 1-4.", 1, "ACC101"),
        ]
        for title, cat, price, summary, ins_id, cid in samples:
            cur.execute(
                """
                INSERT INTO marketplace_products (title, category, price, summary, instructor_id, course_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (title, cat, price, summary, ins_id, cid),
            )
        print(f"  Seeded {len(samples)} sample library items into MySQL.marketplace_products")

    conn.commit()
    conn.close()
    print("=== MySQL Migration Complete ===")


def migrate_sqlite():
    db_path = os.path.join(BASE_DIR, "eduguide1.db")
    if not os.path.exists(db_path):
        print("SQLite eduguide1.db not found, skipping.")
        return

    print("=== Migrating SQLite ===")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id TEXT,
            actor_name TEXT,
            actor_role TEXT,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id TEXT,
            details TEXT,
            ip_address TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {r["name"].lower(): r["name"] for r in cur.fetchall()}

    if "faculty" in existing_tables:
        fname = existing_tables["faculty"]
        cur.execute(f"PRAGMA table_info(`{fname}`)")
        cols = [r["name"] for r in cur.fetchall()]
        if "university_id" not in cols:
            cur.execute(f"ALTER TABLE `{fname}` ADD COLUMN university_id INTEGER DEFAULT 3")
        cur.execute(f"UPDATE `{fname}` SET university_id = 3 WHERE university_id IS NULL")

    for tbl_key in ("users", "admins"):
        if tbl_key in existing_tables:
            tname = existing_tables[tbl_key]
            cur.execute(f"PRAGMA table_info(`{tname}`)")
            cols = [r["name"] for r in cur.fetchall()]
            if "university_id" in cols:
                cur.execute(f"UPDATE `{tname}` SET university_id = 3 WHERE university_id IS NULL")

    for tbl_key, pk in [("system_users", "id"), ("admins", "id"), ("users", "id"), ("facusers", "user_id")]:
        if tbl_key not in existing_tables:
            continue
        tname = existing_tables[tbl_key]
        cur.execute(f"SELECT `{pk}` AS pk_val, password FROM `{tname}`")
        rows = cur.fetchall()
        hashed_count = 0
        for r in rows:
            pw = r["password"]
            if pw and not is_password_hashed(pw):
                cur.execute(f"UPDATE `{tname}` SET password=? WHERE `{pk}`=?", (hash_password(pw), r["pk_val"]))
                hashed_count += 1
        print(f"  Hashed {hashed_count}/{len(rows)} passwords in SQLite.{tname}")

    conn.commit()
    conn.close()
    print("=== SQLite Migration Complete ===")


if __name__ == "__main__":
    migrate_mysql()
    migrate_sqlite()
