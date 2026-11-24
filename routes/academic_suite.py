import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from flask import render_template, request, session, redirect, url_for, flash
from core import config
from core.db import get_db, resolve_student_numeric_id
from core.security import login_required, roles_required, rate_limit, log_audit

ALLOWED_SUBMISSION_EXTS = {".pdf", ".doc", ".docx", ".zip", ".txt", ".png", ".jpg"}


def _sign_transcript(student_code: str) -> str:
    return hmac.new(
        config.SECRET_KEY.encode("utf-8"),
        f"transcript:{student_code}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:24]


def _resolve_inst_id(cursor) -> int:
    user_val = str(session.get("user_id", ""))
    if session.get("real_instructor_id"):
        return int(session["real_instructor_id"])
    if user_val.startswith("ins_"):
        aid = user_val.split("_", 1)[1]
        cursor.execute("SELECT email FROM admins WHERE id=%s", (aid,))
        cursor.execute("SELECT email, university_id FROM admins WHERE id=%s", (aid,))
        a = cursor.fetchone()
        if a:
            cursor.execute("SELECT instructor_id FROM instructors WHERE email=%s", (a["email"],))
            if a.get("university_id") and not session.get("university_id"):
                session["university_id"] = a["university_id"]
            cursor.execute("SELECT instructor_id FROM instructors WHERE LOWER(email)=LOWER(%s)", (a["email"],))
            ins = cursor.fetchone()
            if ins:
                return int(ins["instructor_id"])
    if session.get("university_id"):
        cursor.execute("SELECT instructor_id FROM instructors WHERE university_id=%s ORDER BY instructor_id ASC LIMIT 1", (int(session["university_id"]),))
        ins = cursor.fetchone()
        if ins:
            return int(ins["instructor_id"])
    return 1


def _active_scope_uni_id():
    """Return university_id scope for University Admins (tenant_scope) or Instructors; None for Super Admin."""
    if session.get("role") == "system":
        ts = session.get("tenant_scope")
        return int(ts) if ts else None
    uid = session.get("university_id")
    return int(uid) if uid else None


def register_academic_suite_routes(app):
    # ==========================================================
    # 1. SMART QR ATTENDANCE & 25% DEPRIVATION SYSTEM
    # ==========================================================
    @app.route("/attendance")
    @login_required
    def student_attendance():
        db = get_db()
        cursor = db.cursor()
        try:
            nid = resolve_student_numeric_id(cursor, session["user_id"])
            summary = []
            recent_records = []
            if nid:
                cursor.execute("SELECT * FROM attendance_summary WHERE user_id=%s ORDER BY id ASC", (nid,))
                summary = cursor.fetchall()
                for row in summary:
                    total = max(1, int(row.get("total_lectures") or 12))
                    absences = int(row.get("absences") or 0)
                    absence_pct = round((absences / total) * 100, 1)
                    row["absence_pct"] = absence_pct
                    row["present_pct"] = round(max(0.0, 100.0 - absence_pct), 1)
                    if absence_pct >= 25:
                        row["risk"] = "deprived"
                    elif absence_pct >= 18:
                        row["risk"] = "warning"
                    else:
                        row["risk"] = "safe"

                cursor.execute(
                    """
                    SELECT ar.*, ats.course_id, ats.course_name, ats.title as session_title
                    FROM attendance_records ar
                    JOIN attendance_sessions ats ON ar.session_id = ats.id
                    WHERE ar.user_id = %s
                    ORDER BY ar.checked_in_at DESC LIMIT 15
                    """,
                    (nid,),
                )
                recent_records = cursor.fetchall()
        finally:
            db.close()

        prefill_code = (request.args.get("code") or "").strip().upper()
        return render_template(
            "student_attendance.html",
            summary=summary,
            recent_records=recent_records,
            prefill_code=prefill_code,
        )

    @app.route("/attendance/checkin", methods=["GET", "POST"])
    @login_required
    def attendance_checkin():
        code = (request.values.get("code") or "").strip().upper()
        if not code:
            flash("Please enter a valid 6-character session code.", "danger")
            return redirect(url_for("student_attendance"))

        db = get_db()
        cursor = db.cursor()
        try:
            nid = resolve_student_numeric_id(cursor, session["user_id"])
            if not nid:
                flash("Only student accounts can check into lectures.", "warning")
                return redirect(url_for("dashboard"))

            cursor.execute("SELECT * FROM attendance_sessions WHERE session_code=%s ORDER BY id DESC LIMIT 1", (code,))
            att_session = cursor.fetchone()
            if not att_session:
                flash("Invalid attendance session code.", "danger")
                return redirect(url_for("student_attendance"))

            # Cross-university session code check
            st_uni = session.get("university_id")
            if st_uni and att_session.get("university_id") and int(att_session["university_id"]) != int(st_uni):
                flash("This attendance code belongs to another university.", "danger")
                return redirect(url_for("student_attendance"))

            expires = att_session.get("expires_at")
            if expires:
                exp_dt = expires if isinstance(expires, datetime) else datetime.fromisoformat(str(expires))
                if datetime.now() > exp_dt:
                    flash("This QR attendance session has expired.", "danger")
                    return redirect(url_for("student_attendance"))

            cursor.execute(
                "SELECT id FROM attendance_records WHERE session_id=%s AND user_id=%s",
                (att_session["id"], nid),
            )
            if cursor.fetchone():
                flash("You have already checked in to this lecture!", "warning")
                return redirect(url_for("student_attendance"))

            cursor.execute(
                "INSERT INTO attendance_records (session_id, user_id, student_code, status) VALUES (%s, %s, %s, 'Present')",
                (att_session["id"], nid, session["user_id"]),
            )

            # Sync attendance_summary
            subj = att_session.get("course_name") or att_session["course_id"]
            cursor.execute("SELECT * FROM attendance_summary WHERE user_id=%s AND subject_name=%s", (nid, subj))
            existing = cursor.fetchone()
            if existing:
                cursor.execute(
                    "UPDATE attendance_summary SET total_lectures = total_lectures + 1 WHERE id=%s",
                    (existing["id"],),
                )
            else:
                cursor.execute(
                    "INSERT INTO attendance_summary (user_id, subject_name, total_lectures, absences, max_absences) VALUES (%s, %s, 1, 0, 3)",
                    (nid, subj),
                )

            db.commit()
            log_audit("ATTENDANCE_CHECKIN", "attendance_session", att_session["id"], f"Student {session['user_id']} checked in ({code})")
            flash(f"Checked in successfully to {subj}!", "success")
        finally:
            db.close()
        return redirect(url_for("student_attendance"))

    @app.route("/instructor/attendance", methods=["GET", "POST"])
    @roles_required("instructor", "system")
    def instructor_attendance():
        db = get_db()
        cursor = db.cursor()
        try:
            real_id = _resolve_inst_id(cursor)
            scope_uni = _active_scope_uni_id()

            if session.get("role") == "system":
                cursor.execute("SELECT course_id, name FROM courses ORDER BY course_id ASC")
                if scope_uni:
                    cursor.execute("SELECT course_id, name FROM courses WHERE university_id=%s ORDER BY course_id ASC", (scope_uni,))
                else:
                    cursor.execute("SELECT course_id, name FROM courses ORDER BY course_id ASC")
            else:
                cursor.execute("SELECT course_id, name FROM courses WHERE instructor_id=%s ORDER BY course_id ASC", (real_id,))
            courses = cursor.fetchall()

            if request.method == "POST":
                action = request.form.get("action", "create_session")
                if action == "create_session":
                    course_id = (request.form.get("course_id") or "").strip()
                    title = (request.form.get("title") or "Lecture Session").strip()
                    duration_mins = max(5, min(180, int(request.form.get("duration", 15))))

                    cursor.execute("SELECT name FROM courses WHERE course_id=%s", (course_id,))
                    cursor.execute("SELECT name, university_id FROM courses WHERE course_id=%s", (course_id,))
                    c_row = cursor.fetchone()
                    course_name = c_row["name"] if c_row else course_id
                    sess_uni = (c_row or {}).get("university_id") or scope_uni or 3

                    code = secrets.token_hex(3).upper()
                    expires_at = (datetime.now() + timedelta(minutes=duration_mins)).strftime("%Y-%m-%d %H:%M:%S")
                    cursor.execute(
                        """
                        INSERT INTO attendance_sessions (course_id, course_name, instructor_id, session_code, title, expires_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        INSERT INTO attendance_sessions (course_id, course_name, instructor_id, session_code, title, expires_at, university_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (course_id, course_name, real_id, code, title, expires_at),
                        (course_id, course_name, real_id, code, title, expires_at, sess_uni),
                    )
                    db.commit()
                    log_audit("ATTENDANCE_SESSION_CREATED", "course", course_id, f"Code {code} valid {duration_mins}m")
                    flash(f"Live QR session opened! Code: {code} (valid {duration_mins} mins)", "success")
                    return redirect(url_for("instructor_attendance"))

                if action == "mark_absence":
                    student_uid = int(request.form.get("user_id"))
                    subject_name = (request.form.get("subject_name") or "").strip()
                    cursor.execute(
                        "SELECT * FROM attendance_summary WHERE user_id=%s AND subject_name=%s",
                        (student_uid, subject_name),
                    )
                    row = cursor.fetchone()
                    if row:
                        new_abs = int(row["absences"] or 0) + 1
                        total = max(1, int(row["total_lectures"] or 12))
                        cursor.execute("UPDATE attendance_summary SET absences=%s WHERE id=%s", (new_abs, row["id"]))
                    else:
                        new_abs, total = 1, 12
                        cursor.execute(
                            "INSERT INTO attendance_summary (user_id, subject_name, total_lectures, absences, max_absences) VALUES (%s, %s, 12, 1, 3)",
                            (student_uid, subject_name),
                        )

                    # Auto-send deprivation warning notification if >= 20%
                    pct = round((new_abs / total) * 100, 1)
                    if pct >= 25:
                        cursor.execute(
                            "INSERT INTO notifications (user_id, title, message, type, status) VALUES (%s, %s, %s, 'alert', 'unread')",
                            (student_uid, f"Deprivation Alert: {subject_name}", f"You reached {pct}% absence ({new_abs}/{total} lectures). Contact Student Affairs immediately."),
                        )
                    elif pct >= 18:
                        cursor.execute(
                            "INSERT INTO notifications (user_id, title, message, type, status) VALUES (%s, %s, %s, 'warning', 'unread')",
                            (student_uid, f"Attendance Warning: {subject_name}", f"Your absence rate is {pct}% ({new_abs}/{total}). Reaching 25% results in exam deprivation."),
                        )
                    db.commit()
                    log_audit("ABSENCE_RECORDED", "student", student_uid, f"{subject_name}: {new_abs}/{total} ({pct}%)")
                    flash("Absence recorded and student warning status updated!", "success")
                    return redirect(url_for("instructor_attendance"))

            # Fetch recent sessions with check-in counts
            # Fetch recent sessions with check-in counts (tenant-scoped)
            if session.get("role") == "system":
                cursor.execute("SELECT * FROM attendance_sessions ORDER BY id DESC LIMIT 10")
                if scope_uni:
                    cursor.execute("SELECT * FROM attendance_sessions WHERE university_id=%s ORDER BY id DESC LIMIT 10", (scope_uni,))
                else:
                    cursor.execute("SELECT * FROM attendance_sessions ORDER BY id DESC LIMIT 10")
            else:
                cursor.execute("SELECT * FROM attendance_sessions WHERE instructor_id=%s ORDER BY id DESC LIMIT 10", (real_id,))
            sessions_list = cursor.fetchall()
            now = datetime.now()
            for s in sessions_list:
                cursor.execute("SELECT COUNT(*) as c FROM attendance_records WHERE session_id=%s", (s["id"],))
                s["checkin_count"] = cursor.fetchone()["c"]
                exp = s.get("expires_at")
                try:
                    exp_dt = exp if isinstance(exp, datetime) else datetime.fromisoformat(str(exp))
                    s["is_active"] = now <= exp_dt
                except Exception:
                    s["is_active"] = False
                s["checkin_url"] = url_for("attendance_checkin", code=s["session_code"], _external=True)

            # Fetch students for quick absence recording
            cursor.execute("SELECT id, full_name, student_code FROM users ORDER BY id ASC LIMIT 30")
            # Fetch students for quick absence recording (tenant-scoped)
            if scope_uni:
                cursor.execute("SELECT id, full_name, student_code FROM users WHERE university_id=%s ORDER BY id ASC LIMIT 50", (scope_uni,))
            else:
                cursor.execute("SELECT id, full_name, student_code FROM users ORDER BY id ASC LIMIT 50")
            students_list = cursor.fetchall()
        finally:
            db.close()

        return render_template(
            "instructor_attendance.html",
            courses=courses,
            sessions_list=sessions_list,
            students_list=students_list,
        )

    # ==========================================================
    # 2. OFFICIAL VERIFIED TRANSCRIPT + QR VERIFICATION
    # ==========================================================
    @app.route("/transcript")
    @login_required
    def official_transcript():
        student_code = session["user_id"]
        db = get_db()
        cursor = db.cursor()
        try:
            nid = resolve_student_numeric_id(cursor, student_code)
            cursor.execute("SELECT * FROM users WHERE id=%s", (nid,)) if nid else None
            user = cursor.fetchone() if nid else None
            if not user:
                flash("Transcript is available for student accounts.", "warning")
                return redirect(url_for("dashboard"))

            uni = None
            if user.get("university_id"):
                cursor.execute("SELECT * FROM universities WHERE id=%s", (user["university_id"],))
                uni = cursor.fetchone()

            cursor.execute(
                "SELECT * FROM student_grades WHERE user_id=%s ORDER BY academic_year ASC, semester ASC",
                (nid,),
            )
            all_grades = cursor.fetchall()
        finally:
            db.close()

        terms = {}
        total_credits, total_quality = 0, 0.0
        for g in all_grades:
            key = f"{g['academic_year']} — {g['semester']}"
            terms.setdefault(key, []).append(g)
            try:
                c = int(g["credit_hours"])
                p = float(g["points"])
                total_credits += c
                total_quality += c * p
            except Exception:
                pass

        calc_gpa = round(total_quality / total_credits, 2) if total_credits > 0 else float(user.get("gpa") or 0)
        sig = _sign_transcript(user["student_code"])
        verify_url = url_for("verify_transcript", student_code=user["student_code"], sig=sig, _external=True)

        return render_template(
            "transcript.html",
            user=user,
            uni=uni,
            terms=terms,
            total_credits=total_credits,
            calc_gpa=calc_gpa,
            verify_url=verify_url,
            sig=sig,
            issued_date=datetime.now().strftime("%Y-%m-%d"),
        )

    @app.route("/verify/transcript/<student_code>")
    def verify_transcript(student_code):
        sig = (request.args.get("sig") or "").strip()
        expected = _sign_transcript(student_code)
        is_valid = bool(sig and hmac.compare_digest(sig, expected))

        user, uni, total_courses = None, None, 0
        if is_valid:
            db = get_db()
            cursor = db.cursor()
            try:
                cursor.execute("SELECT id, full_name, student_code, faculty, level, gpa, university_id FROM users WHERE student_code=%s", (student_code,))
                user = cursor.fetchone()
                if user:
                    if user.get("university_id"):
                        cursor.execute("SELECT name, primary_color FROM universities WHERE id=%s", (user["university_id"],))
                        uni = cursor.fetchone()
                    cursor.execute("SELECT COUNT(*) as c FROM student_grades WHERE user_id=%s", (user["id"],))
                    total_courses = cursor.fetchone()["c"]
                else:
                    is_valid = False
            finally:
                db.close()

        return render_template(
            "verify_transcript.html",
            is_valid=is_valid,
            student_code=student_code,
            user=user,
            uni=uni,
            total_courses=total_courses,
        )

    # ==========================================================
    # 3. BROADCAST ANNOUNCEMENTS & PUSH NOTIFICATIONS
    # ==========================================================
    @app.route("/announcements", methods=["GET", "POST"])
    @roles_required("system", "instructor")
    def announcements_page():
        scope_uni = _active_scope_uni_id()
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("SELECT id, name FROM universities ORDER BY name ASC")
            universities = cursor.fetchall()
            cursor.execute("SELECT DISTINCT faculty FROM users WHERE faculty IS NOT NULL AND faculty != '' ORDER BY faculty")
            if scope_uni:
                cursor.execute("SELECT id, name FROM universities WHERE id=%s", (scope_uni,))
                universities = cursor.fetchall()
                cursor.execute(
                    "SELECT DISTINCT faculty FROM users WHERE university_id=%s AND faculty IS NOT NULL AND faculty != '' ORDER BY faculty",
                    (scope_uni,),
                )
            else:
                cursor.execute("SELECT id, name FROM universities ORDER BY name ASC")
                universities = cursor.fetchall()
                cursor.execute("SELECT DISTINCT faculty FROM users WHERE faculty IS NOT NULL AND faculty != '' ORDER BY faculty")
            faculties = [r["faculty"] for r in cursor.fetchall()]

            if session.get("role") == "system":
                cursor.execute("SELECT course_id, name FROM courses ORDER BY course_id ASC")
                if scope_uni:
                    cursor.execute("SELECT course_id, name FROM courses WHERE university_id=%s ORDER BY course_id ASC", (scope_uni,))
                else:
                    cursor.execute("SELECT course_id, name FROM courses ORDER BY course_id ASC")
            else:
                real_id = _resolve_inst_id(cursor)
                cursor.execute("SELECT course_id, name FROM courses WHERE instructor_id=%s ORDER BY course_id ASC", (real_id,))
            courses = cursor.fetchall()

            if request.method == "POST":
                title = (request.form.get("title") or "").strip()
                message = (request.form.get("message") or "").strip()
                priority = (request.form.get("priority") or "normal").strip()
                target_scope = (request.form.get("target_scope") or "all").strip()
                target_value = (request.form.get("target_value") or "").strip()

                if not title or not message:
                    flash("Title and message are required.", "danger")
                    return redirect(url_for("announcements_page"))

                # Resolve recipient student numeric IDs
                # Resolve recipient student numeric IDs (strictly within scope_uni if scoped!)
                if target_scope == "university" and target_value.isdigit():
                    cursor.execute("SELECT id FROM users WHERE university_id=%s", (int(target_value),))
                    eff_uni = scope_uni if scope_uni else int(target_value)
                    cursor.execute("SELECT id FROM users WHERE university_id=%s", (eff_uni,))
                elif target_scope == "faculty" and target_value:
                    cursor.execute("SELECT id FROM users WHERE faculty=%s", (target_value,))
                    if scope_uni:
                        cursor.execute("SELECT id FROM users WHERE faculty=%s AND university_id=%s", (target_value, scope_uni))
                    else:
                        cursor.execute("SELECT id FROM users WHERE faculty=%s", (target_value,))
                elif target_scope == "course" and target_value:
                    cursor.execute(
                        "SELECT DISTINCT user_id as id FROM student_grades WHERE course_code=%s",
                        (target_value,),
                    )
                    if scope_uni:
                        cursor.execute(
                            "SELECT DISTINCT sg.user_id as id FROM student_grades sg JOIN users u ON sg.user_id=u.id WHERE sg.course_code=%s AND u.university_id=%s",
                            (target_value, scope_uni),
                        )
                    else:
                        cursor.execute("SELECT DISTINCT user_id as id FROM student_grades WHERE course_code=%s", (target_value,))
                else:
                    target_scope = "all"
                    cursor.execute("SELECT id FROM users")
                    target_scope = "university" if scope_uni else "all"
                    if scope_uni:
                        target_value = str(scope_uni)
                        cursor.execute("SELECT id FROM users WHERE university_id=%s", (scope_uni,))
                    else:
                        cursor.execute("SELECT id FROM users")

                recipient_ids = [r["id"] for r in cursor.fetchall()]
                for uid in recipient_ids:
                    cursor.execute(
                        "INSERT INTO notifications (user_id, title, message, type, status) VALUES (%s, %s, %s, %s, 'unread')",
                        (uid, title, message, priority),
                    )

                cursor.execute(
                    """
                    INSERT INTO announcements (sender_id, sender_name, sender_role, target_scope, target_value, title, message, priority, recipients_count)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        session.get("user_id"),
                        session.get("name"),
                        session.get("role"),
                        target_scope,
                        target_value,
                        title,
                        message,
                        priority,
                        len(recipient_ids),
                    ),
                )
                db.commit()
                log_audit("ANNOUNCEMENT_BROADCAST", target_scope, target_value or "all", f"'{title}' sent to {len(recipient_ids)} students")
                flash(f"Announcement broadcasted to {len(recipient_ids)} students!", "success")
                return redirect(url_for("announcements_page"))

            cursor.execute("SELECT * FROM announcements ORDER BY id DESC LIMIT 30")
            if scope_uni:
                cursor.execute(
                    "SELECT * FROM announcements WHERE sender_id=%s OR (target_scope='university' AND target_value=%s) ORDER BY id DESC LIMIT 30",
                    (session.get("user_id"), str(scope_uni)),
                )
            else:
                cursor.execute("SELECT * FROM announcements ORDER BY id DESC LIMIT 30")
            history = cursor.fetchall()
        finally:
            db.close()

        base_layout = "system_layout.html" if session.get("role") == "system" else "instructor_layout.html"
        return render_template(
            "announcements.html",
            base_layout=base_layout,
            universities=universities,
            faculties=faculties,
            courses=courses,
            history=history,
        )

    # ==========================================================
    # 4. MINI-LMS ASSIGNMENTS & SUBMISSIONS
    # ==========================================================
    def _is_due_passed(due_val) -> bool:
        if not due_val:
            return False
        s = str(due_val).strip()
        if not s:
            return False
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s[:19], fmt)
                if fmt == "%Y-%m-%d":
                    dt = dt.replace(hour=23, minute=59, second=59)
                return datetime.now() > dt
            except ValueError:
                continue
        return False

    @app.route("/assignments")
    @login_required
    def student_assignments():
        db = get_db()
        cursor = db.cursor()
        try:
            nid = resolve_student_numeric_id(cursor, session["user_id"])
            uni_id = session.get("university_id")
            if not uni_id and nid:
                cursor.execute("SELECT university_id FROM users WHERE id=%s", (nid,))
                u = cursor.fetchone()
                uni_id = (u or {}).get("university_id")
            uni_id = int(uni_id or 3)

            cursor.execute(
                """
                SELECT a.*, c.name as course_name
                FROM assignments a
                LEFT JOIN courses c ON a.course_id = c.course_id
                WHERE a.university_id = %s OR a.university_id IS NULL
                ORDER BY a.id DESC
                """,
                (uni_id,),
            )
            items = cursor.fetchall()

            sub_map = {}
            if nid:
                cursor.execute("SELECT * FROM assignment_submissions WHERE user_id=%s", (nid,))
                for s in cursor.fetchall():
                    sub_map[s["assignment_id"]] = s

            for item in items:
                item["submission"] = sub_map.get(item["id"])
                item["is_closed"] = _is_due_passed(item.get("due_date"))
        finally:
            db.close()
        return render_template("student_assignments.html", assignments=items)

    @app.route("/assignments/<int:assignment_id>/submit", methods=["POST"])
    @login_required
    def submit_assignment(assignment_id):
        db = get_db()
        cursor = db.cursor()
        try:
            nid = resolve_student_numeric_id(cursor, session["user_id"])
            if not nid:
                flash("Only student accounts can submit assignments.", "danger")
                return redirect(url_for("student_assignments"))

            cursor.execute("SELECT id, due_date, title FROM assignments WHERE id=%s", (assignment_id,))
            asg = cursor.fetchone()
            if not asg:
                flash("Assignment not found.", "danger")
                return redirect(url_for("student_assignments"))
            if _is_due_passed(asg.get("due_date")):
                flash(f"Submission locked: the deadline ({asg['due_date']}) for '{asg['title']}' has passed.", "danger")
                return redirect(url_for("student_assignments"))

            note = (request.form.get("note") or "").strip()
            file_path = None
            f = request.files.get("submission_file")
            if f and f.filename:
                ext = os.path.splitext(f.filename)[1].lower()
                if ext not in ALLOWED_SUBMISSION_EXTS:
                    flash("Unsupported file format. Please upload PDF, DOCX, ZIP, TXT, or image.", "danger")
                    return redirect(url_for("student_assignments"))
                fname = f"sub_{assignment_id}_{session['user_id']}_{int(time.time())}_{secure_filename(f.filename)}"
                uploads_dir = os.path.join(app.static_folder, "uploads")
                os.makedirs(uploads_dir, exist_ok=True)
                f.save(os.path.join(uploads_dir, fname))
                file_path = fname

            cursor.execute(
                "SELECT id FROM assignment_submissions WHERE assignment_id=%s AND user_id=%s",
                (assignment_id, nid),
            )
            existing = cursor.fetchone()
            if existing:
                if file_path:
                    cursor.execute(
                        "UPDATE assignment_submissions SET file_path=%s, note=%s, submitted_at=NOW() WHERE id=%s",
                        (file_path, note, existing["id"]),
                    )
                else:
                    cursor.execute(
                        "UPDATE assignment_submissions SET note=%s, submitted_at=NOW() WHERE id=%s",
                        (note, existing["id"]),
                    )
            else:
                cursor.execute(
                    """
                    INSERT INTO assignment_submissions (assignment_id, user_id, student_code, file_path, note)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (assignment_id, nid, session["user_id"], file_path, note),
                )
            db.commit()
            log_audit("ASSIGNMENT_SUBMITTED", "assignment", assignment_id, f"Student {session['user_id']} submitted work")
            flash("Assignment submitted successfully!", "success")
        finally:
            db.close()
        return redirect(url_for("student_assignments"))

    @app.route("/instructor/assignments", methods=["GET", "POST"])
    @roles_required("instructor", "system")
    def instructor_assignments():
        db = get_db()
        cursor = db.cursor()
        try:
            real_id = _resolve_inst_id(cursor)
            scope_uni = _active_scope_uni_id()
            if session.get("role") == "system":
                cursor.execute("SELECT course_id, name FROM courses ORDER BY course_id ASC")
                if scope_uni:
                    cursor.execute("SELECT course_id, name FROM courses WHERE university_id=%s ORDER BY course_id ASC", (scope_uni,))
                else:
                    cursor.execute("SELECT course_id, name FROM courses ORDER BY course_id ASC")
            else:
                cursor.execute("SELECT course_id, name FROM courses WHERE instructor_id=%s ORDER BY course_id ASC", (real_id,))
            courses = cursor.fetchall()

            if request.method == "POST":
                course_id = (request.form.get("course_id") or "").strip()
                title = (request.form.get("title") or "").strip()
                description = (request.form.get("description") or "").strip()
                due_date = (request.form.get("due_date") or "").strip()
                max_score = int(request.form.get("max_score") or 100)

                if not course_id or not title:
                    flash("Course and assignment title are required.", "danger")
                    return redirect(url_for("instructor_assignments"))

                uni_id = int(session.get("university_id") or 3)
                cursor.execute(
                    """
                    INSERT INTO assignments (course_id, instructor_id, title, description, due_date, max_score, university_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (course_id, real_id, title, description, due_date, max_score, uni_id),
                )
                db.commit()
                log_audit("ASSIGNMENT_CREATED", "course", course_id, f"Created assignment '{title}' (Due {due_date})")
                flash(f"Assignment '{title}' published!", "success")
                return redirect(url_for("instructor_assignments"))

            if session.get("role") == "system":
                cursor.execute("SELECT a.*, c.name as course_name FROM assignments a LEFT JOIN courses c ON a.course_id = c.course_id ORDER BY a.id DESC")
                if scope_uni:
                    cursor.execute(
                        "SELECT a.*, c.name as course_name FROM assignments a LEFT JOIN courses c ON a.course_id = c.course_id WHERE a.university_id=%s ORDER BY a.id DESC",
                        (scope_uni,),
                    )
                else:
                    cursor.execute("SELECT a.*, c.name as course_name FROM assignments a LEFT JOIN courses c ON a.course_id = c.course_id ORDER BY a.id DESC")
            else:
                cursor.execute(
                    "SELECT a.*, c.name as course_name FROM assignments a LEFT JOIN courses c ON a.course_id = c.course_id WHERE a.instructor_id=%s ORDER BY a.id DESC",
                    (real_id,),
                )
            my_assignments = cursor.fetchall()

            cursor.execute(
                """
                SELECT s.*, u.full_name as student_name, a.title as assignment_title, a.course_id, a.max_score
                FROM assignment_submissions s
                JOIN users u ON s.user_id = u.id
                JOIN assignments a ON s.assignment_id = a.id
                ORDER BY s.submitted_at DESC LIMIT 50
                """
            )
            if scope_uni:
                cursor.execute(
                    """
                    SELECT s.*, u.full_name as student_name, a.title as assignment_title, a.course_id, a.max_score
                    FROM assignment_submissions s
                    JOIN users u ON s.user_id = u.id
                    JOIN assignments a ON s.assignment_id = a.id
                    WHERE u.university_id = %s
                    ORDER BY s.submitted_at DESC LIMIT 50
                    """,
                    (scope_uni,),
                )
            else:
                cursor.execute(
                    """
                    SELECT s.*, u.full_name as student_name, a.title as assignment_title, a.course_id, a.max_score
                    FROM assignment_submissions s
                    JOIN users u ON s.user_id = u.id
                    JOIN assignments a ON s.assignment_id = a.id
                    ORDER BY s.submitted_at DESC LIMIT 50
                    """
                )
            submissions = cursor.fetchall()
        finally:
            db.close()

        return render_template(
            "instructor_assignments.html",
            courses=courses,
            assignments=my_assignments,
            submissions=submissions,
        )

    @app.route("/instructor/submissions/<int:submission_id>/grade", methods=["POST"])
    @roles_required("instructor", "system")
    def grade_submission(submission_id):
        score = float(request.form.get("score") or 0)
        feedback = (request.form.get("feedback") or "").strip()

        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute(
                """
                SELECT s.user_id, a.title
                FROM assignment_submissions s
                JOIN assignments a ON s.assignment_id = a.id
                WHERE s.id=%s
                """,
                (submission_id,),
            )
            row = cursor.fetchone()
            if row:
                cursor.execute(
                    "UPDATE assignment_submissions SET score=%s, feedback=%s, graded_at=NOW() WHERE id=%s",
                    (score, feedback, submission_id),
                )
                cursor.execute(
                    "INSERT INTO notifications (user_id, title, message, type, status) VALUES (%s, %s, %s, 'grade', 'unread')",
                    (row["user_id"], f"Assignment Graded: {row['title']}", f"You scored {score}. Feedback: {feedback or 'Great work!'}"),
                )
                db.commit()
                log_audit("ASSIGNMENT_GRADED", "submission", submission_id, f"Score={score} for '{row['title']}'")
                flash("Submission graded and student notified!", "success")
        finally:
            db.close()
        return redirect(url_for("instructor_assignments"))

    # ==========================================================
    # 5. B2B UNIVERSITY DEMO REQUESTS (SALES PIPELINE)
    # ==========================================================
    @app.route("/demo-request", methods=["POST"])
    @rate_limit(max_requests=5, window_seconds=60, key_prefix="demo_req")
    def submit_demo_request():
        uni_name = (request.form.get("university_name") or "").strip()
        contact_name = (request.form.get("contact_name") or "").strip()
        role_title = (request.form.get("role_title") or "").strip()
        email = (request.form.get("email") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        student_count = (request.form.get("student_count") or "").strip()
        notes = (request.form.get("notes") or "").strip()

        if not uni_name or not contact_name or not email:
            flash("Please complete required university contact fields.", "danger")
            return redirect(url_for("landing") + "#universities")

        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO demo_requests (university_name, contact_name, role_title, email, phone, student_count, notes, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'New')
                """,
                (uni_name, contact_name, role_title, email, phone, student_count, notes),
            )
            db.commit()
            log_audit("B2B_DEMO_REQUESTED", "university_lead", uni_name, f"Contact: {contact_name} ({email})")
        finally:
            db.close()

        flash("Thank you! Your university demo request has been received. Our team will reach out within 24 hours.", "success")
        return redirect(url_for("landing") + "#universities")

    @app.route("/system/demo-requests", methods=["GET", "POST"])
    @roles_required("system")
    def system_demo_requests():
        db = get_db()
        cursor = db.cursor()
        try:
            if request.method == "POST":
                req_id = int(request.form.get("request_id"))
                status = (request.form.get("status") or "New").strip()
                cursor.execute("UPDATE demo_requests SET status=%s WHERE id=%s", (status, req_id))
                db.commit()
                log_audit("DEMO_LEAD_UPDATED", "demo_request", req_id, f"Status -> {status}")
                flash("Lead status updated!", "success")
                return redirect(url_for("system_demo_requests"))

            cursor.execute("SELECT * FROM demo_requests ORDER BY id DESC")
            leads = cursor.fetchall()
        finally:
            db.close()
        return render_template("system_demo_requests.html", leads=leads)
