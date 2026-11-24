"""
Idempotent demo seed for enterprise features (attendance_summary, assignments, sample B2B lead).
Ensures university demos look rich and populated right out of the box.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.db import get_db, ensure_core_tables
from core.security import hash_password


def seed():
    ensure_core_tables()
    db = get_db()
    cur = db.cursor()
    try:
        # 1. Seed attendance_summary if empty (for demo student #1)
        cur.execute("SELECT COUNT(*) as c FROM attendance_summary")
        if cur.fetchone()["c"] == 0:
            att_rows = [
                (1, "Financial Accounting I", 12, 1, 3),
                (1, "Business Statistics", 12, 2, 3),
                (1, "Principles of Economics", 12, 3, 3),
                (1, "Corporate Finance", 12, 0, 3),
            ]
            for uid, subj, tot, ab, mx in att_rows:
                cur.execute(
                    "INSERT INTO attendance_summary (user_id, subject_name, total_lectures, absences, max_absences) VALUES (%s, %s, %s, %s, %s)",
                    (uid, subj, tot, ab, mx),
                )
            print(f"Seeded {len(att_rows)} attendance_summary rows.")

        # 2. Seed sample LMS assignments if empty
        cur.execute("SELECT COUNT(*) as c FROM assignments")
        if cur.fetchone()["c"] == 0:
            cur.execute("SELECT course_id, instructor_id FROM courses ORDER BY course_id ASC LIMIT 3")
            sample_courses = cur.fetchall()
            if sample_courses:
                templates = [
                    ("Case Study: Financial Statement Analysis", "Analyze the 2025 balance sheet & income statement of an EGX30 listed company and upload a 3-page PDF report.", "2026-12-05", 20),
                    ("Midterm Practical Problem Set", "Solve questions 1 to 10 from Chapter 4 and attach your worked Excel/PDF solutions.", "2026-12-12", 25),
                    ("Group Presentation Deck", "Prepare a 10-slide executive presentation applying course frameworks to an Egyptian market startup.", "2026-12-20", 30),
                ]
                for idx, c in enumerate(sample_courses):
                    title, desc, due, mx = templates[idx % len(templates)]
                    cur.execute(
                        "INSERT INTO assignments (course_id, instructor_id, title, description, due_date, max_score) VALUES (%s, %s, %s, %s, %s, %s)",
                        (c["course_id"], c["instructor_id"] or 1, title, desc, due, mx),
                    )
                print(f"Seeded {len(sample_courses)} sample assignments.")

        # 3. Seed 1 sample B2B demo lead if empty
        cur.execute("SELECT COUNT(*) as c FROM demo_requests")
        if cur.fetchone()["c"] == 0:
            cur.execute(
                """
                INSERT INTO demo_requests (university_name, contact_name, role_title, email, phone, student_count, notes, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'New')
                """,
                ("New Giza University", "Dr. Karim El-Gamal", "VP of Digital Transformation", "k.elgamal@ngu.edu.eg", "+201001234567", "2,000 - 10,000", "Interested in AI Career Advisor + QR Attendance integration."),
            )
            print("Seeded 1 sample B2B demo request.")

        # 4. Seed 1 sample auto-graded quiz if empty
        cur.execute("SELECT COUNT(*) as c FROM quizzes")
        if cur.fetchone()["c"] == 0:
            cur.execute(
                "INSERT INTO quizzes (course_id, instructor_id, title, time_limit_mins, is_published) VALUES (%s, %s, %s, %s, 1)",
                ("ACC101", 1, "Financial Accounting Fundamentals — Practice Quiz", 10),
            )
            qid = cur.lastrowid
            qs = [
                ("Which accounting equation is fundamental to the balance sheet?", "Assets = Liabilities + Equity", "Assets = Revenue - Expenses", "Equity = Assets + Liabilities", "Cash = Profit + Debt", "A"),
                ("Which financial statement reports a company's revenues and expenses over a period?", "Balance Sheet", "Income Statement", "Statement of Cash Flows", "Trial Balance", "B"),
                ("What type of account is 'Accounts Receivable'?", "Liability", "Expense", "Current Asset", "Owner's Equity", "C"),
                ("Under accrual accounting, revenue is recognized when:", "Cash is collected", "It is earned", "Invoices are printed", "The fiscal year ends", "B"),
            ]
            for qt, a, b, c_opt, d, corr in qs:
                cur.execute(
                    "INSERT INTO quiz_questions (quiz_id, question_text, option_a, option_b, option_c, option_d, correct_option) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (qid, qt, a, b, c_opt, d, corr),
                )
            print("Seeded 1 sample auto-graded quiz with 4 questions.")

        # 5. Seed multi-university white-label demo student accounts (AUC & GUC)
        demo_unis = [
            ("AUC2026001", "Nourhan Sherif (AUC Demo)", "auc.student@auc.edu.eg", "School of Business", "3", 3.72, 2),
            ("GUC2026001", "Omar Khaled (GUC Demo)", "guc.student@guc.edu.eg", "Faculty of Management Technology", "2", 1.85, 4),
        ]
        pw_hash = hash_password("123456")
        for code, name, email, fac, lvl, gpa, uni_id in demo_unis:
            cur.execute("SELECT id FROM users WHERE student_code=%s OR email=%s", (code, email))
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO users (full_name, email, password, student_code, faculty, level, gpa, university_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (name, email, pw_hash, code, fac, lvl, gpa, uni_id),
                )
                print(f"Seeded multi-tenant demo student: {email} (university_id={uni_id})")

        db.commit()
    finally:
        db.close()
    print("Enterprise demo seeding complete!")


if __name__ == "__main__":
    seed()
