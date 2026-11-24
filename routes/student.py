import os
import time
from datetime import datetime, timedelta
import werkzeug.utils
from flask import render_template, request, jsonify, session, redirect, url_for, flash
from flask import render_template, request, jsonify, session, redirect, url_for, flash, Response
from core.db import get_db, resolve_student_numeric_id
from core.security import login_required, log_audit

ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _max_credits_for_gpa(gpa_val) -> int:
    try:
        g = float(gpa_val or 0)
    except Exception:
        g = 2.0
    if g >= 3.0:
        return 21
    if g >= 2.0:
        return 18
    return 12


def _student_uni_id(cursor, nid=None) -> int:
    uni_id = session.get("university_id")
    if uni_id:
        try:
            return int(uni_id)
        except Exception:
            pass
    if nid:
        try:
            cursor.execute("SELECT university_id FROM users WHERE id=%s", (nid,))
            row = cursor.fetchone()
            if row and row.get("university_id"):
                return int(row["university_id"])
        except Exception:
            pass
    return 3


def register_student_routes(app):
    @app.route("/dashboard")
    @login_required
    def dashboard():
        user_id = session["user_id"]
        db = get_db()
        cursor = db.cursor()
        try:
            numeric_id = resolve_student_numeric_id(cursor, user_id)
            uni_id = _student_uni_id(cursor, numeric_id)

            cursor.execute("SELECT * FROM vw_studentprofile WHERE student_id=%s", (user_id,))
            user = cursor.fetchone()
            if user:
                user["student_code"] = user["student_id"]
                user["faculty"] = user["department"]
                user["level"] = user["year_level"]
            elif numeric_id:
                cursor.execute("SELECT * FROM users WHERE id=%s", (numeric_id,))
                user = cursor.fetchone()

            schedules, exams, payment, reminder = [], [], None, None
            attendance_alerts = []
            pending_assignments_count = 0
            available_quizzes_count = 0
            if numeric_id:
                cursor.execute("SELECT * FROM schedules WHERE user_id=%s", (numeric_id,))
                schedules = cursor.fetchall()
                cursor.execute("SELECT * FROM exams WHERE user_id=%s ORDER BY exam_date ASC, id ASC", (numeric_id,))
                exams = cursor.fetchall()
                cursor.execute("SELECT * FROM payments WHERE user_id=%s ORDER BY id DESC LIMIT 1", (numeric_id,))
                payment = cursor.fetchone()
                cursor.execute("SELECT * FROM reminders WHERE user_id=%s LIMIT 1", (numeric_id,))
                reminder = cursor.fetchone()

                # Attendance deprivation alerts (>= 18%)
                cursor.execute("SELECT * FROM attendance_summary WHERE user_id=%s", (numeric_id,))
                for att in cursor.fetchall():
                    tot = max(1, int(att.get("total_lectures") or 12))
                    ab = int(att.get("absences") or 0)
                    pct = round((ab / tot) * 100, 1)
                    if pct >= 18:
                        attendance_alerts.append({"subject": att["subject_name"], "pct": pct, "absences": ab, "total": tot})

                try:
                    cursor.execute(
                        """
                        SELECT COUNT(*) as c FROM assignments a
                        WHERE (a.university_id = %s OR a.university_id IS NULL)
                          AND NOT EXISTS (
                            SELECT 1 FROM assignment_submissions s WHERE s.assignment_id = a.id AND s.user_id = %s
                          )
                        """,
                        (uni_id, numeric_id),
                    )
                    pending_assignments_count = cursor.fetchone()["c"]
                except Exception:
                    pass
                try:
                    cursor.execute(
                        "SELECT COUNT(*) as c FROM quizzes WHERE is_published = 1 AND (university_id = %s OR university_id IS NULL)",
                        (uni_id,),
                    )
                    available_quizzes_count = cursor.fetchone()["c"]
                except Exception:
                    pass
        finally:
            db.close()

        return render_template(
            "dashboard.html",
            user=user,
            schedules=schedules,
            exams=exams,
            payment=payment,
            reminder=reminder,
            attendance_alerts=attendance_alerts,
            pending_assignments_count=pending_assignments_count,
            available_quizzes_count=available_quizzes_count,
        )

    @app.route("/schedule")
    @login_required
    def schedule_page():
        db = get_db()
        cursor = db.cursor()
        try:
            nid = resolve_student_numeric_id(cursor, session["user_id"])
            schedules = []
            if nid:
                cursor.execute("SELECT * FROM schedules WHERE user_id=%s", (nid,))
                schedules = cursor.fetchall()
        finally:
            db.close()
        return render_template("schedule.html", schedules=schedules)

    @app.route("/schedule/export.ics")
    @login_required
    def export_schedule_ics():
        db = get_db()
        cursor = db.cursor()
        try:
            nid = resolve_student_numeric_id(cursor, session["user_id"])
            schedules = []
            if nid:
                cursor.execute("SELECT * FROM schedules WHERE user_id=%s", (nid,))
                schedules = cursor.fetchall()
        finally:
            db.close()

        day_map = {
            "monday": (0, "MO"), "tuesday": (1, "TU"), "wednesday": (2, "WE"),
            "thursday": (3, "TH"), "friday": (4, "FR"), "saturday": (5, "SA"), "sunday": (6, "SU"),
            "الاثنين": (0, "MO"), "الثلاثاء": (1, "TU"), "الأربعاء": (2, "WE"),
            "الخميس": (3, "TH"), "الجمعة": (4, "FR"), "السبت": (5, "SA"), "الأحد": (6, "SU"),
        }

        def _parse_hhmm(val, default_h=9, default_m=0):
            s = str(val or "").strip().upper()
            is_pm = "PM" in s
            is_am = "AM" in s
            s = s.replace("AM", "").replace("PM", "").strip()
            parts = s.split(":")
            try:
                h = int(parts[0])
                m = int(parts[1]) if len(parts) > 1 else 0
                if is_pm and h < 12:
                    h += 12
                if is_am and h == 12:
                    h = 0
                return h, m
            except Exception:
                return default_h, default_m

        today = datetime.now()
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//EDUGUIDE//University Timetable//EN",
            "CALSCALE:GREGORIAN",
        ]
        for idx, s in enumerate(schedules):
            wd, byday = day_map.get(str(s.get("day_name") or "").strip().lower(), (6, "SU"))
            delta = (wd - today.weekday()) % 7
            target_day = today + timedelta(days=delta)
            sh, sm = _parse_hhmm(s.get("start_time"), 9, 0)
            eh, em = _parse_hhmm(s.get("end_time"), sh + 2, sm)
            dt_start = target_day.replace(hour=sh, minute=sm, second=0).strftime("%Y%m%dT%H%M%S")
            dt_end = target_day.replace(hour=eh, minute=em, second=0).strftime("%Y%m%dT%H%M%S")
            subj = str(s.get("subject_name") or "Lecture").replace("\n", " ")
            loc = str(s.get("room") or "Campus Hall").replace("\n", " ")
            doc = str(s.get("doctor_name") or "Faculty").replace("\n", " ")
            lines.extend([
                "BEGIN:VEVENT",
                f"UID:eduguide-{s.get('id', idx)}-{session.get('user_id', 'st')}@eduguide.local",
                f"DTSTART:{dt_start}",
                f"DTEND:{dt_end}",
                f"RRULE:FREQ=WEEKLY;BYDAY={byday};COUNT=14",
                f"SUMMARY:{subj}",
                f"LOCATION:{loc}",
                f"DESCRIPTION:Instructor: {doc}",
                "END:VEVENT",
            ])
        lines.append("END:VCALENDAR")
        return Response(
            "\r\n".join(lines),
            mimetype="text/calendar",
            headers={"Content-Disposition": "attachment; filename=university_schedule.ics"},
        )

    @app.route("/schedule_details/<course_id>")
    @login_required
    def schedule_details(course_id):
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute(
                """
                SELECT c.*, i.title as inst_title, i.first_name, i.last_name
                FROM courses c
                LEFT JOIN instructors i ON c.instructor_id = i.instructor_id
                WHERE c.course_id=%s
                """,
                (course_id,),
            )
            course = cursor.fetchone()
            if course:
                parts = [course.get("inst_title"), course.get("first_name"), course.get("last_name")]
                course["instructor_name"] = " ".join(p for p in parts if p) or "TBA"
        finally:
            db.close()
        if not course:
            return redirect(url_for("schedule_page"))
        return render_template("schedule_details.html", course=course)

    @app.route("/exams")
    @login_required
    def exams_page():
        db = get_db()
        cursor = db.cursor()
        try:
            nid = resolve_student_numeric_id(cursor, session["user_id"])
            exams = []
            if nid:
                cursor.execute("SELECT * FROM exams WHERE user_id=%s", (nid,))
                exams = cursor.fetchall()
        finally:
            db.close()
        return render_template("exams.html", exams=exams)

    @app.route("/gpa")
    @login_required
    def gpa_page():
        user_id = session["user_id"]
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("SELECT * FROM vw_studentprofile WHERE student_id=%s", (user_id,))
            user = cursor.fetchone()
            if user:
                user["student_code"] = user["student_id"]
                user["faculty"] = user["department"]
            nid = resolve_student_numeric_id(cursor, user_id)
            if not user and nid:
                cursor.execute("SELECT * FROM users WHERE id=%s", (nid,))
                user = cursor.fetchone()

            if nid:
                cursor.execute(
                    "SELECT * FROM student_grades WHERE user_id=%s ORDER BY academic_year DESC, semester ASC",
                    (nid,),
                )
                all_grades = cursor.fetchall()
            else:
                all_grades = []
        finally:
            db.close()

        years = {}
        for g in all_grades:
            y = g["academic_year"]
            s = g["semester"]
            year_label = y if "Year" in str(y) else f"Academic Year {y}"

            if y not in years:
                years[y] = {
                    "label": year_label,
                    "semesters": {},
                    "total_points": 0,
                    "total_credits": 0,
                    "year_gpa": 0.0,
                }
            if s not in years[y]["semesters"]:
                years[y]["semesters"][s] = {
                    "courses": [],
                    "total_points": 0,
                    "total_credits": 0,
                    "semester_gpa": 0.0,
                }

            years[y]["semesters"][s]["courses"].append(g)
            try:
                pts = float(g["points"])
                creds = int(g["credit_hours"])
                quality_points = pts * creds
                years[y]["semesters"][s]["total_points"] += quality_points
                years[y]["semesters"][s]["total_credits"] += creds
                years[y]["total_points"] += quality_points
                years[y]["total_credits"] += creds
            except Exception:
                pass

        for y in years:
            if years[y]["total_credits"] > 0:
                years[y]["year_gpa"] = round(years[y]["total_points"] / years[y]["total_credits"], 2)
            for s in years[y]["semesters"]:
                if years[y]["semesters"][s]["total_credits"] > 0:
                    years[y]["semesters"][s]["semester_gpa"] = round(
                        years[y]["semesters"][s]["total_points"] / years[y]["semesters"][s]["total_credits"], 2
                    )

        return render_template("gpa.html", user=user, years=years)

    @app.route("/registration", methods=["GET", "POST"])
    @login_required
    def registration_page():
        student_code = session["user_id"]
        db = get_db()
        cursor = db.cursor()
        try:
            nid = resolve_student_numeric_id(cursor, student_code)
            cursor.execute("SELECT * FROM users WHERE id=%s", (nid,)) if nid else None
            user = cursor.fetchone() if nid else {}
            gpa_val = (user or {}).get("gpa") or 2.5
            max_credits = _max_credits_for_gpa(gpa_val)

            # Completed courses (passed in student_grades)
            completed_codes = set()
            if nid:
                cursor.execute(
                    "SELECT course_code FROM student_grades WHERE user_id=%s AND grade NOT IN ('F', 'FA', 'W')",
                    (nid,),
                )
                completed_codes = {r["course_code"] for r in cursor.fetchall()}

            # Current active enrollments
            cursor.execute(
                """
                SELECT e.enrollment_id, e.course_id, e.semester, e.academic_year, e.status,
                       c.name as course_name, c.credit_hours, c.year_level,
                       i.title as inst_title, i.first_name, i.last_name
                FROM enrollments e
                JOIN courses c ON e.course_id = c.course_id
                LEFT JOIN instructors i ON c.instructor_id = i.instructor_id
                WHERE e.student_id = %s AND e.status = 'Enrolled'
                ORDER BY e.enrollment_id DESC
                """,
                (student_code,),
            )
            active_enrollments = cursor.fetchall()
            for r in active_enrollments:
                r["instructor_name"] = " ".join(
                    p for p in [r.get("inst_title"), r.get("first_name"), r.get("last_name")] if p
                ) or "TBA"

            enrolled_codes = {r["course_id"] for r in active_enrollments}
            current_credits = sum(int(r.get("credit_hours") or 0) for r in active_enrollments)

            # Handle Add / Drop actions
            if request.method == "POST":
                action = request.form.get("action")
                course_id = (request.form.get("course_id") or "").strip()

                if action == "add" and course_id:
                    if course_id in enrolled_codes:
                        flash("You are already enrolled in this course.", "warning")
                    else:
                        cursor.execute("SELECT * FROM courses WHERE course_id=%s", (course_id,))
                        course = cursor.fetchone()
                        if not course:
                            flash("Course not found.", "danger")
                        else:
                            # Prerequisite check
                            cursor.execute(
                                "SELECT prereq_id FROM courseprerequisites WHERE course_id=%s",
                                (course_id,),
                            )
                            prereqs = [r["prereq_id"] for r in cursor.fetchall()]
                            missing_prereqs = [p for p in prereqs if p not in completed_codes]

                            # Seat capacity check
                            cursor.execute(
                                "SELECT COUNT(*) as c FROM enrollments WHERE course_id=%s AND status='Enrolled'",
                                (course_id,),
                            )
                            seat_count = cursor.fetchone()["c"]
                            max_cap = int(course.get("max_capacity") or 100)
                            c_credits = int(course.get("credit_hours") or 3)

                            if missing_prereqs:
                                flash(f"Missing prerequisite(s): {', '.join(missing_prereqs)}", "danger")
                            elif seat_count >= max_cap:
                                flash("This course section has reached maximum seat capacity.", "danger")
                            elif current_credits + c_credits > max_credits:
                                flash(
                                    f"Credit hour limit exceeded! Max allowed for GPA {gpa_val}: {max_credits} hrs.",
                                    "danger",
                                )
                            else:
                                cursor.execute(
                                    """
                                    INSERT INTO enrollments (student_id, course_id, semester, academic_year, enrollment_date, status)
                                    VALUES (%s, %s, %s, %s, CURDATE(), 'Enrolled')
                                    """,
                                    (student_code, course_id, course.get("semester") or "Fall", "2026-2027"),
                                )
                                # Time-conflict check & smart slot assignment
                                slot_day = (request.form.get("day_name") or "Thursday").strip()
                                slot_start = (request.form.get("start_time") or "10:00 AM").strip()
                                slot_end = (request.form.get("end_time") or "12:00 PM").strip()
                                has_conflict = False
                                if nid:
                                    cursor.execute(
                                        "SELECT subject_name FROM schedules WHERE user_id=%s AND LOWER(day_name)=LOWER(%s) AND start_time=%s LIMIT 1",
                                        (nid, slot_day, slot_start),
                                    )
                                    conf = cursor.fetchone()
                                    if conf and request.form.get("day_name"):
                                        has_conflict = True
                                        flash(
                                            f"Time conflict detected! You already have '{conf['subject_name']}' on {slot_day} at {slot_start}.",
                                            "danger",
                                        )
                                    elif conf:
                                        # Auto-pick an open time slot on Thursday/Saturday
                                        for cand_day, cand_s, cand_e in [
                                            ("Thursday", "12:00 PM", "02:00 PM"),
                                            ("Saturday", "10:00 AM", "12:00 PM"),
                                            ("Tuesday", "02:00 PM", "04:00 PM"),
                                            ("Wednesday", "02:00 PM", "04:00 PM"),
                                        ]:
                                            cursor.execute(
                                                "SELECT id FROM schedules WHERE user_id=%s AND LOWER(day_name)=LOWER(%s) AND start_time=%s LIMIT 1",
                                                (nid, cand_day, cand_s),
                                            )
                                            if not cursor.fetchone():
                                                slot_day, slot_start, slot_end = cand_day, cand_s, cand_e
                                                break

                                if not has_conflict:
                                    cursor.execute(
                                        """
                                        INSERT INTO schedules (user_id, subject_name, day_name, start_time, end_time, room, doctor_name, type)
                                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                                        INSERT INTO enrollments (student_id, course_id, semester, academic_year, enrollment_date, status)
                                        VALUES (%s, %s, %s, %s, CURDATE(), 'Enrolled')
                                        """,
                                        (nid, course["name"], "Sunday", "10:00", "12:00", "Hall B", "Faculty Staff", "Lecture"),
                                        (student_code, course_id, course.get("semester") or "Fall", "2026-2027"),
                                    )
                                db.commit()
                                log_audit("COURSE_REGISTER", "course", course_id, f"Student {student_code} registered {course_id}")
                                flash(f"Successfully registered for {course_id} — {course['name']}!", "success")
                                    if nid:
                                        cursor.execute(
                                            """
                                            INSERT INTO schedules (user_id, subject_name, day_name, start_time, end_time, room, doctor_name, type)
                                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                                            """,
                                            (nid, course["name"], slot_day, slot_start, slot_end, "Hall B", "Faculty Staff", "Lecture"),
                                        )
                                    db.commit()
                                    log_audit("COURSE_REGISTER", "course", course_id, f"Student {student_code} registered {course_id}")
                                    flash(f"Successfully registered for {course_id} — {course['name']} ({slot_day} {slot_start})!", "success")
                    return redirect(url_for("registration_page"))

                if action == "drop" and course_id:
                    cursor.execute("SELECT name FROM courses WHERE course_id=%s", (course_id,))
                    c_row = cursor.fetchone()
                    cursor.execute(
                        "UPDATE enrollments SET status='Dropped' WHERE student_id=%s AND course_id=%s AND status='Enrolled'",
                        (student_code, course_id),
                    )
                    if nid and c_row:
                        cursor.execute(
                            "DELETE FROM schedules WHERE user_id=%s AND subject_name=%s",
                            (nid, c_row["name"]),
                        )
                    db.commit()
                    log_audit("COURSE_DROP", "course", course_id, f"Student {student_code} dropped {course_id}")
                    flash(f"Dropped {course_id} successfully.", "success")
                    return redirect(url_for("registration_page"))

            # Available catalog scoped to the student's university
            uni_id = _student_uni_id(cursor, nid)
            cursor.execute(
                """
                SELECT c.*, d.name as dept_name,
                       i.title as inst_title, i.first_name, i.last_name
                FROM courses c
                LEFT JOIN departments d ON c.dept_id = d.dept_id
                LEFT JOIN instructors i ON c.instructor_id = i.instructor_id
                WHERE c.university_id = %s OR c.university_id IS NULL
                ORDER BY c.year_level ASC, c.course_id ASC
                """,
                (uni_id,),
            )
            all_courses = cursor.fetchall()

            cursor.execute("SELECT course_id, prereq_id FROM courseprerequisites")
            prereq_map = {}
            for pr in cursor.fetchall():
                prereq_map.setdefault(pr["course_id"], []).append(pr["prereq_id"])

            cursor.execute("SELECT course_id, COUNT(*) as c FROM enrollments WHERE status='Enrolled' GROUP BY course_id")
            seat_map = {r["course_id"]: r["c"] for r in cursor.fetchall()}

            catalog = []
            for c in all_courses:
                cid = c["course_id"]
                prereqs = prereq_map.get(cid, [])
                missing = [p for p in prereqs if p not in completed_codes]
                enrolled_now = seat_map.get(cid, 0)
                max_cap = int(c.get("max_capacity") or 100)
                c["instructor_name"] = " ".join(
                    p for p in [c.get("inst_title"), c.get("first_name"), c.get("last_name")] if p
                ) or "TBA"
                c["prereqs"] = prereqs
                c["missing_prereqs"] = missing
                c["enrolled_count"] = enrolled_now
                c["max_capacity"] = max_cap
                c["is_enrolled"] = cid in enrolled_codes
                c["is_completed"] = cid in completed_codes
                catalog.append(c)

            regs = []
            if nid:
                cursor.execute("SELECT * FROM schedules WHERE user_id=%s", (nid,))
                regs = cursor.fetchall()
        finally:
            db.close()

        return render_template(
            "registration.html",
            registrations=regs,
            active_enrollments=active_enrollments,
            catalog=catalog,
            current_credits=current_credits,
            max_credits=max_credits,
            gpa=gpa_val,
        )

    @app.route("/career")
    def career_page():
        return render_template("career.html")

    @app.route("/cv_optimizer")
    @login_required
    def cv_optimizer_page():
        return render_template("cv_optimizer.html")

    @app.route("/career_matching")
    @login_required
    def career_matching_page():
        db = get_db()
        cursor = db.cursor()
        jobs = []
        applied_ids = set()
        student_gpa = 3.0
        try:
            nid = resolve_student_numeric_id(cursor, session["user_id"])
            if nid:
                cursor.execute("SELECT gpa, faculty FROM users WHERE id=%s", (nid,))
                u = cursor.fetchone()
                if u and u.get("gpa"):
                    student_gpa = float(u["gpa"])
                cursor.execute("SELECT job_id FROM internship_applications WHERE user_id=%s", (nid,))
                applied_ids = {int(r["job_id"]) for r in cursor.fetchall()}

            cursor.execute("SELECT * FROM career_jobs ORDER BY id ASC")
            jobs = cursor.fetchall()
            for j in jobs:
                min_g = float(j.get("min_gpa") or 2.0)
                j["match_score"] = max(62, min(98, int(84 + (student_gpa - min_g) * 12)))
                j["applied"] = int(j["id"]) in applied_ids
                fld = (j.get("field") or "").lower()
                mode = (j.get("work_mode") or "Hybrid").lower()
                cats = []
                if any(k in fld for k in ("tech", "software", "data", "ai", "cyber", "it", "computer", "cloud")):
                    cats.append("tech")
                if any(k in fld for k in ("fin", "account", "bank", "econ", "business", "market")):
                    cats.append("finance")
                if "remote" in mode:
                    cats.append("remote")
                if not cats:
                    cats.append("tech")
                j["cat_tags"] = " ".join(cats)
        finally:
            db.close()
        return render_template("career_matching.html", jobs=jobs, student_gpa=student_gpa)

    @app.route("/api/internships/apply/<int:job_id>", methods=["POST"])
    @login_required
    def apply_internship(job_id):
        db = get_db()
        cursor = db.cursor()
        try:
            nid = resolve_student_numeric_id(cursor, session["user_id"])
            if not nid:
                return jsonify({"success": False, "error": "Student account required."}), 403
            cursor.execute("SELECT * FROM career_jobs WHERE id=%s", (job_id,))
            job = cursor.fetchone()
            if not job:
                return jsonify({"success": False, "error": "Internship not found."}), 404

            cursor.execute(
                "SELECT id FROM internship_applications WHERE user_id=%s AND job_id=%s",
                (nid, job_id),
            )
            if cursor.fetchone():
                return jsonify({"success": True, "already_applied": True, "message": "Already applied."})

            company = job.get("company_name") or "EDUGUIDE Partner"
            cursor.execute(
                """
                INSERT INTO internship_applications (user_id, job_id, job_title, company_name, status)
                VALUES (%s, %s, %s, %s, 'Applied')
                """,
                (nid, job_id, job["job_title"], company),
            )
            cursor.execute(
                "INSERT INTO notifications (user_id, title, message, type, status) VALUES (%s, %s, %s, 'career', 'unread')",
                (nid, f"Internship Application Sent: {job['job_title']}", f"Your application to {company} has been submitted successfully!"),
            )
            db.commit()
            log_audit("INTERNSHIP_APPLIED", "career_job", job_id, f"Student {session['user_id']} applied to {job['job_title']} at {company}")
        finally:
            db.close()
        return jsonify({"success": True, "already_applied": False})

    @app.route("/mock_interview")
    @login_required
    def mock_interview_page():
        db = get_db()
        cursor = db.cursor()
        history = []
        try:
            nid = resolve_student_numeric_id(cursor, session["user_id"])
            if nid:
                cursor.execute(
                    "SELECT id, field, score, feedback, created_at FROM mock_interview_sessions WHERE user_id=%s ORDER BY id DESC LIMIT 6",
                    (nid,),
                )
                history = cursor.fetchall()
        except Exception:
            history = []
        finally:
            db.close()
        return render_template("mock_interview.html", interview_history=history)

    @app.route("/notifications")
    @login_required
    def notifications_page():
        db = get_db()
        cursor = db.cursor()
        try:
            nid = resolve_student_numeric_id(cursor, session["user_id"])
            notifs = []
            if nid:
                cursor.execute("SELECT * FROM notifications WHERE user_id=%s ORDER BY created_at DESC", (nid,))
                notifs = cursor.fetchall()
                # Mark as read when opened
                cursor.execute("UPDATE notifications SET status='read' WHERE user_id=%s AND (status IS NULL OR status='unread')", (nid,))
                db.commit()
        finally:
            db.close()
        return render_template("notifications.html", notifications=notifs)

    @app.route("/notifications/count")
    def notifications_count():
        if "user_id" not in session:
            return jsonify({"count": 0})
        db = get_db()
        cursor = db.cursor()
        try:
            if session.get("role") == "student":
                n_id = resolve_student_numeric_id(cursor, session["user_id"])
                if n_id:
                    cursor.execute(
                        "SELECT COUNT(*) as c FROM notifications WHERE user_id=%s AND (status='unread' OR status IS NULL)",
                        (n_id,),
                    )
                    cnt = cursor.fetchone()
                    return jsonify({"count": cnt["c"] if cnt else 0})
        except Exception:
            pass
        finally:
            db.close()
        return jsonify({"count": 0})

    @app.route("/marketplace")
    @login_required
    def marketplace_page():
        q = (request.args.get("q") or "").strip()
        db = get_db()
        cursor = db.cursor()
        try:
            nid = resolve_student_numeric_id(cursor, session["user_id"])
            uni_id = _student_uni_id(cursor, nid)
            sql = """
                SELECT mp.*, i.first_name, i.last_name
                FROM marketplace_products mp
                LEFT JOIN instructors i ON mp.instructor_id = i.instructor_id
                WHERE (mp.university_id = %s OR mp.university_id IS NULL)
            """
            params = [uni_id]
            if q:
                sql += " AND (mp.title LIKE %s OR mp.category LIKE %s OR mp.summary LIKE %s)"
                params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
            sql += " ORDER BY mp.created_at DESC"
            cursor.execute(sql, tuple(params))
            products = cursor.fetchall()
            for p in products:
                fn = p.get("first_name") or ""
                ln = p.get("last_name") or ""
                p["doctor_name"] = f"{fn} {ln}".strip() or "Faculty Staff"
        finally:
            db.close()
        return render_template("marketplace.html", products=products, search_query=q)

    @app.route("/marketplace/<int:product_id>")
    @login_required
    def marketplace_details(product_id):
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute(
                """
                SELECT mp.*, i.first_name, i.last_name
                FROM marketplace_products mp
                LEFT JOIN instructors i ON mp.instructor_id = i.instructor_id
                WHERE mp.id=%s
                """,
                (product_id,),
            )
            product = cursor.fetchone()
            if product:
                fn = product.get("first_name") or ""
                ln = product.get("last_name") or ""
                product["doctor_name"] = f"{fn} {ln}".strip() or "Faculty Staff"
        finally:
            db.close()
        if not product:
            return redirect(url_for("marketplace_page"))
        return render_template("marketplace_details.html", product=product)

    @app.route("/new_request", methods=["GET", "POST"])
    @login_required
    def new_request_page():
        db = get_db()
        cursor = db.cursor()
        try:
            nid = resolve_student_numeric_id(cursor, session["user_id"])
            uni_id = _student_uni_id(cursor, nid)
            if request.method == "POST":
                instructor_id = request.form.get("instructor_id")
                req_title = (request.form.get("request_type") or request.form.get("title") or "Direct Message").strip()
                msg = (request.form.get("message") or "").strip()

                cursor.execute(
                    "INSERT INTO student_requests (user_id, instructor_id, title, message, status) VALUES (%s, %s, %s, %s, 'Pending')",
                    (nid or session["user_id"], instructor_id, req_title, msg),
                )
                db.commit()
                flash("Request submitted successfully!", "success")
                return redirect(url_for("my_requests_page"))

            cursor.execute(
                "SELECT instructor_id as id, title, first_name, last_name FROM instructors WHERE university_id = %s OR university_id IS NULL ORDER BY first_name ASC",
                (uni_id,),
            )
            instructors = cursor.fetchall()
            for ins in instructors:
                ins["full_name"] = " ".join(p for p in [ins.get("title"), ins.get("first_name"), ins.get("last_name")] if p)
        finally:
            db.close()
        return render_template("new_request.html", instructors=instructors)

    @app.route("/my_requests")
    @login_required
    def my_requests_page():
        db = get_db()
        cursor = db.cursor()
        try:
            nid = resolve_student_numeric_id(cursor, session["user_id"])
            reqs = []
            if nid:
                cursor.execute(
                    """
                    SELECT sr.*, i.first_name, i.last_name
                    FROM student_requests sr
                    LEFT JOIN instructors i ON sr.instructor_id = i.instructor_id
                    WHERE sr.user_id=%s
                    ORDER BY sr.created_at DESC
                    """,
                    (nid,),
                )
                reqs = cursor.fetchall()
                for r in reqs:
                    r["doctor_name"] = f"{r.get('first_name') or ''} {r.get('last_name') or ''}".strip() or "Academic Advisor"
        finally:
            db.close()
        return render_template("my_requests.html", requests=reqs)

    @app.route("/my_notes", methods=["GET", "POST"])
    @login_required
    def my_notes():
        db = get_db()
        cursor = db.cursor()
        try:
            nid = resolve_student_numeric_id(cursor, session["user_id"]) or session["user_id"]
            if request.method == "POST":
                title = (request.form.get("title") or "New Note").strip()
                content = (request.form.get("content") or "").strip()
                category = (request.form.get("category") or "General").strip()
                cursor.execute(
                    "INSERT INTO student_notes (user_id, title, content, category) VALUES (%s, %s, %s, %s)",
                    (nid, title, content, category),
                )
                db.commit()
                flash("Note saved!", "success")
                return redirect(url_for("my_notes"))

            cursor.execute("SELECT * FROM student_notes WHERE user_id=%s ORDER BY created_at DESC", (nid,))
            notes = cursor.fetchall()
        finally:
            db.close()
        return render_template("my_notes.html", notes=notes)

    @app.route("/delete_note/<int:note_id>", methods=["POST"])
    @login_required
    def delete_note(note_id):
        db = get_db()
        cursor = db.cursor()
        try:
            nid = resolve_student_numeric_id(cursor, session["user_id"])
            if nid:
                cursor.execute("DELETE FROM student_notes WHERE id=%s AND user_id=%s", (note_id, nid))
                db.commit()
        finally:
            db.close()
        flash("Note deleted!", "success")
        return redirect(url_for("my_notes"))

    @app.route("/edit_note/<int:note_id>", methods=["POST"])
    @login_required
    def edit_note(note_id):
        db = get_db()
        cursor = db.cursor()
        try:
            nid = resolve_student_numeric_id(cursor, session["user_id"])
            title = (request.form.get("title") or "").strip()
            content = (request.form.get("content") or "").strip()
            category = (request.form.get("category") or "General").strip()
            if nid:
                cursor.execute(
                    "UPDATE student_notes SET title=%s, content=%s, category=%s WHERE id=%s AND user_id=%s",
                    (title, content, category, note_id, nid),
                )
                db.commit()
        finally:
            db.close()
        flash("Note updated!", "success")
        return redirect(url_for("my_notes"))

    @app.route("/profile")
    @login_required
    def profile_page():
        user_id = session["user_id"]
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute(
                """
                SELECT v.*, u.profile_pic
                FROM vw_studentprofile v
                LEFT JOIN users u ON v.student_id = u.student_code
                WHERE v.student_id=%s
                """,
                (user_id,),
            )
            user = cursor.fetchone()
            if user:
                user["student_code"] = user["student_id"]
                user["faculty"] = user["department"]
                user["level"] = user["year_level"]

            nid = resolve_student_numeric_id(cursor, user_id)
            if not user and nid:
                cursor.execute("SELECT * FROM users WHERE id=%s", (nid,))
                user = cursor.fetchone()

            latest_payment = None
            subject_count = {"total_subjects": 0}
            exam_count = {"total_exams": 0}
            req_count = {"total_requests": 0}

            if nid:
                cursor.execute(
                    "SELECT *, amount as amount_egp, payment_type as type_name, payment_status as status_name FROM payments WHERE user_id=%s ORDER BY id DESC LIMIT 1",
                    (nid,),
                )
                latest_payment = cursor.fetchone()
                if latest_payment:
                    latest_payment["payment_type"] = latest_payment["type_name"]
                    latest_payment["payment_status"] = latest_payment["status_name"]

                cursor.execute("SELECT COUNT(*) as total_subjects FROM schedules WHERE user_id=%s", (nid,))
                subject_count = cursor.fetchone() or subject_count

                cursor.execute("SELECT COUNT(*) as total_exams FROM exams WHERE user_id=%s", (nid,))
                exam_count = cursor.fetchone() or exam_count

                cursor.execute("SELECT COUNT(*) as total_requests FROM student_requests WHERE user_id=%s", (nid,))
                req_count = cursor.fetchone() or req_count
        finally:
            db.close()

        return render_template(
            "profile.html",
            user=user,
            subject_count=subject_count,
            exam_count=exam_count,
            req_count=req_count,
            latest_payment=latest_payment,
        )

    @app.route("/edit_profile", methods=["GET", "POST"])
    @login_required
    def edit_profile():
        db = get_db()
        cursor = db.cursor()
        try:
            student_code = session["user_id"]
            if request.method == "POST":
                email = (request.form.get("email") or "").strip()
                phone = (request.form.get("phone") or "").strip()
                address = (request.form.get("address") or "").strip()

                # Persist in legacy students table (if present) AND primary users table (all 7 universities!)
                try:
                    cursor.execute(
                        "UPDATE students SET email=%s, phone=%s, address=%s WHERE student_id=%s",
                        (email, phone, address, student_code),
                    )
                except Exception:
                    pass
                cursor.execute(
                    "UPDATE users SET email=COALESCE(NULLIF(%s, ''), email), phone=%s, address=%s WHERE student_code=%s",
                    (email, phone, address, student_code),
                )

                profile_pic = request.files.get("profile_pic")
                if profile_pic and profile_pic.filename:
                    ext = os.path.splitext(profile_pic.filename)[1].lower()
                    if ext not in ALLOWED_IMAGE_EXTS:
                        flash("Unsupported image format. Please upload PNG, JPG, or WEBP.", "danger")
                        return redirect(url_for("edit_profile"))
                    filename = werkzeug.utils.secure_filename(profile_pic.filename)
                    filename = f"user_{student_code}_{int(time.time())}_{filename}"
                    uploads_dir = os.path.join(app.static_folder, "uploads")
                    os.makedirs(uploads_dir, exist_ok=True)
                    profile_pic.save(os.path.join(uploads_dir, filename))
                    cursor.execute("UPDATE users SET profile_pic=%s WHERE student_code=%s", (filename, student_code))
                    session["profile_pic"] = filename

                db.commit()
                log_audit("PROFILE_UPDATE", "student", student_code, f"Updated contact info ({email})")
                flash("Profile updated successfully!", "success")
                return redirect(url_for("profile_page"))

            # Unified lookup: works for ALL universities (users primary + optional students details)
            cursor.execute(
                """
                SELECT u.id, u.full_name, u.email, u.student_code, u.faculty, u.level, u.gpa, u.profile_pic,
                       COALESCE(NULLIF(u.phone, ''), s.phone, '') AS phone,
                       COALESCE(NULLIF(u.address, ''), s.address, '') AS address,
                       s.first_name, s.last_name
                FROM users u
                LEFT JOIN students s ON u.student_code = s.student_id
                WHERE u.student_code=%s
                """,
                (student_code,),
            )
            user = cursor.fetchone()
            if user:
                user["student_id"] = user["student_code"]
                user["phone"] = user.get("phone") or ""
                user["address"] = user.get("address") or ""
        finally:
            db.close()
        return render_template("edit_profile.html", user=user)

    @app.route("/study/planner")
    @login_required
    def study_planner():
        db = get_db()
        cursor = db.cursor()
        try:
            n_id = resolve_student_numeric_id(cursor, session["user_id"])
            tasks = []
            if n_id:
                cursor.execute("SELECT * FROM student_tasks WHERE user_id=%s ORDER BY deadline ASC", (n_id,))
                tasks = cursor.fetchall()
        finally:
            db.close()
        return render_template("study_planner.html", tasks=tasks)

    @app.route("/api/study/add-task", methods=["POST"])
    @login_required
    def add_task():
        data = request.get_json(silent=True) or {}
        db = get_db()
        cursor = db.cursor()
        try:
            n_id = resolve_student_numeric_id(cursor, session["user_id"])
            if not n_id:
                return jsonify({"success": False, "error": "Student account not found"}), 404
            cursor.execute(
                "INSERT INTO student_tasks (user_id, title, deadline, status, priority) VALUES (%s, %s, %s, 'Pending', %s)",
                (n_id, data.get("title", ""), data.get("deadline", ""), data.get("priority", "Medium")),
            )
            db.commit()
        finally:
            db.close()
        return jsonify({"success": True})

    @app.route("/api/study/toggle-task/<int:task_id>", methods=["POST"])
    @login_required
    def toggle_task(task_id):
        db = get_db()
        cursor = db.cursor()
        try:
            n_id = resolve_student_numeric_id(cursor, session["user_id"])
            if not n_id:
                return jsonify({"success": False}), 404
            cursor.execute("SELECT status FROM student_tasks WHERE id=%s AND user_id=%s", (task_id, n_id))
            row = cursor.fetchone()
            if not row:
                return jsonify({"success": False, "error": "Task not found"}), 404
            new_status = "Pending" if (row.get("status") or "").lower() == "completed" else "Completed"
            cursor.execute("UPDATE student_tasks SET status=%s WHERE id=%s AND user_id=%s", (new_status, task_id, n_id))
            db.commit()
        finally:
            db.close()
        return jsonify({"success": True, "status": new_status})

    @app.route("/api/study/edit-task/<int:task_id>", methods=["POST"])
    @login_required
    def edit_study_task(task_id):
        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "").strip()
        deadline = (data.get("deadline") or "").strip()
        priority = (data.get("priority") or "Medium").strip()
        if not title:
            return jsonify({"success": False, "error": "Title is required."}), 400
        db = get_db()
        cursor = db.cursor()
        try:
            n_id = resolve_student_numeric_id(cursor, session["user_id"])
            if not n_id:
                return jsonify({"success": False}), 404
            cursor.execute(
                "UPDATE student_tasks SET title=%s, deadline=%s, priority=%s WHERE id=%s AND user_id=%s",
                (title, deadline, priority, task_id, n_id),
            )
            db.commit()
        finally:
            db.close()
        return jsonify({"success": True, "title": title, "deadline": deadline, "priority": priority})

    @app.route("/api/study/delete-task/<int:task_id>", methods=["POST"])
    @login_required
    def delete_task(task_id):
        db = get_db()
        cursor = db.cursor()
        try:
            n_id = resolve_student_numeric_id(cursor, session["user_id"])
            if n_id:
                cursor.execute("DELETE FROM student_tasks WHERE id=%s AND user_id=%s", (task_id, n_id))
                db.commit()
        finally:
            db.close()
        return jsonify({"success": True})

    @app.route("/instructor/feedback")
    @login_required
    def instructor_feedback():
        db = get_db()
        cursor = db.cursor()
        try:
            n_id = resolve_student_numeric_id(cursor, session["user_id"])
            uni_id = _student_uni_id(cursor, n_id)
            cursor.execute(
                "SELECT * FROM instructors WHERE university_id = %s OR university_id IS NULL ORDER BY first_name ASC",
                (uni_id,),
            )
            instructors = cursor.fetchall()
            for ins in instructors:
                ins["full_name"] = " ".join(p for p in [ins.get("title"), ins.get("first_name"), ins.get("last_name")] if p)

            cursor.execute(
                """
                SELECT r.*, i.title, i.first_name, i.last_name
                FROM instructor_ratings r
                JOIN instructors i ON r.instructor_id = i.instructor_id
                WHERE i.university_id = %s OR i.university_id IS NULL
                ORDER BY r.created_at DESC LIMIT 10
                """,
                (uni_id,),
            )
            recent_ratings = cursor.fetchall()
            for rr in recent_ratings:
                rr["instructor_name"] = " ".join(p for p in [rr.get("title"), rr.get("first_name"), rr.get("last_name")] if p)
        finally:
            db.close()
        return render_template("instructor_feedback.html", instructors=instructors, recent_ratings=recent_ratings)

    @app.route("/api/instructor/rate", methods=["POST"])
    @login_required
    def rate_instructor():
        data = request.get_json(silent=True) or {}
        db = get_db()
        cursor = db.cursor()
        updated = False
        try:
            n_id = resolve_student_numeric_id(cursor, session["user_id"])
            if not n_id:
                return jsonify({"success": False, "error": "Student not found"}), 404
            inst_id = data.get("instructor_id")
            rating_val = max(1, min(5, int(data.get("rating", 5))))
            comment = (data.get("comment") or "").strip()

            # Prevent duplicate spam ratings: update student's existing rating for this instructor
            cursor.execute(
                "SELECT id FROM instructor_ratings WHERE student_id=%s AND instructor_id=%s LIMIT 1",
                (n_id, inst_id),
            )
            existing = cursor.fetchone()
            if existing:
                cursor.execute(
                    "UPDATE instructor_ratings SET rating=%s, comment=%s WHERE id=%s",
                    (rating_val, comment, existing["id"]),
                )
                updated = True
            else:
                cursor.execute(
                    "INSERT INTO instructor_ratings (student_id, instructor_id, rating, comment) VALUES (%s, %s, %s, %s)",
                    (n_id, inst_id, rating_val, comment),
                )
            db.commit()
        finally:
            db.close()
        return jsonify({"success": True, "updated": updated})

    @app.route("/api/search")
    @login_required
    def global_search():
        q = request.args.get("q", "").lower().strip()
        if not q:
            return jsonify([])

        db = get_db()
        cursor = db.cursor()
        results = []
        try:
            n_id = resolve_student_numeric_id(cursor, session["user_id"])
            if n_id:
                cursor.execute(
                    "SELECT id, title, category FROM student_notes WHERE user_id=%s AND (title LIKE %s OR content LIKE %s)",
                    (n_id, f"%{q}%", f"%{q}%"),
                )
                for n in cursor.fetchall():
                    results.append({"type": "note", "title": n["title"], "cat": n["category"], "url": url_for("my_notes") + f"#note-{n['id']}"})

                cursor.execute("SELECT DISTINCT subject_name FROM schedules WHERE user_id=%s AND subject_name LIKE %s", (n_id, f"%{q}%"))
                for s in cursor.fetchall():
                    results.append({"type": "subject", "title": s["subject_name"], "cat": "Schedule", "url": url_for("schedule_page")})

                cursor.execute("SELECT DISTINCT subject_name FROM exams WHERE user_id=%s AND subject_name LIKE %s", (n_id, f"%{q}%"))
                for e in cursor.fetchall():
                    results.append({"type": "exam", "title": e["subject_name"], "cat": "Exam", "url": url_for("exams_page")})
        finally:
            db.close()
        return jsonify(results[:10])
