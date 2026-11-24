"""
Idempotent demo data seeder for all multi-university student accounts
(EELU, AUC, GUC, ASU, Alexandria, AASTMT) so every university demo looks 100% alive:
- Schedules, Exams, Student Grades, Payments, Attendance Summary, Notifications, Study Tasks.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.db import get_db

UNI_CURRICULA = {
    "eelu.student@eelu.edu.eg": {
        "courses": [
            ("IT301", "Artificial Intelligence & Machine Learning", 3, "A", 4.0, "Sunday", "10:00 AM", "12:00 PM", "Smart Lab 3", "Dr. Hossam El-Din", "2026-12-20"),
            ("CS304", "Cloud Computing & Distributed Systems", 3, "A-", 3.7, "Monday", "12:00 PM", "02:00 PM", "E-Hall B", "Dr. Reem Nabil", "2026-12-23"),
            ("SE302", "Modern Web Engineering", 3, "B+", 3.3, "Tuesday", "09:00 AM", "11:00 AM", "Online Sync Lab", "Dr. Karim Tarek", "2026-12-26"),
            ("IS305", "Database Management Systems", 3, "A", 4.0, "Wednesday", "11:00 AM", "01:00 PM", "Dokki Center Hall 2", "Dr. Salma Adel", "2026-12-29"),
        ],
        "tuition": 18500,
        "welcome_notif": ("Welcome to EELU Smart Campus", "Your Fall 2026 blended schedule, AI advisor, and online quizzes are now active."),
    },
    "auc.student@auc.edu.eg": {
        "courses": [
            ("FIN310", "Corporate Finance & Valuation", 3, "A", 4.0, "Sunday", "10:00 AM", "11:30 AM", "Waleed C114", "Dr. Nadia Mansour", "2026-12-18"),
            ("MGT320", "Global Corporate Strategy", 3, "A-", 3.7, "Monday", "01:00 PM", "02:30 PM", "Abdul Latif Jameel Hall", "Dr. Sherif Kamel", "2026-12-21"),
            ("ECON305", "Applied Econometrics", 3, "B+", 3.3, "Tuesday", "11:30 AM", "01:00 PM", "Hatem & Janet Mostafa Hall", "Dr. Mona El-Baradei", "2026-12-24"),
            ("BUS340", "FinTech & Digital Banking", 3, "A", 4.0, "Wednesday", "02:30 PM", "04:00 PM", "Venture Lab 202", "Dr. Tarek Hassan", "2026-12-28"),
        ],
        "tuition": 95000,
        "welcome_notif": ("AUC Fall 2026 Portal Ready", "Review your New Cairo schedule, Career Center CV optimizer, and verified transcript."),
    },
    "guc.student@guc.edu.eg": {
        "courses": [
            ("BI201", "Business Intelligence & Analytics", 4, "B", 3.0, "Saturday", "08:30 AM", "10:30 AM", "C3.204", "Dr. Tarek El-Shenawy", "2026-12-19"),
            ("ERP205", "Enterprise Resource Planning (SAP)", 4, "C+", 2.3, "Sunday", "10:30 AM", "12:30 PM", "B1.102", "Dr. Youssef Gamal", "2026-12-22"),
            ("FIN202", "Managerial Accounting", 4, "C", 2.0, "Tuesday", "12:30 PM", "02:30 PM", "H12", "Dr. Laila Amr", "2026-12-25"),
            ("OPR208", "Operations Research", 4, "D+", 1.3, "Thursday", "09:00 AM", "11:00 AM", "C7.301", "Dr. Hany Fouad", "2026-12-29"),
        ],
        "tuition": 78000,
        "welcome_notif": ("GUC Academic Standing Notice", "Check your Attendance & AI Study Planner to stay ahead of the 25% absence policy."),
    },
    "asu.student@asu.edu.eg": {
        "courses": [
            ("CIS301", "Algorithms & Data Structures", 3, "A-", 3.7, "Sunday", "09:00 AM", "11:00 AM", "FCIS Hall 1", "Dr. Hala Mostafa", "2026-12-20"),
            ("CIS305", "Operating Systems", 3, "B+", 3.3, "Monday", "11:00 AM", "01:00 PM", "FCIS Lab 4", "Dr. Wael Ahmed", "2026-12-23"),
            ("CIS310", "Software Engineering", 3, "A", 4.0, "Wednesday", "10:00 AM", "12:00 PM", "Credit Hall 2", "Dr. Ayman Reda", "2026-12-27"),
        ],
        "tuition": 14000,
        "welcome_notif": ("Ain Shams Credit-Hour Portal", "Fall 2026 lectures, QR attendance, and exam halls are now published."),
    },
    "alex.student@alexu.edu.eg": {
        "courses": [
            ("ENG201", "Engineering Mathematics III", 3, "A", 4.0, "Sunday", "09:00 AM", "11:00 AM", "Shatby Hall A", "Dr. Sherif Nabil", "2026-12-21"),
            ("ENG204", "Data Science for Engineers", 3, "A-", 3.7, "Tuesday", "11:00 AM", "01:00 PM", "SSP Lab 2", "Dr. Rania Magdy", "2026-12-24"),
            ("ENG209", "Control Systems", 3, "B+", 3.3, "Wednesday", "01:00 PM", "03:00 PM", "Main Bldg 305", "Dr. Khaled Saad", "2026-12-28"),
        ],
        "tuition": 16500,
        "welcome_notif": ("Alexandria University SSP Portal", "Your Shatby Campus schedule and Digital Student ID are active."),
    },
    "aast.student@aast.edu": {
        "courses": [
            ("CC401", "Deep Learning & Computer Vision", 3, "A", 4.0, "Sunday", "10:30 AM", "12:30 PM", "Abu Qir Bldg B", "Dr. Amr Fawzy", "2026-12-19"),
            ("CC405", "Cybersecurity & Network Defense", 3, "A", 4.0, "Monday", "12:30 PM", "02:30 PM", "Smart Lab 5", "Dr. Dina Samy", "2026-12-22"),
            ("CC409", "Maritime Logistics Analytics", 3, "A-", 3.7, "Wednesday", "09:00 AM", "11:00 AM", "Hall 401", "Dr. Mostafa Zaki", "2026-12-26"),
        ],
        "tuition": 52000,
        "welcome_notif": ("AASTMT Smart Portal Active", "Your Fall 2026 schedule, tuition receipt, and graduation audit are ready."),
    },
}


def seed_all():
    db = get_db()
    cur = db.cursor()
    try:
        for email, data in UNI_CURRICULA.items():
            cur.execute("SELECT id, student_code, full_name FROM users WHERE email=%s", (email,))
            u = cur.fetchone()
            if not u:
                continue
            uid = u["id"]
            courses = data["courses"]

            # 1. Schedules
            cur.execute("SELECT COUNT(*) as c FROM schedules WHERE user_id=%s", (uid,))
            if cur.fetchone()["c"] == 0:
                for code, name, hrs, gr, pts, day, st, en, room, doc, ex_dt in courses:
                    cur.execute(
                        "INSERT INTO schedules (user_id, subject_name, day_name, start_time, end_time, room, doctor_name, type) VALUES (%s,%s,%s,%s,%s,%s,%s,'Lecture')",
                        (uid, name, day, st, en, room, doc),
                    )

            # 2. Exams
            cur.execute("SELECT COUNT(*) as c FROM exams WHERE user_id=%s", (uid,))
            if cur.fetchone()["c"] == 0:
                for code, name, hrs, gr, pts, day, st, en, room, doc, ex_dt in courses:
                    cur.execute(
                        "INSERT INTO exams (user_id, subject_name, exam_date, exam_time, location) VALUES (%s,%s,%s,%s,%s)",
                        (uid, name, ex_dt, "10:00 AM - 12:00 PM", room),
                    )

            # 3. Student Grades
            cur.execute("SELECT COUNT(*) as c FROM student_grades WHERE user_id=%s", (uid,))
            if cur.fetchone()["c"] == 0:
                for code, name, hrs, gr, pts, day, st, en, room, doc, ex_dt in courses:
                    cur.execute(
                        "INSERT INTO student_grades (user_id, academic_year, semester, course_code, course_name, credit_hours, grade, points) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (uid, "2025-2026", "Spring", code, name, hrs, gr, pts),
                    )

            # 4. Payments (1 paid + 1 pending so Paymob / Receipt both look alive)
            cur.execute("SELECT COUNT(*) as c FROM payments WHERE user_id=%s", (uid,))
            if cur.fetchone()["c"] == 0:
                t_amt = data["tuition"]
                cur.execute(
                    "INSERT INTO payments (user_id, amount, payment_type, payment_status, payment_date, payment_method) VALUES (%s,%s,%s,'paid',CURDATE(),'Card')",
                    (uid, t_amt, "Spring 2026 Tuition Fees"),
                )
                cur.execute(
                    "INSERT INTO payments (user_id, amount, payment_type, payment_status, payment_date, payment_method) VALUES (%s,%s,%s,'pending',CURDATE(),'Pending')",
                    (uid, int(t_amt * 0.5), "Fall 2026 Installment #1"),
                )

            # 5. Attendance Summary (realistic mix including 1 warning for demo)
            cur.execute("SELECT COUNT(*) as c FROM attendance_summary WHERE user_id=%s", (uid,))
            if cur.fetchone()["c"] == 0:
                for idx, (code, name, *_) in enumerate(courses):
                    abs_cnt = 3 if (email.startswith("guc") and idx == 0) else (2 if idx == 1 else (1 if idx == 0 else 0))
                    cur.execute(
                        "INSERT INTO attendance_summary (user_id, subject_name, total_lectures, absences, max_absences) VALUES (%s,%s,12,%s,3)",
                        (uid, name, abs_cnt),
                    )

            # 6. Welcome Notification
            cur.execute("SELECT COUNT(*) as c FROM notifications WHERE user_id=%s", (uid,))
            if cur.fetchone()["c"] == 0:
                nt, nm = data["welcome_notif"]
                cur.execute(
                    "INSERT INTO notifications (user_id, title, message, type, status) VALUES (%s,%s,%s,'info','unread')",
                    (uid, nt, nm),
                )

            # 7. Study Tasks
            cur.execute("SELECT COUNT(*) as c FROM student_tasks WHERE user_id=%s", (uid,))
            if cur.fetchone()["c"] == 0:
                cur.execute(
                    "INSERT INTO student_tasks (user_id, title, deadline, status, priority) VALUES (%s,%s,%s,'Pending','High')",
                    (uid, f"Prepare Midterm Summary — {courses[0][1]}", "2026-12-05"),
                )
                cur.execute(
                    "INSERT INTO student_tasks (user_id, title, deadline, status, priority) VALUES (%s,%s,%s,'Completed','Medium')",
                    (uid, f"Complete Lab Exercises — {courses[1][1]}", "2026-12-01"),
                )

            print(f"Populated complete campus dataset for {email} (uid={uid})")

        db.commit()
    finally:
        db.close()
    print("ALL UNIVERSITY STUDENT DEMO DATASETS READY!")


if __name__ == "__main__":
    seed_all()
