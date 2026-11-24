"""
Idempotent V3 Migration:
1. Links every university instructor in `admins` (role='instructor') to a real row in `instructors`
   with matching email & university_id, and assigns that university's courses/assignments/quizzes/books
   so each university's instructor portal is 100% isolated and populated.
2. Adds `university_id` to `attendance_sessions`.
3. Adds `seat_number` and `exam_type` to `exams` and backfills realistic seat numbers.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.db import get_db


def _ensure_column(cursor, table, column, col_def):
    try:
        cursor.execute(f"SHOW COLUMNS FROM {table} LIKE %s", (column,))
        if not cursor.fetchone():
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
            print(f"  + Added {table}.{column}")
    except Exception:
        try:
            cursor.execute(f"PRAGMA table_info({table})")
            cols = [r["name"] for r in cursor.fetchall()]
            if column not in cols:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
                print(f"  + Added (SQLite) {table}.{column}")
        except Exception as e:
            print(f"  ! Could not add {table}.{column}: {e}")


def run():
    db = get_db()
    cursor = db.cursor()
    try:
        print("=== 1. Schema Additions ===")
        _ensure_column(cursor, "attendance_sessions", "university_id", "INT DEFAULT NULL")
        _ensure_column(cursor, "exams", "seat_number", "VARCHAR(30) DEFAULT NULL")
        _ensure_column(cursor, "exams", "exam_type", "VARCHAR(30) DEFAULT 'Final'")

        cursor.execute("UPDATE attendance_sessions SET university_id = 3 WHERE university_id IS NULL")

        print("=== 2. Linking All University Instructors (admins <-> instructors) ===")
        cursor.execute("SELECT id, slug FROM universities")
        slug_map = {r["id"]: (r["slug"] or "UNI").upper() for r in cursor.fetchall()}

        cursor.execute("SELECT * FROM admins WHERE role='instructor' ORDER BY id ASC")
        admin_insts = cursor.fetchall()
        for a in admin_insts:
            email = (a.get("email") or "").strip()
            uni_id = int(a.get("university_id") or 3)
            full_name = (a.get("full_name") or "Faculty Member").strip()
            spec = (a.get("specialization") or "Academic Faculty").strip()

            # Split title + first/last cleanly
            tokens = full_name.split()
            title = "Dr."
            if tokens and tokens[0].lower() in ("dr.", "prof.", "dr", "prof"):
                title = tokens[0] if tokens[0].endswith(".") else tokens[0] + "."
                tokens = tokens[1:]
            first_name = tokens[0] if tokens else "Faculty"
            last_name = " ".join(tokens[1:]) if len(tokens) > 1 else slug_map.get(uni_id, "Staff")

            cursor.execute("SELECT instructor_id FROM instructors WHERE LOWER(email)=LOWER(%s)", (email,))
            existing = cursor.fetchone()
            if existing:
                real_iid = existing["instructor_id"]
                cursor.execute("UPDATE instructors SET university_id=%s WHERE instructor_id=%s", (uni_id, real_iid))
            else:
                cursor.execute(
                    """
                    INSERT INTO instructors (dept_id, first_name, last_name, title, specialisation, email, office_room, university_id)
                    VALUES (1, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (first_name, last_name, title, spec, email, f"{slug_map.get(uni_id, 'MAIN')}-Office 201", uni_id),
                )
                real_iid = cursor.lastrowid
                print(f"  + Linked {email} (uni={uni_id}) -> instructors.instructor_id={real_iid}")

            # For non-CU universities, bind their university's primary courses/assignments/quizzes/books to this demo professor
            # so when prof.<uni> logs in, their dashboard is 100% alive with THEIR university's data!
            if uni_id != 3:
                cursor.execute("UPDATE courses SET instructor_id=%s WHERE university_id=%s", (real_iid, uni_id))
                cursor.execute("UPDATE assignments SET instructor_id=%s WHERE university_id=%s", (real_iid, uni_id))
                cursor.execute("UPDATE quizzes SET instructor_id=%s WHERE university_id=%s", (real_iid, uni_id))
                cursor.execute("UPDATE marketplace_products SET instructor_id=%s WHERE university_id=%s", (real_iid, uni_id))

                # Seed 1 realistic pending student request for this professor if none exists
                cursor.execute("SELECT id FROM users WHERE university_id=%s LIMIT 1", (uni_id,))
                st = cursor.fetchone()
                if st:
                    cursor.execute(
                        "SELECT id FROM student_requests WHERE user_id=%s AND instructor_id=%s LIMIT 1",
                        (st["id"], real_iid),
                    )
                    if not cursor.fetchone():
                        cursor.execute(
                            """
                            INSERT INTO student_requests (user_id, instructor_id, title, message, status)
                            VALUES (%s, %s, 'Academic Inquiry', 'Could you please confirm the office hours before the upcoming midterm?', 'Pending')
                            """,
                            (st["id"], real_iid),
                        )

        print("=== 3. Backfilling Exam Seat Numbers & Types ===")
        cursor.execute(
            """
            SELECT e.id, e.user_id, u.university_id
            FROM exams e
            LEFT JOIN users u ON e.user_id = u.id
            WHERE e.seat_number IS NULL OR e.seat_number = ''
            ORDER BY e.id ASC
            """
        )
        exam_rows = cursor.fetchall()
        for idx, er in enumerate(exam_rows):
            u_slug = slug_map.get(er.get("university_id") or 3, "UNI")
            seat = f"{u_slug}-{101 + (idx % 180)}"
            etype = "Midterm" if idx % 3 == 2 else "Final"
            cursor.execute(
                "UPDATE exams SET seat_number=%s, exam_type=%s WHERE id=%s",
                (seat, etype, er["id"]),
            )

        db.commit()
        print("V3 MIGRATION COMPLETE!")
    finally:
        db.close()


if __name__ == "__main__":
    run()
