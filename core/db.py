import os
import re
import time
import sqlite3
import pymysql
from core.config import BASE_DIR


class SQLiteCursorWrapper:
    def __init__(self, cursor):
        self.cursor = cursor

    @staticmethod
    def _adapt_query(query: str) -> str:
        # Adapt common MySQL constructs for SQLite fallback
        q = query.replace("CURDATE()", "DATE('now')")
        q = q.replace("NOW()", "CURRENT_TIMESTAMP")
        q = re.sub(r"INT\s+AUTO_INCREMENT\s+PRIMARY\s+KEY", "INTEGER PRIMARY KEY AUTOINCREMENT", q, flags=re.I)
        return q

    def execute(self, query, params=None):
        query = self._adapt_query(query)
        if params is not None:
            query = query.replace("%s", "?")
            return self.cursor.execute(query, params)
        return self.cursor.execute(query)

    def fetchone(self):
        row = self.cursor.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        return [dict(row) for row in self.cursor.fetchall()]

    def close(self):
        return self.cursor.close()

    def __getattr__(self, name):
        return getattr(self.cursor, name)


class SQLiteConnWrapper:
    is_sqlite = True

    def __init__(self, conn):
        self.conn = conn

    def cursor(self):
        return SQLiteCursorWrapper(self.conn.cursor())

    def commit(self):
        return self.conn.commit()

    def close(self):
        return self.conn.close()

    def rollback(self):
        return self.conn.rollback()


