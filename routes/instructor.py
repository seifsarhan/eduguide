import csv
import io
import os
import time
from werkzeug.utils import secure_filename
from flask import render_template, request, jsonify, session, redirect, url_for, flash, Response
from core.db import get_db
from core.security import roles_required, log_audit

ALLOWED_PDF_EXTS = {".pdf"}
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _resolve_instructor_id(cursor) -> int:
    user_val = str(session.get("user_id", ""))
    instructor_email = None
    real_instructor_id = None

    if user_val.startswith("ins_"):
        admin_id = user_val.split("_", 1)[1]
        cursor.execute("SELECT email FROM admins WHERE id=%s", (admin_id,))
        cursor.execute("SELECT email, university_id FROM admins WHERE id=%s", (admin_id,))
        a = cursor.fetchone()
        if a:
            instructor_email = a["email"]
            if a.get("university_id") and not session.get("university_id"):
                session["university_id"] = a["university_id"]
    else:
        cursor.execute("SELECT email FROM instructors WHERE instructor_id=%s", (user_val,))
        i = cursor.fetchone()
        if i:
            instructor_email = i["email"]
            real_instructor_id = user_val

    if instructor_email and not real_instructor_id:
        cursor.execute("SELECT instructor_id FROM instructors WHERE email=%s", (instructor_email,))
        cursor.execute("SELECT instructor_id FROM instructors WHERE LOWER(email)=LOWER(%s)", (instructor_email,))
        i = cursor.fetchone()
        if i:
            real_instructor_id = i["instructor_id"]

    if not real_instructor_id and session.get("university_id"):
        cursor.execute("SELECT instructor_id FROM instructors WHERE university_id=%s ORDER BY instructor_id ASC LIMIT 1", (int(session["university_id"]),))
        i = cursor.fetchone()
        if i:
            real_instructor_id = i["instructor_id"]

    return real_instructor_id or 1


def _recalculate_student_gpa(cursor, numeric_user_id):
    cursor.execute("SELECT points, credit_hours FROM student_grades WHERE user_id=%s", (numeric_user_id,))
    rows = cursor.fetchall()
    total_pts, total_creds = 0.0, 0
    for r in rows:
        try:
            pts = float(r["points"])
            creds = int(r["credit_hours"])
            total_pts += pts * creds
            total_creds += creds
        except Exception:
            pass
    if total_creds > 0:
        new_gpa = round(total_pts / total_creds, 2)
        cursor.execute("UPDATE users SET gpa=%s WHERE id=%s", (new_gpa, numeric_user_id))
        cursor.execute("SELECT student_code FROM users WHERE id=%s", (numeric_user_id,))
        u = cursor.fetchone()
        if u and u.get("student_code"):
            cursor.execute("UPDATE students SET gpa=%s WHERE student_id=%s", (new_gpa, u["student_code"]))


