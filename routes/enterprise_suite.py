import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timedelta
from flask import render_template, request, jsonify, session, redirect, url_for, flash
from core import config
from core.db import get_db, resolve_student_numeric_id
from core.security import login_required, roles_required, rate_limit, hash_password, verify_password, log_audit
from core.ai_helpers import ask_gemini


def _sign_student_id(student_code: str) -> str:
    return hmac.new(
        config.SECRET_KEY.encode("utf-8"),
        f"student_id:{student_code}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:24]


def _resolve_inst_id(cursor) -> int:
    if session.get("real_instructor_id"):
        return int(session["real_instructor_id"])
    user_val = str(session.get("user_id", ""))
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


def _scope_uni():
    if session.get("role") == "system":
        ts = session.get("tenant_scope")
        return int(ts) if ts else None
    uid = session.get("university_id")
    return int(uid) if uid else None


def register_enterprise_suite_routes(app):
    # ==========================================================
    # 1. UNIVERSITY FINANCE & TUITION BILLING PORTAL
    # ==========================================================
    @app.route("/system/finance")
    @roles_required("system")
    def system_finance():
        status_filter = (request.args.get("status") or "").strip().lower()
        q = (request.args.get("q") or "").strip()
        scope_uni = _scope_uni()
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("SELECT SUM(amount) as total FROM payments")
            total_billed = float((cursor.fetchone() or {}).get("total") or 0)
            cursor.execute("SELECT SUM(amount) as total FROM payments WHERE LOWER(payment_status)='paid'")
            total_collected = float((cursor.fetchone() or {}).get("total") or 0)
            if scope_uni:
                cursor.execute("SELECT SUM(p.amount) as total FROM payments p JOIN users u ON p.user_id=u.id WHERE u.university_id=%s", (scope_uni,))
                total_billed = float((cursor.fetchone() or {}).get("total") or 0)
                cursor.execute("SELECT SUM(p.amount) as total FROM payments p JOIN users u ON p.user_id=u.id WHERE u.university_id=%s AND LOWER(p.payment_status)='paid'", (scope_uni,))
                total_collected = float((cursor.fetchone() or {}).get("total") or 0)
            else:
                cursor.execute("SELECT SUM(amount) as total FROM payments")
                total_billed = float((cursor.fetchone() or {}).get("total") or 0)
                cursor.execute("SELECT SUM(amount) as total FROM payments WHERE LOWER(payment_status)='paid'")
                total_collected = float((cursor.fetchone() or {}).get("total") or 0)
            total_pending = max(0.0, total_billed - total_collected)
            collection_rate = round((total_collected / total_billed) * 100, 1) if total_billed > 0 else 0.0

            sql = """
                SELECT p.*, u.full_name as student_name, u.student_code, u.faculty
                FROM payments p
                LEFT JOIN users u ON p.user_id = u.id
                WHERE 1=1
            """
            params = []
            if scope_uni:
                sql += " AND u.university_id = %s"
                params.append(scope_uni)
            if status_filter in ("paid", "pending", "unpaid"):
                if status_filter == "paid":
                    sql += " AND LOWER(p.payment_status) = 'paid'"
                else:
                    sql += " AND LOWER(p.payment_status) != 'paid'"
            if q:
                sql += " AND (u.full_name LIKE %s OR u.student_code LIKE %s OR p.payment_type LIKE %s)"
                params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
            sql += " ORDER BY p.id DESC LIMIT 120"
            cursor.execute(sql, tuple(params))
            payments = cursor.fetchall()

            cursor.execute("SELECT id, full_name, student_code FROM users ORDER BY full_name ASC")
            students = cursor.fetchall()
            cursor.execute("SELECT DISTINCT faculty FROM users WHERE faculty IS NOT NULL AND faculty != '' ORDER BY faculty")
            faculties = [r["faculty"] for r in cursor.fetchall()]
            cursor.execute("SELECT id, name FROM universities ORDER BY name ASC")
            universities = cursor.fetchall()
            if scope_uni:
                cursor.execute("SELECT id, full_name, student_code FROM users WHERE university_id=%s ORDER BY full_name ASC", (scope_uni,))
                students = cursor.fetchall()
                cursor.execute("SELECT DISTINCT faculty FROM users WHERE university_id=%s AND faculty IS NOT NULL AND faculty != '' ORDER BY faculty", (scope_uni,))
                faculties = [r["faculty"] for r in cursor.fetchall()]
                cursor.execute("SELECT id, name FROM universities WHERE id=%s", (scope_uni,))
                universities = cursor.fetchall()
            else:
                cursor.execute("SELECT id, full_name, student_code FROM users ORDER BY full_name ASC")
                students = cursor.fetchall()
                cursor.execute("SELECT DISTINCT faculty FROM users WHERE faculty IS NOT NULL AND faculty != '' ORDER BY faculty")
                faculties = [r["faculty"] for r in cursor.fetchall()]
                cursor.execute("SELECT id, name FROM universities ORDER BY name ASC")
                universities = cursor.fetchall()
        finally:
            db.close()

        return render_template(
            "system_finance.html",
            total_billed=round(total_billed, 2),
            total_collected=round(total_collected, 2),
            total_pending=round(total_pending, 2),
            collection_rate=collection_rate,
            payments=payments,
            students=students,
            faculties=faculties,
            universities=universities,
            status_filter=status_filter,
            q=q,
        )

    @app.route("/system/finance/create", methods=["POST"])
    @roles_required("system")
    def system_finance_create():
        scope = (request.form.get("scope") or "student").strip()
        target_id = (request.form.get("target_id") or "").strip()
        payment_type = (request.form.get("payment_type") or "Tuition Fees").strip()
        try:
            amount = float(request.form.get("amount") or 0)
        except ValueError:
            amount = 0.0

        if amount <= 0:
            flash("Invoice amount must be greater than 0 EGP.", "danger")
            return redirect(url_for("system_finance"))

        scope_uni = _scope_uni()
        db = get_db()
        cursor = db.cursor()
        try:
            if scope == "student" and target_id.isdigit():
                cursor.execute("SELECT id FROM users WHERE id=%s", (int(target_id),))
                if scope_uni:
                    cursor.execute("SELECT id FROM users WHERE id=%s AND university_id=%s", (int(target_id), scope_uni))
                else:
                    cursor.execute("SELECT id FROM users WHERE id=%s", (int(target_id),))
            elif scope == "faculty" and target_id:
                cursor.execute("SELECT id FROM users WHERE faculty=%s", (target_id,))
                if scope_uni:
                    cursor.execute("SELECT id FROM users WHERE faculty=%s AND university_id=%s", (target_id, scope_uni))
                else:
                    cursor.execute("SELECT id FROM users WHERE faculty=%s", (target_id,))
            elif scope == "university" and target_id.isdigit():
                cursor.execute("SELECT id FROM users WHERE university_id=%s", (int(target_id),))
                eff_u = scope_uni if scope_uni else int(target_id)
                cursor.execute("SELECT id FROM users WHERE university_id=%s", (eff_u,))
            else:
                cursor.execute("SELECT id FROM users")
                if scope_uni:
                    cursor.execute("SELECT id FROM users WHERE university_id=%s", (scope_uni,))
                else:
                    cursor.execute("SELECT id FROM users")

            student_ids = [r["id"] for r in cursor.fetchall()]
            for uid in student_ids:
                cursor.execute(
                    """
                    INSERT INTO payments (user_id, amount, payment_type, payment_status, payment_date, payment_method)
                    VALUES (%s, %s, %s, 'pending', CURDATE(), 'Pending')
                    """,
                    (uid, amount, payment_type),
                )
                cursor.execute(
                    "INSERT INTO notifications (user_id, title, message, type, status) VALUES (%s, %s, %s, 'payment', 'unread')",
                    (uid, f"New Invoice: {payment_type}", f"A fee invoice of {amount:,.0f} EGP has been issued to your account."),
                )
            db.commit()
            log_audit("INVOICES_GENERATED", scope, target_id or "all", f"{len(student_ids)} invoices of {amount} EGP ({payment_type})")
            flash(f"Generated {len(student_ids)} invoice(s) of {amount:,.0f} EGP successfully!", "success")
        finally:
            db.close()
        return redirect(url_for("system_finance"))

    @app.route("/system/finance/<int:payment_id>/mark-paid", methods=["POST"])
    @roles_required("system")
    def system_finance_mark_paid(payment_id):
        method = (request.form.get("method") or "Cash / Treasury").strip()
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("SELECT * FROM payments WHERE id=%s", (payment_id,))
            p = cursor.fetchone()
            if p:
                cursor.execute(
                    "UPDATE payments SET payment_status='paid', payment_date=CURDATE(), payment_method=%s WHERE id=%s",
                    (method, payment_id),
                )
                cursor.execute(
                    "INSERT INTO notifications (user_id, title, message, type, status) VALUES (%s, %s, %s, 'payment', 'unread')",
                    (p["user_id"], "Payment Approved", f"Your payment of {p['amount']} EGP ({p['payment_type']}) has been approved via {method}."),
                )
                db.commit()
                log_audit("FINANCE_PAYMENT_APPROVED", "payment", payment_id, f"Marked paid via {method} ({p['amount']} EGP)")
                flash(f"Invoice #{payment_id} marked as Paid ({method}).", "success")
        finally:
            db.close()
        return redirect(url_for("system_finance"))

    # ==========================================================
    # 2. TIMETABLE & EXAM SCHEDULER ADMIN MANAGER
    # ==========================================================
    @app.route("/system/scheduler")
    @roles_required("system")
    def system_scheduler():
        scope_uni = _scope_uni()
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute(
                """
                SELECT s.*, u.full_name as student_name, u.student_code
                FROM schedules s
                LEFT JOIN users u ON s.user_id = u.id
                ORDER BY s.id DESC LIMIT 60
                """
            )
            schedules = cursor.fetchall()

            cursor.execute(
                """
                SELECT e.*, u.full_name as student_name, u.student_code
                FROM exams e
                LEFT JOIN users u ON e.user_id = u.id
                ORDER BY e.id DESC LIMIT 60
                """
            )
            exams = cursor.fetchall()

            cursor.execute("SELECT course_id, name FROM courses ORDER BY course_id ASC")
            courses = cursor.fetchall()
            cursor.execute("SELECT id, full_name, student_code FROM users ORDER BY full_name ASC")
            students = cursor.fetchall()
            cursor.execute("SELECT DISTINCT faculty FROM users WHERE faculty IS NOT NULL AND faculty != '' ORDER BY faculty")
            faculties = [r["faculty"] for r in cursor.fetchall()]
            if scope_uni:
                cursor.execute(
                    """
                    SELECT s.*, u.full_name as student_name, u.student_code
                    FROM schedules s
                    JOIN users u ON s.user_id = u.id
                    WHERE u.university_id = %s
                    ORDER BY s.id DESC LIMIT 60
                    """,
                    (scope_uni,),
                )
                schedules = cursor.fetchall()
                cursor.execute(
                    """
                    SELECT e.*, u.full_name as student_name, u.student_code
                    FROM exams e
                    JOIN users u ON e.user_id = u.id
                    WHERE u.university_id = %s
                    ORDER BY e.id DESC LIMIT 60
                    """,
                    (scope_uni,),
                )
                exams = cursor.fetchall()
                cursor.execute("SELECT course_id, name FROM courses WHERE university_id=%s ORDER BY course_id ASC", (scope_uni,))
                courses = cursor.fetchall()
                cursor.execute("SELECT id, full_name, student_code FROM users WHERE university_id=%s ORDER BY full_name ASC", (scope_uni,))
                students = cursor.fetchall()
                cursor.execute("SELECT DISTINCT faculty FROM users WHERE university_id=%s AND faculty IS NOT NULL AND faculty != '' ORDER BY faculty", (scope_uni,))
                faculties = [r["faculty"] for r in cursor.fetchall()]
            else:
                cursor.execute(
                    """
                    SELECT s.*, u.full_name as student_name, u.student_code
                    FROM schedules s
                    LEFT JOIN users u ON s.user_id = u.id
                    ORDER BY s.id DESC LIMIT 60
                    """
                )
                schedules = cursor.fetchall()
                cursor.execute(
                    """
                    SELECT e.*, u.full_name as student_name, u.student_code
                    FROM exams e
                    LEFT JOIN users u ON e.user_id = u.id
                    ORDER BY e.id DESC LIMIT 60
                    """
                )
                exams = cursor.fetchall()
                cursor.execute("SELECT course_id, name FROM courses ORDER BY course_id ASC")
                courses = cursor.fetchall()
                cursor.execute("SELECT id, full_name, student_code FROM users ORDER BY full_name ASC")
                students = cursor.fetchall()
                cursor.execute("SELECT DISTINCT faculty FROM users WHERE faculty IS NOT NULL AND faculty != '' ORDER BY faculty")
                faculties = [r["faculty"] for r in cursor.fetchall()]
        finally:
            db.close()

        return render_template(
            "system_scheduler.html",
            schedules=schedules,
            exams=exams,
            courses=courses,
            students=students,
            faculties=faculties,
        )

    @app.route("/system/scheduler/add-schedule", methods=["POST"])
    @roles_required("system")
    def system_add_schedule():
        scope = (request.form.get("scope") or "student").strip()
        target_id = (request.form.get("target_id") or "").strip()
        subject_name = (request.form.get("subject_name") or "").strip()
        day_name = (request.form.get("day_name") or "Sunday").strip()
        start_time = (request.form.get("start_time") or "09:00").strip()
        end_time = (request.form.get("end_time") or "11:00").strip()
        room = (request.form.get("room") or "Hall A").strip()
        doctor_name = (request.form.get("doctor_name") or "Faculty Staff").strip()
        lec_type = (request.form.get("type") or "Lecture").strip()

        scope_uni = _scope_uni()
        db = get_db()
        cursor = db.cursor()
        try:
            if scope == "student" and target_id.isdigit():
                cursor.execute("SELECT id FROM users WHERE id=%s", (int(target_id),))
                if scope_uni:
                    cursor.execute("SELECT id FROM users WHERE id=%s AND university_id=%s", (int(target_id), scope_uni))
                else:
                    cursor.execute("SELECT id FROM users WHERE id=%s", (int(target_id),))
            elif scope == "faculty" and target_id:
                cursor.execute("SELECT id FROM users WHERE faculty=%s", (target_id,))
                if scope_uni:
                    cursor.execute("SELECT id FROM users WHERE faculty=%s AND university_id=%s", (target_id, scope_uni))
                else:
                    cursor.execute("SELECT id FROM users WHERE faculty=%s", (target_id,))
            else:
                cursor.execute("SELECT id FROM users LIMIT 20")
                if scope_uni:
                    cursor.execute("SELECT id FROM users WHERE university_id=%s LIMIT 50", (scope_uni,))
                else:
                    cursor.execute("SELECT id FROM users LIMIT 20")
            uids = [r["id"] for r in cursor.fetchall()]

            for uid in uids:
                cursor.execute(
                    """
                    INSERT INTO schedules (user_id, subject_name, day_name, start_time, end_time, room, doctor_name, type)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (uid, subject_name, day_name, start_time, end_time, room, doctor_name, lec_type),
                )
            db.commit()
            log_audit("SCHEDULE_ADDED", scope, target_id or "bulk", f"{subject_name} ({day_name} {start_time}-{end_time}) for {len(uids)} students")
            flash(f"Schedule slot added for {len(uids)} student(s)!", "success")
        finally:
            db.close()
        return redirect(url_for("system_scheduler"))

    @app.route("/system/scheduler/delete-schedule/<int:sched_id>", methods=["POST"])
    @roles_required("system")
    def system_delete_schedule(sched_id):
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("DELETE FROM schedules WHERE id=%s", (sched_id,))
            db.commit()
            log_audit("SCHEDULE_DELETED", "schedule", sched_id, f"Deleted schedule #{sched_id}")
            flash("Schedule entry removed.", "success")
        finally:
            db.close()
        return redirect(url_for("system_scheduler"))

    @app.route("/system/scheduler/add-exam", methods=["POST"])
    @roles_required("system")
    def system_add_exam():
        scope = (request.form.get("scope") or "student").strip()
        target_id = (request.form.get("target_id") or "").strip()
        subject_name = (request.form.get("subject_name") or "").strip()
        exam_date = (request.form.get("exam_date") or "").strip()
        exam_time = (request.form.get("exam_time") or "10:00 AM").strip()
        location = (request.form.get("location") or "Main Exam Hall 1").strip()

        scope_uni = _scope_uni()
        db = get_db()
        cursor = db.cursor()
        try:
            if scope == "student" and target_id.isdigit():
                cursor.execute("SELECT id FROM users WHERE id=%s", (int(target_id),))
                if scope_uni:
                    cursor.execute("SELECT id FROM users WHERE id=%s AND university_id=%s", (int(target_id), scope_uni))
                else:
                    cursor.execute("SELECT id FROM users WHERE id=%s", (int(target_id),))
            elif scope == "faculty" and target_id:
                cursor.execute("SELECT id FROM users WHERE faculty=%s", (target_id,))
                if scope_uni:
                    cursor.execute("SELECT id FROM users WHERE faculty=%s AND university_id=%s", (target_id, scope_uni))
                else:
                    cursor.execute("SELECT id FROM users WHERE faculty=%s", (target_id,))
            else:
                cursor.execute("SELECT id FROM users LIMIT 20")
                if scope_uni:
                    cursor.execute("SELECT id FROM users WHERE university_id=%s LIMIT 50", (scope_uni,))
                else:
                    cursor.execute("SELECT id FROM users LIMIT 20")
            uids = [r["id"] for r in cursor.fetchall()]

            for uid in uids:
                cursor.execute(
                    "INSERT INTO exams (user_id, subject_name, exam_date, exam_time, location) VALUES (%s, %s, %s, %s, %s)",
                    (uid, subject_name, exam_date, exam_time, location),
                )
                cursor.execute(
                    "INSERT INTO notifications (user_id, title, message, type, status) VALUES (%s, %s, %s, 'exam', 'unread')",
                    (uid, f"Exam Scheduled: {subject_name}", f"Date: {exam_date} at {exam_time} — {location}."),
                )
            db.commit()
            log_audit("EXAM_SCHEDULED", scope, target_id or "bulk", f"{subject_name} on {exam_date} for {len(uids)} students")
            flash(f"Exam scheduled for {len(uids)} student(s) & notifications sent!", "success")
        finally:
            db.close()
        return redirect(url_for("system_scheduler"))

    @app.route("/system/scheduler/delete-exam/<int:exam_id>", methods=["POST"])
    @roles_required("system")
    def system_delete_exam(exam_id):
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("DELETE FROM exams WHERE id=%s", (exam_id,))
            db.commit()
            log_audit("EXAM_DELETED", "exam", exam_id, f"Deleted exam #{exam_id}")
            flash("Exam entry removed.", "success")
        finally:
            db.close()
        return redirect(url_for("system_scheduler"))

    # ==========================================================
    # 3. CHANGE PASSWORD & FORGOT / RESET PASSWORD
    # ==========================================================
    @app.route("/change-password", methods=["GET", "POST"])
    @login_required
    def change_password():
        role = session.get("role", "student")
        base_layout = (
            "system_layout.html" if role == "system"
            else ("instructor_layout.html" if role == "instructor" else "dashboard_layout.html")
        )

        if request.method == "POST":
            current_pw = (request.form.get("current_password") or "").strip()
            new_pw = (request.form.get("new_password") or "").strip()
            confirm_pw = (request.form.get("confirm_password") or "").strip()

            if len(new_pw) < 6:
                flash("New password must be at least 6 characters.", "danger")
                return redirect(url_for("change_password"))
            if new_pw != confirm_pw:
                flash("New passwords do not match.", "danger")
                return redirect(url_for("change_password"))

            db = get_db()
            cursor = db.cursor()
            try:
                uid_str = str(session["user_id"])
                updated = False
                if role == "system" and uid_str.startswith("sys_"):
                    sid = int(uid_str.split("_", 1)[1])
                    cursor.execute("SELECT password FROM system_users WHERE id=%s", (sid,))
                    row = cursor.fetchone()
                    if row and verify_password(row["password"], current_pw):
                        cursor.execute("UPDATE system_users SET password=%s WHERE id=%s", (hash_password(new_pw), sid))
                        updated = True
                elif role == "instructor" and uid_str.startswith("ins_"):
                    iid = int(uid_str.split("_", 1)[1])
                    cursor.execute("SELECT password FROM admins WHERE id=%s", (iid,))
                    row = cursor.fetchone()
                    if row and verify_password(row["password"], current_pw):
                        cursor.execute("UPDATE admins SET password=%s WHERE id=%s", (hash_password(new_pw), iid))
                        updated = True
                elif role == "student":
                    cursor.execute("SELECT id, password FROM users WHERE student_code=%s", (uid_str,))
                    row = cursor.fetchone()
                    if row and verify_password(row["password"], current_pw):
                        new_hash = hash_password(new_pw)
                        cursor.execute("UPDATE users SET password=%s WHERE id=%s", (new_hash, row["id"]))
                        cursor.execute("UPDATE facusers SET password=%s WHERE user_id=%s", (new_hash, uid_str))
                        updated = True

                if updated:
                    db.commit()
                    log_audit("PASSWORD_CHANGED", role, uid_str, "User updated their account password")
                    flash("Password updated successfully!", "success")
                else:
                    flash("Current password is incorrect.", "danger")
            finally:
                db.close()
            return redirect(url_for("change_password"))

        return render_template("change_password.html", base_layout=base_layout)

    @app.route("/forgot-password", methods=["GET", "POST"])
    @rate_limit(max_requests=8, window_seconds=60, key_prefix="forgot_pw")
    def forgot_password():
        reset_link = None
        if request.method == "POST":
            email = (request.form.get("email") or "").strip().lower()
            role = (request.form.get("role") or "student").strip()
            db = get_db()
            cursor = db.cursor()
            try:
                found = False
                if role == "system":
                    cursor.execute("SELECT id FROM system_users WHERE LOWER(email)=%s", (email,))
                    found = bool(cursor.fetchone())
                elif role == "instructor":
                    cursor.execute("SELECT id FROM admins WHERE LOWER(email)=%s", (email,))
                    found = bool(cursor.fetchone())
                else:
                    cursor.execute("SELECT id FROM users WHERE LOWER(email)=%s", (email,))
                    found = bool(cursor.fetchone())

                if found:
                    token = secrets.token_urlsafe(32)
                    expires = (datetime.now() + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
                    cursor.execute(
                        "INSERT INTO password_reset_tokens (email, role, token, expires_at, used) VALUES (%s, %s, %s, %s, 0)",
                        (email, role, token, expires),
                    )
                    db.commit()
                    log_audit("PASSWORD_RESET_REQUESTED", role, email, "Generated 30-min reset token",
                              actor_override={"id": email, "name": email, "role": role})
                    reset_link = url_for("reset_password", token=token, _external=True)
                    flash("Password reset link generated (valid for 30 minutes).", "success")
                else:
                    flash("If an account with that email exists, a reset link has been issued.", "info")
            finally:
                db.close()
        return render_template("forgot_password.html", reset_link=reset_link)

    @app.route("/reset-password/<token>", methods=["GET", "POST"])
    def reset_password(token):
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("SELECT * FROM password_reset_tokens WHERE token=%s AND used=0 ORDER BY id DESC LIMIT 1", (token,))
            rec = cursor.fetchone()
            valid = False
            if rec:
                exp = rec["expires_at"]
                exp_dt = exp if isinstance(exp, datetime) else datetime.fromisoformat(str(exp))
                valid = datetime.now() <= exp_dt

            if request.method == "POST":
                if not valid:
                    flash("This reset link has expired or already been used.", "danger")
                    return redirect(url_for("forgot_password"))
                new_pw = (request.form.get("new_password") or "").strip()
                confirm_pw = (request.form.get("confirm_password") or "").strip()
                if len(new_pw) < 6 or new_pw != confirm_pw:
                    flash("Passwords must match and be at least 6 characters.", "danger")
                    return render_template("reset_password.html", valid=True, token=token)

                pw_hash = hash_password(new_pw)
                email = rec["email"]
                role = rec["role"]
                if role == "system":
                    cursor.execute("UPDATE system_users SET password=%s WHERE LOWER(email)=%s", (pw_hash, email))
                elif role == "instructor":
                    cursor.execute("UPDATE admins SET password=%s WHERE LOWER(email)=%s", (pw_hash, email))
                else:
                    cursor.execute("UPDATE users SET password=%s WHERE LOWER(email)=%s", (pw_hash, email))
                    cursor.execute("UPDATE facusers SET password=%s WHERE LOWER(email)=%s", (pw_hash, email))

                cursor.execute("UPDATE password_reset_tokens SET used=1 WHERE id=%s", (rec["id"],))
                db.commit()
                log_audit("PASSWORD_RESET_COMPLETED", role, email, "Password reset via secure token",
                          actor_override={"id": email, "name": email, "role": role})
                flash("Password has been reset! You can now log in.", "success")
                return redirect(url_for("login"))
        finally:
            db.close()
        return render_template("reset_password.html", valid=valid, token=token)

    # ==========================================================
    # 4. AI EARLY WARNING / AT-RISK STUDENTS (NAQAAE QUALITY)
    # ==========================================================
    @app.route("/system/at-risk")
    @roles_required("system", "instructor")
    def system_at_risk():
        scope_uni = _scope_uni()
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("SELECT id, full_name, student_code, email, faculty, level, gpa FROM users ORDER BY gpa ASC")
            if scope_uni:
                cursor.execute(
                    "SELECT id, full_name, student_code, email, faculty, level, gpa FROM users WHERE university_id=%s ORDER BY gpa ASC",
                    (scope_uni,),
                )
            else:
                cursor.execute("SELECT id, full_name, student_code, email, faculty, level, gpa FROM users ORDER BY gpa ASC")
            all_users = cursor.fetchall()

            cursor.execute("SELECT user_id, subject_name, total_lectures, absences FROM attendance_summary")
            att_map = {}
            for a in cursor.fetchall():
                tot = max(1, int(a.get("total_lectures") or 12))
                ab = int(a.get("absences") or 0)
                pct = round((ab / tot) * 100, 1)
                if pct >= 18:
                    att_map.setdefault(a["user_id"], []).append({"subject": a["subject_name"], "pct": pct})

            at_risk = []
            for u in all_users:
                gpa = float(u.get("gpa") or 0)
                high_abs = att_map.get(u["id"], [])
                reasons = []
                score = 0
                if 0 < gpa < 2.0:
                    reasons.append(f"Low GPA ({gpa:.2f})")
                    score += 55 if gpa < 1.5 else 35
                elif 0 < gpa < 2.3:
                    reasons.append(f"Borderline GPA ({gpa:.2f})")
                    score += 20
                if high_abs:
                    max_ab = max(x["pct"] for x in high_abs)
                    reasons.append(f"High Absence ({max_ab}% in {high_abs[0]['subject']})")
                    score += 50 if max_ab >= 25 else 30

                if reasons:
                    severity = "Critical" if score >= 60 else ("High" if score >= 35 else "Watchlist")
                    at_risk.append({
                        **u,
                        "gpa": round(gpa, 2),
                        "reasons": reasons,
                        "risk_score": min(100, score),
                        "severity": severity,
                    })

            at_risk.sort(key=lambda x: x["risk_score"], reverse=True)
        finally:
            db.close()

        base_layout = "system_layout.html" if session.get("role") == "system" else "instructor_layout.html"
        return render_template("system_at_risk.html", at_risk=at_risk, base_layout=base_layout)

    @app.route("/system/at-risk/notify/<int:student_uid>", methods=["POST"])
    @roles_required("system", "instructor")
    def notify_at_risk_student(student_uid):
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("SELECT full_name, student_code FROM users WHERE id=%s", (student_uid,))
            st = cursor.fetchone()
            if st:
                cursor.execute(
                    "INSERT INTO notifications (user_id, title, message, type, status) VALUES (%s, %s, %s, 'warning', 'unread')",
                    (
                        student_uid,
                        "Academic Advising Intervention Required",
                        "Please schedule an academic advising session this week to review your GPA and attendance standing.",
                    ),
                )
                db.commit()
                log_audit("AT_RISK_INTERVENTION_SENT", "student", st["student_code"], f"Advising alert sent to {st['full_name']}")
                flash(f"Academic advising alert sent to {st['full_name']}!", "success")
        finally:
            db.close()
        return redirect(url_for("system_at_risk"))

    @app.route("/api/at-risk/ai-plan", methods=["POST"])
    @roles_required("system", "instructor")
    @rate_limit(max_requests=12, window_seconds=60, key_prefix="ai_risk")
    def ai_at_risk_plan():
        data = request.get_json(silent=True) or {}
        name = data.get("name", "Student")
        gpa = data.get("gpa", "N/A")
        reasons = ", ".join(data.get("reasons", []))
        prompt = (
            f"You are an Egyptian university academic quality advisor (NAQAAE). Write a concise 3-bullet academic intervention plan "
            f"for student {name} (GPA: {gpa}, Risk Factors: {reasons}). Include concrete next steps for advisor and student."
        )
        plan = None
        if config.groq_client:
            try:
                comp = config.groq_client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model=config.GROQ_MODEL_NAME,
                    max_tokens=260,
                )
                plan = comp.choices[0].message.content
            except Exception:
                pass
        if not plan:
            plan = ask_gemini(prompt)
        if not plan or plan in ("__QUOTA__", "__ERROR__"):
            plan = (
                f"1. Schedule a mandatory 1-on-1 advising meeting with {name} within 5 days.\n"
                "2. Cap next semester credit load to 12 credit hours and prioritize retaking failed core courses.\n"
                "3. Track weekly attendance via QR check-ins and assign peer tutoring support."
            )
        return jsonify({"success": True, "plan": plan})

    # ==========================================================
    # 5. DIGITAL STUDENT ID CARD + CAMPUS QR VERIFICATION
    # ==========================================================
    @app.route("/student-id")
    @login_required
    def student_id_card():
        student_code = session["user_id"]
        db = get_db()
        cursor = db.cursor()
        try:
            nid = resolve_student_numeric_id(cursor, student_code)
            if not nid:
                flash("Digital ID Card is available for student accounts.", "warning")
                return redirect(url_for("dashboard"))
            cursor.execute("SELECT * FROM users WHERE id=%s", (nid,))
            user = cursor.fetchone()
            uni = None
            if user and user.get("university_id"):
                cursor.execute("SELECT * FROM universities WHERE id=%s", (user["university_id"],))
                uni = cursor.fetchone()
        finally:
            db.close()

        sig = _sign_student_id(user["student_code"])
        verify_url = url_for("verify_student_id", student_code=user["student_code"], sig=sig, _external=True)
        return render_template("student_id_card.html", user=user, uni=uni, verify_url=verify_url, sig=sig)

    @app.route("/verify/student/<student_code>")
    def verify_student_id(student_code):
        sig = (request.args.get("sig") or "").strip()
        is_valid = bool(sig and hmac.compare_digest(sig, _sign_student_id(student_code)))
        user, uni = None, None
        if is_valid:
            db = get_db()
            cursor = db.cursor()
            try:
                cursor.execute("SELECT full_name, student_code, faculty, level, gpa, profile_pic, university_id FROM users WHERE student_code=%s", (student_code,))
                user = cursor.fetchone()
                if user and user.get("university_id"):
                    cursor.execute("SELECT name, primary_color FROM universities WHERE id=%s", (user["university_id"],))
                    uni = cursor.fetchone()
                if not user:
                    is_valid = False
            finally:
                db.close()
        return render_template("verify_student.html", is_valid=is_valid, student_code=student_code, user=user, uni=uni)

    # ==========================================================
    # 6. ONLINE QUIZZES + AI QUIZ GENERATOR + AUTO-GRADING
    # ==========================================================
    @app.route("/instructor/quizzes", methods=["GET", "POST"])
    @roles_required("instructor", "system")
    def instructor_quizzes():
        db = get_db()
        cursor = db.cursor()
        try:
            real_id = _resolve_inst_id(cursor)
            scope_uni = _scope_uni()
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
                time_limit = max(3, min(180, int(request.form.get("time_limit_mins") or 15)))
                questions_raw = request.form.get("questions_json") or "[]"

                try:
                    questions = json.loads(questions_raw)
                except Exception:
                    questions = []

                if not course_id or not title or not questions:
                    flash("Course, quiz title, and at least 1 question are required.", "danger")
                    return redirect(url_for("instructor_quizzes"))

                uni_id = int(session.get("university_id") or 3)
                max_att = max(1, min(5, int(request.form.get("max_attempts") or 1)))
                cursor.execute(
                    "INSERT INTO quizzes (course_id, instructor_id, title, time_limit_mins, is_published, max_attempts, university_id) VALUES (%s, %s, %s, %s, 1, %s, %s)",
                    (course_id, real_id, title, time_limit, max_att, uni_id),
                )
                quiz_id = cursor.lastrowid
                for q in questions:
                    cursor.execute(
                        """
                        INSERT INTO quiz_questions (quiz_id, question_text, option_a, option_b, option_c, option_d, correct_option)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            quiz_id,
                            q.get("question", ""),
                            q.get("a", ""),
                            q.get("b", ""),
                            q.get("c", ""),
                            q.get("d", ""),
                            str(q.get("correct", "A")).upper()[:1],
                        ),
                    )
                db.commit()
                log_audit("QUIZ_PUBLISHED", "quiz", quiz_id, f"{course_id}: '{title}' ({len(questions)} questions)")
                flash(f"Quiz '{title}' published with {len(questions)} auto-graded questions!", "success")
                return redirect(url_for("instructor_quizzes"))

            cursor.execute(
                """
                SELECT q.*, c.name as course_name,
                       (SELECT COUNT(*) FROM quiz_questions qq WHERE qq.quiz_id = q.id) as q_count,
                       (SELECT COUNT(*) FROM quiz_attempts qa WHERE qa.quiz_id = q.id) as attempt_count,
                       (SELECT ROUND(AVG(qa.percentage), 1) FROM quiz_attempts qa WHERE qa.quiz_id = q.id) as avg_pct
                FROM quizzes q
                LEFT JOIN courses c ON q.course_id = c.course_id
                ORDER BY q.id DESC
                """
            )
            if session.get("role") == "system" and not scope_uni:
                cursor.execute(
                    """
                    SELECT q.*, c.name as course_name,
                           (SELECT COUNT(*) FROM quiz_questions qq WHERE qq.quiz_id = q.id) as q_count,
                           (SELECT COUNT(*) FROM quiz_attempts qa WHERE qa.quiz_id = q.id) as attempt_count,
                           (SELECT ROUND(AVG(qa.percentage), 1) FROM quiz_attempts qa WHERE qa.quiz_id = q.id) as avg_pct
                    FROM quizzes q
                    LEFT JOIN courses c ON q.course_id = c.course_id
                    ORDER BY q.id DESC
                    """
                )
            elif session.get("role") == "system" and scope_uni:
                cursor.execute(
                    """
                    SELECT q.*, c.name as course_name,
                           (SELECT COUNT(*) FROM quiz_questions qq WHERE qq.quiz_id = q.id) as q_count,
                           (SELECT COUNT(*) FROM quiz_attempts qa WHERE qa.quiz_id = q.id) as attempt_count,
                           (SELECT ROUND(AVG(qa.percentage), 1) FROM quiz_attempts qa WHERE qa.quiz_id = q.id) as avg_pct
                    FROM quizzes q
                    LEFT JOIN courses c ON q.course_id = c.course_id
                    WHERE q.university_id = %s
                    ORDER BY q.id DESC
                    """,
                    (scope_uni,),
                )
            else:
                cursor.execute(
                    """
                    SELECT q.*, c.name as course_name,
                           (SELECT COUNT(*) FROM quiz_questions qq WHERE qq.quiz_id = q.id) as q_count,
                           (SELECT COUNT(*) FROM quiz_attempts qa WHERE qa.quiz_id = q.id) as attempt_count,
                           (SELECT ROUND(AVG(qa.percentage), 1) FROM quiz_attempts qa WHERE qa.quiz_id = q.id) as avg_pct
                    FROM quizzes q
                    LEFT JOIN courses c ON q.course_id = c.course_id
                    WHERE q.instructor_id = %s
                    ORDER BY q.id DESC
                    """,
                    (real_id,),
                )
            quizzes = cursor.fetchall()
        finally:
            db.close()

        return render_template("instructor_quizzes.html", courses=courses, quizzes=quizzes)

    @app.route("/api/instructor/quizzes/ai-generate", methods=["POST"])
    @roles_required("instructor", "system")
    @rate_limit(max_requests=10, window_seconds=60, key_prefix="ai_quiz")
    def ai_generate_quiz():
        data = request.get_json(silent=True) or {}
        topic = (data.get("topic") or "Financial Accounting Fundamentals").strip()
        count = max(2, min(10, int(data.get("count") or 5)))

        prompt = (
            f"Generate exactly {count} university-level multiple-choice questions about '{topic}'. "
            'Return ONLY a valid JSON array with no markdown fences. Each item must have keys: '
            '"question", "a", "b", "c", "d", "correct" (where "correct" is one of "A","B","C","D").'
        )
        raw = None
        if config.groq_client:
            try:
                comp = config.groq_client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model=config.GROQ_MODEL_NAME,
                    temperature=0.4,
                    max_tokens=1400,
                )
                raw = comp.choices[0].message.content
            except Exception as e:
                print("AI Quiz Groq error:", e)
        if not raw:
            raw = ask_gemini(prompt)

        questions = []
        if raw and raw not in ("__QUOTA__", "__ERROR__"):
            m = re.search(r"\[.*\]", raw, flags=re.S)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                    if isinstance(parsed, list):
                        questions = parsed
                except Exception:
                    pass

        if not questions:
            questions = [
                {
                    "question": f"Which concept is foundational to {topic}?",
                    "a": "Systematic measurement and reporting",
                    "b": "Random estimation without records",
                    "c": "Ignoring regulatory standards",
                    "d": "Unverified informal entries",
                    "correct": "A",
                },
                {
                    "question": f"What is the primary objective of applying analytical methods in {topic}?",
                    "a": "Increasing ambiguity",
                    "b": "Supporting evidence-based decision making",
                    "c": "Delaying operational feedback",
                    "d": "Eliminating documentation",
                    "correct": "B",
                },
            ]
        return jsonify({"success": True, "questions": questions})

    @app.route("/quizzes")
    @login_required
    def student_quizzes():
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
                SELECT q.*, c.name as course_name,
                       (SELECT COUNT(*) FROM quiz_questions qq WHERE qq.quiz_id = q.id) as q_count
                FROM quizzes q
                LEFT JOIN courses c ON q.course_id = c.course_id
                WHERE q.is_published = 1
                  AND (q.university_id = %s OR q.university_id IS NULL)
                ORDER BY q.id DESC
                """,
                (uni_id,),
            )
            quizzes = cursor.fetchall()

            attempts_map = {}
            attempts_count = {}
            if nid:
                cursor.execute("SELECT * FROM quiz_attempts WHERE user_id=%s ORDER BY id DESC", (nid,))
                for att in cursor.fetchall():
                    qid = att["quiz_id"]
                    attempts_map.setdefault(qid, att)
                    attempts_count[qid] = attempts_count.get(qid, 0) + 1
            for q in quizzes:
                max_att = int(q.get("max_attempts") or 1)
                used = attempts_count.get(q["id"], 0)
                q["attempt"] = attempts_map.get(q["id"])
                q["attempts_used"] = used
                q["max_attempts"] = max_att
                q["is_locked"] = used >= max_att
        finally:
            db.close()
        return render_template("student_quizzes.html", quizzes=quizzes)

    @app.route("/quizzes/<int:quiz_id>/take", methods=["GET", "POST"])
    @login_required
    def take_quiz(quiz_id):
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("SELECT q.*, c.name as course_name FROM quizzes q LEFT JOIN courses c ON q.course_id=c.course_id WHERE q.id=%s", (quiz_id,))
            quiz = cursor.fetchone()
            if not quiz:
                flash("Quiz not found.", "danger")
                return redirect(url_for("student_quizzes"))

            nid = resolve_student_numeric_id(cursor, session["user_id"]) or 0
            max_att = int(quiz.get("max_attempts") or 1)
            if nid:
                cursor.execute("SELECT COUNT(*) as c FROM quiz_attempts WHERE quiz_id=%s AND user_id=%s", (quiz_id, nid))
                used = int((cursor.fetchone() or {}).get("c") or 0)
                if used >= max_att:
                    flash(f"You have already used all allowed attempts ({used}/{max_att}) for this quiz.", "warning")
                    return redirect(url_for("student_quizzes"))

            cursor.execute("SELECT * FROM quiz_questions WHERE quiz_id=%s ORDER BY id ASC", (quiz_id,))
            questions = cursor.fetchall()

            # Server-side authoritative countdown (cannot be reset by browser refresh)
            time_limit_secs = max(60, int(quiz.get("time_limit_mins") or 15) * 60)
            timer_key = f"quiz_start_{quiz_id}"
            now_ts = int(datetime.now().timestamp())
            start_ts = session.get(timer_key)
            if not start_ts:
                start_ts = now_ts
                session[timer_key] = start_ts
            elapsed = max(0, now_ts - int(start_ts))
            remaining_secs = max(0, time_limit_secs - elapsed)

            if request.method == "POST":
                # Grace window of 60s for network latency / auto-submit
                if elapsed > time_limit_secs + 60:
                    session.pop(timer_key, None)
                    flash("Quiz time limit exceeded on server. Submission was closed.", "danger")
                    return redirect(url_for("student_quizzes"))

                correct_count = 0
                review = []
                answers_saved = {}
                for q in questions:
                    chosen = (request.form.get(f"q_{q['id']}") or "").strip().upper()
                    expected = (q["correct_option"] or "A").strip().upper()
                    is_right = chosen == expected
                    if is_right:
                        correct_count += 1
                    answers_saved[str(q["id"])] = chosen
                    review.append({**q, "chosen": chosen, "is_right": is_right})

                total = max(1, len(questions))
                pct = round((correct_count / total) * 100, 1)

                if nid:
                    cursor.execute(
                        """
                        INSERT INTO quiz_attempts (quiz_id, user_id, student_code, score, total_questions, percentage, answers_json)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (quiz_id, nid, session["user_id"], correct_count, len(questions), pct, json.dumps(answers_saved)),
                    )
                    cursor.execute(
                        "INSERT INTO notifications (user_id, title, message, type, status) VALUES (%s, %s, %s, 'grade', 'unread')",
                        (nid, f"Quiz Auto-Graded: {quiz['title']}", f"You scored {correct_count}/{len(questions)} ({pct}%)."),
                    )
                    db.commit()
                    log_audit("QUIZ_SUBMITTED", "quiz", quiz_id, f"Student {session['user_id']} scored {correct_count}/{len(questions)} ({pct}%)")

                session.pop(timer_key, None)
                return render_template(
                    "student_quiz_take.html",
                    quiz=quiz,
                    questions=questions,
                    remaining_secs=0,
                    result={"score": correct_count, "total": len(questions), "pct": pct, "review": review},
                )
        finally:
            db.close()

        return render_template("student_quiz_take.html", quiz=quiz, questions=questions, remaining_secs=remaining_secs, result=None)