def get_db():
    """Return MySQL connection if configured and reachable, else SQLite fallback."""
    host = os.getenv("DB_HOST")
    if host and host.lower() != "localhost":
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
            conn.is_sqlite = False
            return conn
        except Exception as e:
            print(f"DEBUG: MySQL connection failed, falling back to SQLite: {e}")

    db_path = os.path.join(BASE_DIR, "eduguide1.db")
    db = sqlite3.connect(db_path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    return SQLiteConnWrapper(db)


def is_sqlite_conn(db) -> bool:
    return bool(getattr(db, "is_sqlite", False))


def resolve_student_numeric_id(cursor, student_code_or_id):
    """Map string student_code (e.g. COM2021001) to numeric users.id."""
    if student_code_or_id is None:
        return None
    cursor.execute("SELECT id FROM users WHERE student_code=%s", (str(student_code_or_id),))
    row = cursor.fetchone()
    if row:
        return row["id"]
    if str(student_code_or_id).isdigit():
        cursor.execute("SELECT id FROM users WHERE id=%s", (int(student_code_or_id),))
        row2 = cursor.fetchone()
        if row2:
            return row2["id"]
    return None


def get_db_status():
    """Live database health diagnostics for System Admin Database view."""
    start = time.perf_counter()
    db = get_db()
    cursor = db.cursor()
    sqlite_mode = is_sqlite_conn(db)
    tables_info = []
    engine_version = "SQLite 3"
    db_name = "eduguide1.db"
    port = "Local File"
    host = "local"

    try:
        if sqlite_mode:
            cursor.execute("SELECT sqlite_version() as v")
            v = cursor.fetchone()
            engine_version = f"SQLite {v['v']}" if v else "SQLite 3"
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
            t_names = [r["name"] for r in cursor.fetchall()]
        else:
            cursor.execute("SELECT VERSION() as v")
            v = cursor.fetchone()
            engine_version = f"MySQL {v['v']}" if v else "MySQL"
            db_name = os.getenv("DB_NAME", "edyguide1")
            host = os.getenv("DB_HOST", "127.0.0.1")
            port = os.getenv("DB_PORT", "3306")
            cursor.execute("SHOW FULL TABLES WHERE Table_type = 'BASE TABLE'")
            t_names = [list(r.values())[0] for r in cursor.fetchall()]

        for t in t_names:
            try:
                cursor.execute(f"SELECT COUNT(*) as c FROM `{t}`")
                cnt = cursor.fetchone()["c"]
                tables_info.append({"name": t, "rows": cnt})
            except Exception:
                tables_info.append({"name": t, "rows": 0})
    finally:
        db.close()

    latency_ms = round((time.perf_counter() - start) * 1000, 1)
    return {
        "healthy": True,
        "engine": engine_version,
        "is_sqlite": sqlite_mode,
        "host": host,
        "port": port,
        "db_name": db_name,
        "latency_ms": latency_ms,
        "table_count": len(tables_info),
        "total_rows": sum(t["rows"] for t in tables_info),
        "tables": tables_info,
    }


def ensure_core_tables():
    """Idempotently ensure enterprise tables exist on startup (MySQL & SQLite)."""
    db = get_db()
    cursor = db.cursor()
    sqlite_mode = is_sqlite_conn(db)
    pk = "INTEGER PRIMARY KEY AUTOINCREMENT" if sqlite_mode else "INT AUTO_INCREMENT PRIMARY KEY"

    ddl_statements = [
        f"""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id {pk},
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
        """,
        f"""
        CREATE TABLE IF NOT EXISTS attendance_sessions (
            id {pk},
            course_id VARCHAR(50) NOT NULL,
            course_name VARCHAR(255),
            instructor_id INT,
            session_code VARCHAR(16) NOT NULL,
            title VARCHAR(255),
            expires_at DATETIME,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS attendance_records (
            id {pk},
            session_id INT NOT NULL,
            user_id INT NOT NULL,
            student_code VARCHAR(50),
            status VARCHAR(20) DEFAULT 'Present',
            checked_in_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS assignments (
            id {pk},
            course_id VARCHAR(50) NOT NULL,
            instructor_id INT,
            title VARCHAR(255) NOT NULL,
            description TEXT,
            due_date VARCHAR(50),
            max_score INT DEFAULT 100,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS assignment_submissions (
            id {pk},
            assignment_id INT NOT NULL,
            user_id INT NOT NULL,
            student_code VARCHAR(50),
            file_path VARCHAR(255),
            note TEXT,
            score FLOAT,
            feedback TEXT,
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            graded_at TIMESTAMP NULL
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS announcements (
            id {pk},
            sender_id VARCHAR(100),
            sender_name VARCHAR(255),
            sender_role VARCHAR(50),
            target_scope VARCHAR(50) DEFAULT 'all',
            target_value VARCHAR(100),
            title VARCHAR(255) NOT NULL,
            message TEXT NOT NULL,
            priority VARCHAR(20) DEFAULT 'normal',
            recipients_count INT DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS demo_requests (
            id {pk},
            university_name VARCHAR(255) NOT NULL,
            contact_name VARCHAR(255) NOT NULL,
            role_title VARCHAR(150),
            email VARCHAR(255) NOT NULL,
            phone VARCHAR(60),
            student_count VARCHAR(60),
            notes TEXT,
            status VARCHAR(40) DEFAULT 'New',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id {pk},
            email VARCHAR(255) NOT NULL,
            role VARCHAR(40) NOT NULL,
            token VARCHAR(128) NOT NULL,
            expires_at DATETIME NOT NULL,
            used INT DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS quizzes (
            id {pk},
            course_id VARCHAR(50) NOT NULL,
            instructor_id INT,
            title VARCHAR(255) NOT NULL,
            time_limit_mins INT DEFAULT 15,
            is_published INT DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS quiz_questions (
            id {pk},
            quiz_id INT NOT NULL,
            question_text TEXT NOT NULL,
            option_a VARCHAR(500) NOT NULL,
            option_b VARCHAR(500) NOT NULL,
            option_c VARCHAR(500) NOT NULL,
            option_d VARCHAR(500) NOT NULL,
            correct_option VARCHAR(2) NOT NULL
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS quiz_attempts (
            id {pk},
            quiz_id INT NOT NULL,
            user_id INT NOT NULL,
            student_code VARCHAR(50),
            score INT DEFAULT 0,
            total_questions INT DEFAULT 0,
            percentage FLOAT DEFAULT 0,
            answers_json TEXT,
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ]

    try:
        for ddl in ddl_statements:
            cursor.execute(ddl)
        db.commit()
    except Exception as e:
        print(f"WARN: ensure_core_tables error: {e}")
    finally:
        db.close()