def register_instructor_routes(app):
    @app.route("/admin_dashboard")
    @roles_required("instructor", "system")
    def admin_dashboard():
        db = get_db()
        cursor = db.cursor()
        try:
            real_instructor_id = _resolve_instructor_id(cursor)
            session["real_instructor_id"] = real_instructor_id

            cursor.execute(
                "SELECT course_id, name, credit_hours, semester FROM courses WHERE instructor_id=%s",
                (real_instructor_id,),
            )
            my_courses = cursor.fetchall()

            course_ids = [c["course_id"] for c in my_courses]
            total_students = 0
            my_students = []
            if course_ids:
                placeholders = ",".join(["%s"] * len(course_ids))
                cursor.execute(
                    f"SELECT COUNT(DISTINCT user_id) as count FROM student_grades WHERE course_code IN ({placeholders})",
                    tuple(course_ids),
                )
                total_students = cursor.fetchone()["count"]

                cursor.execute(
                    f"""
                    SELECT DISTINCT u.full_name, sg.course_code, sg.grade, u.email
                    FROM student_grades sg
                    JOIN users u ON sg.user_id = u.id
                    WHERE sg.course_code IN ({placeholders})
                    LIMIT 8
                    """,
                    tuple(course_ids),
                )
                my_students = cursor.fetchall()

            cursor.execute(
                "SELECT COUNT(*) as count FROM student_requests WHERE instructor_id=%s AND (status='Pending' OR doctor_reply IS NULL)",
                (real_instructor_id,),
            )
            pending_requests_count = cursor.fetchone()["count"]

            cursor.execute(
                """
                SELECT sr.*, u.full_name as student_name
                FROM student_requests sr
                JOIN users u ON sr.user_id = u.id
                WHERE sr.instructor_id=%s
                ORDER BY sr.created_at DESC LIMIT 5
                """,
                (real_instructor_id,),
            )
            my_requests = cursor.fetchall()

            cursor.execute(
                "SELECT * FROM marketplace_products WHERE instructor_id=%s ORDER BY id DESC LIMIT 4",
                (real_instructor_id,),
            )
            my_materials = cursor.fetchall()
        finally:
            db.close()

        return render_template(
            "admin_dashboard.html",
            courses=my_courses,
            total_students=total_students,
            my_students=my_students,
            pending_requests_count=pending_requests_count,
            my_requests=my_requests,
            my_materials=my_materials,
        )

    @app.route("/admin_my_students")
    @roles_required("instructor", "system")
    def admin_my_students():
        db = get_db()
        cursor = db.cursor()
        try:
            real_id = session.get("real_instructor_id") or _resolve_instructor_id(cursor)
            cursor.execute(
                """
                SELECT u.full_name as student_name, u.email, sg.course_code as course_id, sg.course_name, sg.grade
                FROM student_grades sg
                JOIN users u ON sg.user_id = u.id
                JOIN courses c ON sg.course_code = c.course_id
                WHERE c.instructor_id = %s
                """,
                (real_id,),
            )
            students = cursor.fetchall()
        finally:
            db.close()
        return render_template("admin_my_students.html", students=students)

    @app.route("/admin_grades", methods=["GET", "POST"])
    @roles_required("instructor", "system")
    def admin_grades():
        db = get_db()
        cursor = db.cursor()
        try:
            real_id = session.get("real_instructor_id") or _resolve_instructor_id(cursor)

            if request.method == "POST":
                grade_id = request.form.get("grade_id")
                grade_letter = (request.form.get("grade") or "").strip().upper()
                try:
                    points = round(max(0.0, min(4.0, float(request.form.get("points", 0)))), 2)
                except ValueError:
                    flash("Invalid GPA points value.", "danger")
                    return redirect(url_for("admin_grades"))

                # Ownership & Audit verification
                cursor.execute(
                    """
                    SELECT sg.*, c.instructor_id
                    FROM student_grades sg
                    JOIN courses c ON sg.course_code = c.course_id
                    WHERE sg.id=%s
                    """,
                    (grade_id,),
                )
                existing = cursor.fetchone()
                if not existing or (session.get("role") != "system" and str(existing["instructor_id"]) != str(real_id)):
                    flash("Unauthorized grade modification blocked.", "danger")
                    log_audit("GRADE_UPDATE_BLOCKED", "student_grade", grade_id, "Unauthorized grade edit attempt")
                    return redirect(url_for("admin_grades"))

                old_grade = existing["grade"]
                old_points = existing["points"]
                cursor.execute(
                    "UPDATE student_grades SET grade=%s, points=%s WHERE id=%s",
                    (grade_letter, points, grade_id),
                )
                _recalculate_student_gpa(cursor, existing["user_id"])
                db.commit()
                log_audit(
                    "GRADE_UPDATED",
                    "student_grade",
                    grade_id,
                    f"Course {existing['course_code']} for user #{existing['user_id']}: {old_grade} ({old_points}) -> {grade_letter} ({points})",
                )
                flash("Grade updated and student GPA recalculated!", "success")

            cursor.execute(
                """
                SELECT sg.*, u.full_name as student_name
                FROM student_grades sg
                JOIN users u ON sg.user_id = u.id
                JOIN courses c ON sg.course_code = c.course_id
                WHERE c.instructor_id = %s
                """,
                (real_id,),
            )
            grades = cursor.fetchall()
        finally:
            db.close()
        return render_template("admin_grades.html", grades=grades)

    @app.route("/admin_requests", methods=["GET", "POST"])
    @roles_required("instructor", "system")
    def admin_requests():
        db = get_db()
        cursor = db.cursor()
        try:
            real_id = session.get("real_instructor_id") or _resolve_instructor_id(cursor)

            if request.method == "POST":
                req_id = request.form.get("request_id")
                status = (request.form.get("status") or "Pending").strip()
                cursor.execute(
                    "UPDATE student_requests SET status=%s WHERE id=%s AND (instructor_id=%s OR instructor_id IS NULL)",
                    (status, req_id, real_id),
                )
                db.commit()
                log_audit("REQUEST_STATUS_UPDATED", "student_request", req_id, f"Status set to {status}")
                flash("Request status updated!", "success")

            cursor.execute(
                """
                SELECT sr.*, u.full_name as student_name, u.email
                FROM student_requests sr
                JOIN users u ON sr.user_id = u.id
                WHERE (sr.instructor_id = %s OR sr.instructor_id IS NULL)
                ORDER BY sr.created_at DESC
                """,
                (real_id,),
            )
            reqs = cursor.fetchall()
        finally:
            db.close()
        return render_template("admin_requests.html", requests=reqs)

    @app.route("/admin/reply_request", methods=["POST"])
    @roles_required("instructor", "system")
    def admin_reply_request():
        data = request.get_json(silent=True) or {}
        req_id = data.get("request_id")
        reply_text = (data.get("reply_text") or "").strip()
        if not req_id or not reply_text:
            return jsonify({"success": False, "error": "Missing data"}), 400

        db = get_db()
        cursor = db.cursor()
        try:
            real_instructor_id = session.get("real_instructor_id") or _resolve_instructor_id(cursor)
            cursor.execute(
                "SELECT id FROM student_requests WHERE id=%s AND (instructor_id=%s OR instructor_id IS NULL)",
                (req_id, real_instructor_id),
            )
            if not cursor.fetchone():
                return jsonify({"success": False, "error": "Request not found"}), 404

            cursor.execute(
                "UPDATE student_requests SET doctor_reply=%s, status='Replied' WHERE id=%s",
                (reply_text, req_id),
            )
            db.commit()
            log_audit("REQUEST_REPLIED", "student_request", req_id, "Instructor replied to student request")
        finally:
            db.close()
        return jsonify({"success": True})

    @app.route("/admin_add_product", methods=["GET", "POST"])
    @roles_required("instructor", "system")
    def admin_add_product():
        if request.method == "POST":
            title = (request.form.get("title") or "").strip()
            cat = (request.form.get("category") or "PDF Notes").strip()
            price = request.form.get("price") or 0
            summary = (request.form.get("summary") or "").strip()
            real_id = session.get("real_instructor_id", 1)

            pdf_filename = None
            img_filename = None
            uploads_dir = os.path.join(app.static_folder, "uploads")
            os.makedirs(uploads_dir, exist_ok=True)

            if "pdf_file" in request.files:
                pdf = request.files["pdf_file"]
                if pdf and pdf.filename:
                    if os.path.splitext(pdf.filename)[1].lower() not in ALLOWED_PDF_EXTS:
                        flash("Only PDF files are allowed for document uploads.", "danger")
                        return redirect(url_for("admin_add_product"))
                    pdf_filename = f"pdf_{int(time.time())}_{secure_filename(pdf.filename)}"
                    pdf.save(os.path.join(uploads_dir, pdf_filename))

            if "image" in request.files:
                img = request.files["image"]
                if img and img.filename:
                    if os.path.splitext(img.filename)[1].lower() not in ALLOWED_IMAGE_EXTS:
                        flash("Unsupported cover image format.", "danger")
                        return redirect(url_for("admin_add_product"))
                    img_filename = f"img_{int(time.time())}_{secure_filename(img.filename)}"
                    img.save(os.path.join(uploads_dir, img_filename))

            db = get_db()
            cursor = db.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO marketplace_products (title, category, price, summary, instructor_id, pdf_file, image)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (title, cat, price, summary, real_id, pdf_filename, img_filename),
                )
                db.commit()
                log_audit("LIBRARY_ITEM_ADDED", "marketplace_product", title, f"Category: {cat}, Price: {price}")
            finally:
                db.close()

            flash("Product added to library successfully!", "success")
            return redirect(url_for("admin_dashboard"))
        return render_template("admin_add_product.html")

    @app.route("/instructor/grades/export")
    @roles_required("instructor", "system")
    def instructor_grades_export():
        db = get_db()
        cursor = db.cursor()
        try:
            real_id = session.get("real_instructor_id") or _resolve_instructor_id(cursor)
            cursor.execute(
                """
                SELECT sg.id as grade_id, u.student_code, u.full_name as student_name,
                       sg.course_code, sg.course_name, sg.grade, sg.points
                FROM student_grades sg
                JOIN users u ON sg.user_id = u.id
                JOIN courses c ON sg.course_code = c.course_id
                WHERE c.instructor_id = %s
                ORDER BY sg.course_code, u.student_code
                """,
                (real_id,),
            )
            rows = cursor.fetchall()
        finally:
            db.close()

        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["grade_id", "student_code", "student_name", "course_code", "course_name", "grade", "points"])
        for r in rows:
            writer.writerow([r["grade_id"], r["student_code"], r["student_name"], r["course_code"], r["course_name"], r["grade"], r["points"]])
        return Response(
            out.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=course_grades_sheet.csv"},
        )

    @app.route("/instructor/grades/import", methods=["POST"])
    @roles_required("instructor", "system")
    def instructor_grades_import():
        csv_file = request.files.get("grades_csv")
        if not csv_file or not csv_file.filename.lower().endswith(".csv"):
            flash("Please upload a valid .csv grades sheet.", "danger")
            return redirect(url_for("admin_grades"))

        content = csv_file.read().decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(content))
        updated, skipped = 0, 0
        touched_users = set()

        db = get_db()
        cursor = db.cursor()
        try:
            real_id = session.get("real_instructor_id") or _resolve_instructor_id(cursor)
            for row in reader:
                gid = (row.get("grade_id") or "").strip()
                grade_letter = (row.get("grade") or "").strip().upper()
                pts_raw = (row.get("points") or "").strip()
                if not gid.isdigit() or not grade_letter or not pts_raw:
                    skipped += 1
                    continue
                try:
                    pts = round(max(0.0, min(4.0, float(pts_raw))), 2)
                except ValueError:
                    skipped += 1
                    continue

                cursor.execute(
                    """
                    SELECT sg.id, sg.user_id, c.instructor_id
                    FROM student_grades sg
                    JOIN courses c ON sg.course_code = c.course_id
                    WHERE sg.id=%s
                    """,
                    (int(gid),),
                )
                existing = cursor.fetchone()
                if not existing or (session.get("role") != "system" and str(existing["instructor_id"]) != str(real_id)):
                    skipped += 1
                    continue

                cursor.execute("UPDATE student_grades SET grade=%s, points=%s WHERE id=%s", (grade_letter, pts, int(gid)))
                touched_users.add(existing["user_id"])
                updated += 1

            for uid in touched_users:
                _recalculate_student_gpa(cursor, uid)
            db.commit()
            log_audit("GRADES_BULK_IMPORT", "student_grade", "csv", f"Updated {updated} grades across {len(touched_users)} students (skipped {skipped})")
            flash(f"Bulk grades upload complete: {updated} updated, {len(touched_users)} GPAs recalculated, {skipped} skipped.", "success")
        except Exception as e:
            flash(f"Bulk grade import failed: {e}", "danger")
        finally:
            db.close()
        return redirect(url_for("admin_grades"))
