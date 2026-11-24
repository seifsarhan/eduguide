"""
Migrate universities table for Multi-Tenant Branded University Portals:
1. Widen universities.admin_password to VARCHAR(255) and hash all plaintext passwords (MySQL & SQLite)
2. Ensure welcome_tagline column exists for custom Dean welcome message
3. Seed demo instructors for AUC (id=2) and GUC (id=4) so all 3 roles work in multi-university demos
"""
import os
import sys
import sqlite3
import pymysql

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import BASE_DIR
from core.security import hash_password, is_password_hashed


def upgrade_mysql():
    host = os.getenv("DB_HOST")
    if not host or host.lower() == "localhost":
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
        print(f"MySQL unreachable: {e}")
        return

    cur = conn.cursor()
    try:
        cur.execute("ALTER TABLE universities MODIFY COLUMN admin_password VARCHAR(255)")
    except Exception:
        pass

    cur.execute("SHOW COLUMNS FROM universities LIKE 'welcome_tagline'")
    if not cur.fetchone():
        cur.execute("ALTER TABLE universities ADD COLUMN welcome_tagline VARCHAR(255) DEFAULT NULL")

    cur.execute("SELECT id, name, slug, admin_email, admin_password FROM universities")
    unis = cur.fetchall()
    hashed = 0
    for u in unis:
        pw = u.get("admin_password")
        if not pw:
            pw = "123456"
        if not is_password_hashed(pw):
            cur.execute("UPDATE universities SET admin_password=%s WHERE id=%s", (hash_password(pw), u["id"]))
            hashed += 1
    print(f"MySQL universities: hashed {hashed}/{len(unis)} admin passwords.")

    # Seed demo instructors for AUC (2) and GUC (4)
    demo_ins = [
        ("prof_auc", "prof.auc@auc.edu.eg", "Dr. Nadia Mansour (AUC)", "Finance & Strategy", 2),
        ("prof_guc", "prof.guc@guc.edu.eg", "Dr. Tarek El-Shenawy (GUC)", "Management & BI", 4),
    ]
    pw_h = hash_password("123456")
    for uname, email, fname, spec, uid in demo_ins:
        cur.execute("SELECT id FROM admins WHERE email=%s", (email,))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO admins (username, email, password, full_name, role, specialization, university_id) VALUES (%s, %s, %s, %s, 'instructor', %s, %s)",
                (uname, email, pw_h, fname, spec, uid),
            )
            print(f"Seeded demo instructor {email} for university #{uid}")

    conn.close()


def upgrade_sqlite():
    db_path = os.path.join(BASE_DIR, "eduguide1.db")
    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND LOWER(name)='universities'")
    t = cur.fetchone()
    if t:
        tname = t["name"]
        cur.execute(f"PRAGMA table_info(`{tname}`)")
        cols = [r["name"] for r in cur.fetchall()]
        if "welcome_tagline" not in cols:
            cur.execute(f"ALTER TABLE `{tname}` ADD COLUMN welcome_tagline TEXT")
        cur.execute(f"SELECT id, admin_password FROM `{tname}`")
        rows = cur.fetchall()
        cnt = 0
        for r in rows:
            pw = r["admin_password"] or "123456"
            if not is_password_hashed(pw):
                cur.execute(f"UPDATE `{tname}` SET admin_password=? WHERE id=?", (hash_password(pw), r["id"]))
                cnt += 1
        print(f"SQLite universities: hashed {cnt}/{len(rows)} admin passwords.")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    upgrade_mysql()
    upgrade_sqlite()
    print("Multi-tenant university portal migration complete!")
