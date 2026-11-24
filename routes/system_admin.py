import csv
import io
import json
import os
from datetime import datetime, date
from flask import render_template, request, session, redirect, url_for, flash, Response
from core.db import get_db, get_db_status
from core.security import roles_required, hash_password, log_audit
from core.config import ENV_PATH, reload_ai_clients


def _json_default(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return str(obj)


def register_system_routes(app):
    @app.route("/system_dashboard")
    @roles_required("system")
    def system_dashboard():
        tenant_scope = session.get("tenant_scope")
        uni_filter = str(tenant_scope) if tenant_scope else request.args.get("university_id", "").strip()
        db = get_db()
        cursor = db.cursor()
        try:
            if tenant_scope:
                cursor.execute("SELECT id, name, slug, primary_color FROM universities WHERE id=%s", (int(tenant_scope),))
            else:
                cursor.execute("SELECT id, name, slug, primary_color FROM universities ORDER BY id ASC")
            universities = cursor.fetchall()

            if uni_filter and uni_filter.isdigit():
                uid = int(uni_filter)
                cursor.execute("SELECT COUNT(*) as c FROM users WHERE university_id=%s", (uid,))
                st_count = cursor.fetchone()["c"]
                cursor.execute("SELECT COUNT(*) as c FROM admins WHERE role='instructor' AND university_id=%s", (uid,))
                ins_count = cursor.fetchone()["c"]
                cursor.execute("SELECT AVG(gpa) as avg_gpa FROM users WHERE gpa > 0 AND university_id=%s", (uid,))
                res = cursor.fetchone()
                avg_gpa = res["avg_gpa"] if res and res["avg_gpa"] else 0
                cursor.execute(
                    "SELECT faculty, COUNT(*) as c FROM users WHERE university_id=%s AND faculty IS NOT NULL AND faculty != '' GROUP BY faculty",
                    (uid,),
                )
                depts = cursor.fetchall()
                cursor.execute(
                    "SELECT full_name, student_code, gpa FROM users WHERE university_id=%s AND gpa IS NOT NULL AND gpa > 0 ORDER BY gpa DESC LIMIT 5",
                    (uid,),
                )
                top_students = cursor.fetchall()
            else:
                cursor.execute("SELECT COUNT(*) as c FROM users")
                st_count = cursor.fetchone()["c"]
                cursor.execute("SELECT COUNT(*) as c FROM admins WHERE role='instructor'")
                ins_count = cursor.fetchone()["c"]
                cursor.execute("SELECT AVG(gpa) as avg_gpa FROM users WHERE gpa > 0")
                res = cursor.fetchone()
                avg_gpa = res["avg_gpa"] if res and res["avg_gpa"] else 0
                cursor.execute(
                    "SELECT faculty, COUNT(*) as c FROM users WHERE faculty IS NOT NULL AND faculty != '' GROUP BY faculty"
                )
                depts = cursor.fetchall()
                cursor.execute(
                    "SELECT full_name, student_code, gpa FROM users WHERE gpa IS NOT NULL AND gpa > 0 ORDER BY gpa DESC LIMIT 5"
                )
                top_students = cursor.fetchall()

            cursor.execute("SELECT COUNT(*) as c FROM courses")
            if uni_filter and uni_filter.isdigit():
                cursor.execute("SELECT COUNT(*) as c FROM courses WHERE university_id=%s", (int(uni_filter),))
            else:
                cursor.execute("SELECT COUNT(*) as c FROM courses")
            cr_count = cursor.fetchone()["c"]

            recent_audits = []
            try:
                cursor.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 6")
                recent_audits = cursor.fetchall()
            except Exception:
                pass

            # Real gender distribution from students table
            gender_counts = [0, 0]
            try:
                cursor.execute("SELECT LOWER(gender) as g, COUNT(*) as c FROM students WHERE gender IS NOT NULL GROUP BY LOWER(gender)")
                for gr in cursor.fetchall():
                    gval = (gr.get("g") or "").strip()
                    if gval in ("male", "m"):
                        gender_counts[0] += gr["c"]
                    elif gval in ("female", "f"):
                        gender_counts[1] += gr["c"]
            except Exception:
                gender_counts = [45, 35]

            demo_leads_count = 0
            try:
                cursor.execute("SELECT COUNT(*) as c FROM demo_requests WHERE status='New'")
                demo_leads_count = cursor.fetchone()["c"]
            except Exception:
                pass

            dept_labels = [d["faculty"] for d in depts]
            dept_counts = [d["c"] for d in depts]
        finally:
            db.close()

        return render_template(
            "system_dashboard.html",
            st_count=st_count,
            ins_count=ins_count,
            cr_count=cr_count,
            uni_count=len(universities),
            universities=universities,
            selected_uni=uni_filter,
            avg_gpa=round(float(avg_gpa), 2),
            dept_labels=dept_labels,
            dept_counts=dept_counts,
            gender_counts=gender_counts,
            top_students=top_students,
            recent_audits=recent_audits,
            demo_leads_count=demo_leads_count,
        )

    # ==========================================================
    # STUDENTS CRUD + BULK CSV IMPORT & EXPORT
    # ==========================================================
    @app.route("/system_students")
    @roles_required("system")
    def system_students():
        q = (request.args.get("q") or "").strip()
        faculty_filter = (request.args.get("faculty") or "").strip()
        tenant_scope = session.get("tenant_scope")
        db = get_db()
        cursor = db.cursor()
        try:
            sql = """
                SELECT u.*, uni.name as university_name
                FROM users u
                LEFT JOIN universities uni ON u.university_id = uni.id
                WHERE 1=1
            """
            params = []
            if tenant_scope:
                sql += " AND u.university_id = %s"
                params.append(int(tenant_scope))
            if q:
                sql += " AND (u.full_name LIKE %s OR u.student_code LIKE %s OR u.email LIKE %s)"
                params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
            if faculty_filter:
                sql += " AND u.faculty = %s"
                params.append(faculty_filter)
            sql += " ORDER BY u.id DESC"
            cursor.execute(sql, tuple(params))
            students = cursor.fetchall()

            if tenant_scope:
                cursor.execute("SELECT id, name FROM universities WHERE id=%s", (int(tenant_scope),))
            else:
                cursor.execute("SELECT id, name FROM universities ORDER BY name ASC")
            universities = cursor.fetchall()
            cursor.execute("SELECT DISTINCT faculty FROM users WHERE faculty IS NOT NULL AND faculty != '' ORDER BY faculty")
            faculties = [r["faculty"] for r in cursor.fetchall()]
        finally:
            db.close()

        return render_template(
            "system_students.html",
            students=students,
            universities=universities,
            faculties=faculties,
            q=q,
            faculty_filter=faculty_filter,
        )

    @app.route("/system/students/add", methods=["POST"])
    @roles_required("system")
    def system_add_student():
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip()
        student_code = (request.form.get("student_code") or "").strip()
        faculty = (request.form.get("faculty") or "Faculty of Commerce").strip()
        level = (request.form.get("level") or "1").strip()
        gpa = request.form.get("gpa") or 0.0
        university_id = request.form.get("university_id") or 3
        raw_pw = (request.form.get("password") or "123456").strip()

        if not full_name or not email or not student_code:
            flash("Full name, email, and student code are required.", "danger")
            return redirect(url_for("system_students"))

        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO users (full_name, email, password, student_code, faculty, level, gpa, university_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (full_name, email, hash_password(raw_pw), student_code, faculty, level, float(gpa), int(university_id)),
            )
            db.commit()
            log_audit("STUDENT_CREATED", "student", student_code, f"Added student {full_name} ({email})")
            flash(f"Student {full_name} added successfully!", "success")
        except Exception as e:
            flash(f"Could not add student: {e}", "danger")
        finally:
            db.close()
        return redirect(url_for("system_students"))

    @app.route("/system/students/<int:student_id>/edit", methods=["POST"])
    @roles_required("system")
    def system_edit_student(student_id):
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip()
        student_code = (request.form.get("student_code") or "").strip()
        faculty = (request.form.get("faculty") or "").strip()
        level = (request.form.get("level") or "").strip()
        gpa = float(request.form.get("gpa") or 0.0)
        university_id = int(request.form.get("university_id") or 3)
        new_pw = (request.form.get("password") or "").strip()

        db = get_db()
        cursor = db.cursor()
        try:
            if new_pw:
                cursor.execute(
                    """
                    UPDATE users SET full_name=%s, email=%s, student_code=%s, faculty=%s, level=%s, gpa=%s, university_id=%s, password=%s
                    WHERE id=%s
                    """,
                    (full_name, email, student_code, faculty, level, gpa, university_id, hash_password(new_pw), student_id),
                )
            else:
                cursor.execute(
                    """
                    UPDATE users SET full_name=%s, email=%s, student_code=%s, faculty=%s, level=%s, gpa=%s, university_id=%s
                    WHERE id=%s
                    """,
                    (full_name, email, student_code, faculty, level, gpa, university_id, student_id),
                )
            db.commit()
            log_audit("STUDENT_UPDATED", "student", student_id, f"Updated {full_name} ({student_code}), GPA={gpa}")
            flash("Student record updated!", "success")
        except Exception as e:
            flash(f"Update failed: {e}", "danger")
        finally:
            db.close()
        return redirect(url_for("system_students"))

    @app.route("/system/students/<int:student_id>/delete", methods=["POST"])
    @roles_required("system")
    def system_delete_student(student_id):
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("SELECT full_name, student_code FROM users WHERE id=%s", (student_id,))
            st = cursor.fetchone()
            cursor.execute("DELETE FROM users WHERE id=%s", (student_id,))
            db.commit()
            if st:
                log_audit("STUDENT_DELETED", "student", student_id, f"Deleted {st['full_name']} ({st['student_code']})")
            flash("Student deleted successfully.", "success")
        except Exception as e:
            flash(f"Delete failed: {e}", "danger")
        finally:
            db.close()
        return redirect(url_for("system_students"))

    @app.route("/system/students/template")
    @roles_required("system")
    def system_students_csv_template():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["student_code", "full_name", "email", "faculty", "level", "gpa", "password", "university_id"])
        writer.writerow(["CU2026001", "Ahmed Tarek Hassan", "ahmed.tarek@cu.edu.eg", "Faculty of Commerce", "1", "3.40", "123456", "3"])
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=eduguide_students_template.csv"},
        )

    @app.route("/system/students/export")
    @roles_required("system")
    def system_students_export():
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("SELECT student_code, full_name, email, faculty, level, gpa, university_id FROM users ORDER BY id DESC")
            rows = cursor.fetchall()
        finally:
            db.close()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["student_code", "full_name", "email", "faculty", "level", "gpa", "university_id"])
        for r in rows:
            writer.writerow([r["student_code"], r["full_name"], r["email"], r["faculty"], r["level"], r["gpa"], r["university_id"]])
        log_audit("STUDENTS_EXPORTED", "student", "bulk", f"Exported {len(rows)} student records to CSV")
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=eduguide_students_export.csv"},
        )

    @app.route("/system/students/import", methods=["POST"])
    @roles_required("system")
    def system_students_import():
        csv_file = request.files.get("csv_file")
        if not csv_file or not csv_file.filename.lower().endswith(".csv"):
            flash("Please upload a valid .csv file.", "danger")
            return redirect(url_for("system_students"))

        content = csv_file.read().decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(content))
        imported, skipped = 0, 0
        default_hash = hash_password("123456")

        db = get_db()
        cursor = db.cursor()
        try:
            for row in reader:
                code = (row.get("student_code") or "").strip()
                name = (row.get("full_name") or "").strip()
                email = (row.get("email") or "").strip()
                if not code or not name or not email:
                    skipped += 1
                    continue
                cursor.execute("SELECT id FROM users WHERE student_code=%s OR email=%s", (code, email))
                if cursor.fetchone():
                    skipped += 1
                    continue
                faculty = (row.get("faculty") or "Faculty of Commerce").strip()
                level = (row.get("level") or "1").strip()
                try:
                    gpa = float(row.get("gpa") or 0.0)
                except ValueError:
                    gpa = 0.0
                try:
                    uni_id = int(row.get("university_id") or 3)
                except ValueError:
                    uni_id = 3
                raw_pw = (row.get("password") or "").strip()
                pw_hash = hash_password(raw_pw) if raw_pw and raw_pw != "123456" else default_hash

                cursor.execute(
                    """
                    INSERT INTO users (full_name, email, password, student_code, faculty, level, gpa, university_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (name, email, pw_hash, code, faculty, level, gpa, uni_id),
                )
                imported += 1
            db.commit()
            log_audit("STUDENTS_BULK_IMPORT", "student", "csv", f"Imported {imported} students, skipped {skipped}")
            flash(f"Bulk import complete: {imported} added, {skipped} skipped (duplicates or invalid).", "success")
        except Exception as e:
            flash(f"Import failed: {e}", "danger")
        finally:
            db.close()
        return redirect(url_for("system_students"))

    # ==========================================================
    # INSTRUCTORS / FACULTY CRUD
    # ==========================================================
    @app.route("/system_faculty")
    @roles_required("system")
    def system_faculty():
        q = (request.args.get("q") or "").strip()
        tenant_scope = session.get("tenant_scope")
        db = get_db()
        cursor = db.cursor()
        try:
            sql = """
                SELECT a.*, uni.name as university_name
                FROM admins a
                LEFT JOIN universities uni ON a.university_id = uni.id
                WHERE a.role='instructor'
            """
            params = []
            if tenant_scope:
                sql += " AND a.university_id = %s"
                params.append(int(tenant_scope))
            if q:
                sql += " AND (a.full_name LIKE %s OR a.email LIKE %s OR a.specialization LIKE %s)"
                params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
            sql += " ORDER BY a.id DESC"
            cursor.execute(sql, tuple(params))
            faculty = cursor.fetchall()
            if tenant_scope:
                cursor.execute("SELECT id, name FROM universities WHERE id=%s", (int(tenant_scope),))
            else:
                cursor.execute("SELECT id, name FROM universities ORDER BY name ASC")
            universities = cursor.fetchall()
        finally:
            db.close()
        return render_template("system_faculty.html", faculty=faculty, universities=universities, q=q)

    @app.route("/system/faculty/add", methods=["POST"])
    @roles_required("system")
    def system_add_faculty():
        tenant_scope = session.get("tenant_scope")
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip()
        username = (request.form.get("username") or email.split("@")[0]).strip()
        specialization = (request.form.get("specialization") or "General").strip()
        university_id = int(request.form.get("university_id") or 3)
        university_id = int(tenant_scope) if tenant_scope else int(request.form.get("university_id") or 3)
        raw_pw = (request.form.get("password") or "123456").strip()

        if not full_name or not email:
            flash("Instructor name and email are required.", "danger")
            return redirect(url_for("system_faculty"))

        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO admins (username, email, password, full_name, role, specialization, university_id)
                VALUES (%s, %s, %s, %s, 'instructor', %s, %s)
                """,
                (username, email, hash_password(raw_pw), full_name, specialization, university_id),
            )
            # Also create/link matching instructors record for course & assignment ownership
            tokens = full_name.split()
            title = "Dr."
            if tokens and tokens[0].lower() in ("dr.", "prof.", "dr", "prof"):
                title = tokens[0] if tokens[0].endswith(".") else tokens[0] + "."
                tokens = tokens[1:]
            fn = tokens[0] if tokens else full_name
            ln = " ".join(tokens[1:]) if len(tokens) > 1 else "Faculty"
            cursor.execute("SELECT instructor_id FROM instructors WHERE LOWER(email)=LOWER(%s)", (email,))
            if not cursor.fetchone():
                cursor.execute(
                    """
                    INSERT INTO instructors (dept_id, first_name, last_name, title, specialisation, email, university_id)
                    VALUES (1, %s, %s, %s, %s, %s, %s)
                    """,
                    (fn, ln, title, specialization, email, university_id),
                )
            db.commit()
            log_audit("INSTRUCTOR_CREATED", "instructor", email, f"Added instructor {full_name} ({specialization})")
            flash(f"Instructor {full_name} added successfully!", "success")
        except Exception as e:
            flash(f"Could not add instructor: {e}", "danger")
        finally:
            db.close()
        return redirect(url_for("system_faculty"))

    @app.route("/system/faculty/<int:instructor_id>/edit", methods=["POST"])
    @roles_required("system")
    def system_edit_faculty(instructor_id):
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip()
        specialization = (request.form.get("specialization") or "").strip()
        university_id = int(request.form.get("university_id") or 3)
        new_pw = (request.form.get("password") or "").strip()

        db = get_db()
        cursor = db.cursor()
        try:
            if new_pw:
                cursor.execute(
                    "UPDATE admins SET full_name=%s, email=%s, specialization=%s, university_id=%s, password=%s WHERE id=%s",
                    (full_name, email, specialization, university_id, hash_password(new_pw), instructor_id),
                )
            else:
                cursor.execute(
                    "UPDATE admins SET full_name=%s, email=%s, specialization=%s, university_id=%s WHERE id=%s",
                    (full_name, email, specialization, university_id, instructor_id),
                )
            db.commit()
            log_audit("INSTRUCTOR_UPDATED", "instructor", instructor_id, f"Updated {full_name}")
            flash("Instructor updated successfully!", "success")
        except Exception as e:
            flash(f"Update failed: {e}", "danger")
        finally:
            db.close()
        return redirect(url_for("system_faculty"))

    @app.route("/system/faculty/<int:instructor_id>/delete", methods=["POST"])
    @roles_required("system")
    def system_delete_faculty(instructor_id):
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("SELECT full_name, email FROM admins WHERE id=%s", (instructor_id,))
            ins = cursor.fetchone()
            cursor.execute("DELETE FROM admins WHERE id=%s AND role='instructor'", (instructor_id,))
            db.commit()
            if ins:
                log_audit("INSTRUCTOR_DELETED", "instructor", instructor_id, f"Deleted {ins['full_name']} ({ins['email']})")
            flash("Instructor removed.", "success")
        except Exception as e:
            flash(f"Delete failed: {e}", "danger")
        finally:
            db.close()
        return redirect(url_for("system_faculty"))

    # ==========================================================
    # COURSES CRUD
    # ==========================================================
    @app.route("/system_courses")
    @roles_required("system")
    def system_courses():
        q = (request.args.get("q") or "").strip()
        tenant_scope = session.get("tenant_scope")
        db = get_db()
        cursor = db.cursor()
        try:
            sql = """
                SELECT c.*, d.name as dept_name,
                       i.title as inst_title, i.first_name, i.last_name
                FROM courses c
                LEFT JOIN departments d ON c.dept_id = d.dept_id
                LEFT JOIN instructors i ON c.instructor_id = i.instructor_id
                WHERE 1=1
            """
            params = []
            if tenant_scope:
                sql += " AND c.university_id = %s"
                params.append(int(tenant_scope))
            if q:
                sql += " AND (c.course_id LIKE %s OR c.name LIKE %s)"
                params.extend([f"%{q}%", f"%{q}%"])
            sql += " ORDER BY c.course_id ASC"
            cursor.execute(sql, tuple(params))
            courses = cursor.fetchall()
            for c in courses:
                c["instructor_name"] = " ".join(
                    p for p in [c.get("inst_title"), c.get("first_name"), c.get("last_name")] if p
                ) or "Unassigned"

            cursor.execute("SELECT dept_id, name FROM departments ORDER BY name ASC")
            departments = cursor.fetchall()
            cursor.execute("SELECT instructor_id, title, first_name, last_name FROM instructors ORDER BY first_name ASC")
            if tenant_scope:
                cursor.execute(
                    "SELECT instructor_id, title, first_name, last_name FROM instructors WHERE university_id=%s ORDER BY first_name ASC",
                    (int(tenant_scope),),
                )
            else:
                cursor.execute("SELECT instructor_id, title, first_name, last_name FROM instructors ORDER BY first_name ASC")
            instructors = cursor.fetchall()
            for ins in instructors:
                ins["full_name"] = " ".join(p for p in [ins.get("title"), ins.get("first_name"), ins.get("last_name")] if p)
        finally:
            db.close()
        return render_template("system_courses.html", courses=courses, departments=departments, instructors=instructors, q=q)

    @app.route("/system/courses/add", methods=["POST"])
    @roles_required("system")
    def system_add_course():
        tenant_scope = session.get("tenant_scope")
        course_id = (request.form.get("course_id") or "").strip().upper()
        name = (request.form.get("name") or "").strip()
        dept_id = int(request.form.get("dept_id") or 1)
        year_level = int(request.form.get("year_level") or 1)
        instructor_id = int(request.form.get("instructor_id") or 1)
        credit_hours = int(request.form.get("credit_hours") or 3)
        semester = (request.form.get("semester") or "Fall").strip()
        max_capacity = int(request.form.get("max_capacity") or 100)
        description = (request.form.get("description") or "").strip()
        uni_id = int(tenant_scope) if tenant_scope else int(request.form.get("university_id") or 3)

        if not course_id or not name:
            flash("Course code and name are required.", "danger")
            return redirect(url_for("system_courses"))

        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO courses (course_id, dept_id, year_level, instructor_id, name, credit_hours, semester, max_capacity, description)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                INSERT INTO courses (course_id, dept_id, year_level, instructor_id, name, credit_hours, semester, max_capacity, description, university_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (course_id, dept_id, year_level, instructor_id, name, credit_hours, semester, max_capacity, description),
                (course_id, dept_id, year_level, instructor_id, name, credit_hours, semester, max_capacity, description, uni_id),
            )
            db.commit()
            log_audit("COURSE_CREATED", "course", course_id, f"Added {course_id}: {name} ({credit_hours} hrs)")
            flash(f"Course {course_id} added!", "success")
        except Exception as e:
            flash(f"Could not add course: {e}", "danger")
        finally:
            db.close()
        return redirect(url_for("system_courses"))

    @app.route("/system/courses/<course_id>/edit", methods=["POST"])
    @roles_required("system")
    def system_edit_course(course_id):
        name = (request.form.get("name") or "").strip()
        dept_id = int(request.form.get("dept_id") or 1)
        year_level = int(request.form.get("year_level") or 1)
        instructor_id = int(request.form.get("instructor_id") or 1)
        credit_hours = int(request.form.get("credit_hours") or 3)
        semester = (request.form.get("semester") or "Fall").strip()
        max_capacity = int(request.form.get("max_capacity") or 100)

        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute(
                """
                UPDATE courses
                SET name=%s, dept_id=%s, year_level=%s, instructor_id=%s, credit_hours=%s, semester=%s, max_capacity=%s
                WHERE course_id=%s
                """,
                (name, dept_id, year_level, instructor_id, credit_hours, semester, max_capacity, course_id),
            )
            db.commit()
            log_audit("COURSE_UPDATED", "course", course_id, f"Updated {course_id}: {name}")
            flash(f"Course {course_id} updated!", "success")
        except Exception as e:
            flash(f"Update failed: {e}", "danger")
        finally:
            db.close()
        return redirect(url_for("system_courses"))

    @app.route("/system/courses/<course_id>/delete", methods=["POST"])
    @roles_required("system")
    def system_delete_course(course_id):
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("DELETE FROM courses WHERE course_id=%s", (course_id,))
            db.commit()
            log_audit("COURSE_DELETED", "course", course_id, f"Deleted course {course_id}")
            flash(f"Course {course_id} deleted.", "success")
        except Exception as e:
            flash(f"Cannot delete course (may have active enrollments): {e}", "danger")
        finally:
            db.close()
        return redirect(url_for("system_courses"))

    # ==========================================================
    # FACULTIES & DEPARTMENTS CRUD
    # ==========================================================
    @app.route("/system_departments")
    @roles_required("system")
    def system_departments():
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute(
                """
                SELECT d.*, f.name as faculty_name,
                       (SELECT COUNT(*) FROM courses c WHERE c.dept_id = d.dept_id) as course_count,
                       (SELECT COUNT(*) FROM students s WHERE s.dept_id = d.dept_id) as student_count
                FROM departments d
                LEFT JOIN faculty f ON d.faculty_id = f.faculty_id
                ORDER BY d.dept_id ASC
                """
            )
            depts = cursor.fetchall()
            cursor.execute(
                """
                SELECT f.*, u.name as university_name
                FROM faculty f
                LEFT JOIN universities u ON f.university_id = u.id
                ORDER BY f.faculty_id ASC
                """
            )
            faculties = cursor.fetchall()
            cursor.execute("SELECT id, name FROM universities ORDER BY name ASC")
            universities = cursor.fetchall()
        finally:
            db.close()
        return render_template("system_departments.html", depts=depts, faculties=faculties, universities=universities)

    @app.route("/system/departments/add", methods=["POST"])
    @roles_required("system")
    def system_add_department():
        name = (request.form.get("name") or "").strip()
        faculty_id = int(request.form.get("faculty_id") or 1)
        head_of_dept = (request.form.get("head_of_dept") or "").strip()
        description = (request.form.get("description") or "").strip()
        if not name:
            flash("Department name is required.", "danger")
            return redirect(url_for("system_departments"))

        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute(
                "INSERT INTO departments (faculty_id, name, head_of_dept, description) VALUES (%s, %s, %s, %s)",
                (faculty_id, name, head_of_dept, description),
            )
            db.commit()
            log_audit("DEPARTMENT_CREATED", "department", name, f"Faculty #{faculty_id}, Head: {head_of_dept}")
            flash(f"Department '{name}' added!", "success")
        except Exception as e:
            flash(f"Could not add department: {e}", "danger")
        finally:
            db.close()
        return redirect(url_for("system_departments"))

    @app.route("/system/departments/<int:dept_id>/delete", methods=["POST"])
    @roles_required("system")
    def system_delete_department(dept_id):
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("DELETE FROM departments WHERE dept_id=%s", (dept_id,))
            db.commit()
            log_audit("DEPARTMENT_DELETED", "department", dept_id, f"Deleted department #{dept_id}")
            flash("Department deleted.", "success")
        except Exception as e:
            flash(f"Cannot delete department (linked records exist): {e}", "danger")
        finally:
            db.close()
        return redirect(url_for("system_departments"))

    @app.route("/system/faculties/add", methods=["POST"])
    @roles_required("system")
    def system_add_faculty_unit():
        name = (request.form.get("name") or "").strip()
        dean = (request.form.get("dean") or "").strip()
        university_id = int(request.form.get("university_id") or 3)
        location = (request.form.get("location") or "").strip()
        email = (request.form.get("email") or "").strip()
        if not name:
            flash("Faculty name is required.", "danger")
            return redirect(url_for("system_departments"))

        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute(
                "INSERT INTO faculty (name, dean, location, email, university_id) VALUES (%s, %s, %s, %s, %s)",
                (name, dean, location, email, university_id),
            )
            db.commit()
            log_audit("FACULTY_CREATED", "faculty", name, f"University #{university_id}, Dean: {dean}")
            flash(f"Faculty '{name}' created!", "success")
        except Exception as e:
            flash(f"Could not create faculty: {e}", "danger")
        finally:
            db.close()
        return redirect(url_for("system_departments"))

    # ==========================================================
    # MULTI-TENANT UNIVERSITIES CRUD
    # ==========================================================
    @app.route("/system_universities")
    @roles_required("system")
    def system_universities():
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute(
                """
                SELECT u.*,
                       (SELECT COUNT(*) FROM users s WHERE s.university_id = u.id) as student_count,
                       (SELECT COUNT(*) FROM admins a WHERE a.university_id = u.id AND a.role='instructor') as instructor_count
                FROM universities u
                ORDER BY u.id ASC
                """
            )
            universities = cursor.fetchall()
        finally:
            db.close()
        return render_template("system_universities.html", universities=universities)

    @app.route("/system/universities/add", methods=["POST"])
    @roles_required("system")
    def system_add_university():
        name = (request.form.get("name") or "").strip()
        slug = (request.form.get("slug") or "").strip().lower()
        primary_color = (request.form.get("primary_color") or "#4f46e5").strip()
        secondary_color = (request.form.get("secondary_color") or "#3730a3").strip()
        admin_email = (request.form.get("admin_email") or "").strip()
        admin_password = (request.form.get("admin_password") or "123456").strip()

        if not name or not slug:
            flash("University name and slug are required.", "danger")
            return redirect(url_for("system_universities"))

        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO universities (name, slug, primary_color, secondary_color, accent_color, bg_color, admin_email, admin_password)
                VALUES (%s, %s, %s, %s, %s, '#f8fafc', %s, %s)
                """,
                (name, slug, primary_color, secondary_color, primary_color, admin_email, hash_password(admin_password)),
            )
            db.commit()
            log_audit("UNIVERSITY_CREATED", "university", slug, f"Added university {name} ({admin_email})")
            flash(f"University '{name}' onboarded! Portal URL: /u/{slug}", "success")
        except Exception as e:
            flash(f"Could not add university: {e}", "danger")
        finally:
            db.close()
        return redirect(url_for("system_universities"))

    @app.route("/system/universities/<int:uni_id>/delete", methods=["POST"])
    @roles_required("system")
    def system_delete_university(uni_id):
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("DELETE FROM universities WHERE id=%s", (uni_id,))
            db.commit()
            log_audit("UNIVERSITY_DELETED", "university", uni_id, f"Deleted university #{uni_id}")
            flash("University removed.", "success")
        except Exception as e:
            flash(f"Delete failed: {e}", "danger")
        finally:
            db.close()
        return redirect(url_for("system_universities"))

    # ==========================================================
    # AUDIT LOGS VIEWER
    # ==========================================================
    @app.route("/system_audit")
    @roles_required("system")
    def system_audit():
        action_filter = (request.args.get("action") or "").strip()
        db = get_db()
        cursor = db.cursor()
        try:
            if action_filter:
                cursor.execute(
                    "SELECT * FROM audit_logs WHERE action=%s ORDER BY id DESC LIMIT 200",
                    (action_filter,),
                )
            else:
                cursor.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 200")
            logs = cursor.fetchall()
            cursor.execute("SELECT DISTINCT action FROM audit_logs ORDER BY action ASC")
            actions = [r["action"] for r in cursor.fetchall()]
        finally:
            db.close()
        return render_template("system_audit.html", logs=logs, actions=actions, action_filter=action_filter)

    # ==========================================================
    # DATABASE STATUS & REAL BACKUP DOWNLOAD
    # ==========================================================
    @app.route("/system_database")
    @roles_required("system")
    def system_database():
        db_info = get_db_status()
        return render_template("system_database.html", db_info=db_info)

    @app.route("/system/database/backup")
    @roles_required("system")
    def system_database_backup():
        db = get_db()
        cursor = db.cursor()
        export_tables = [
            "universities", "faculty", "departments", "courses",
            "users", "admins", "enrollments", "student_grades",
            "payments", "student_requests", "audit_logs",
        ]
        snapshot = {"exported_at": datetime.utcnow().isoformat() + "Z", "tables": {}}
        try:
            for tbl in export_tables:
                try:
                    cursor.execute(f"SELECT * FROM `{tbl}`")
                    rows = cursor.fetchall()
                    # Scrub password hashes from exported JSON backup for safety
                    for r in rows:
                        if "password" in r:
                            r["password"] = "[REDACTED]"
                        if "admin_password" in r:
                            r["admin_password"] = "[REDACTED]"
                    snapshot["tables"][tbl] = rows
                except Exception:
                    pass
        finally:
            db.close()

        log_audit("DATABASE_BACKUP", "database", "snapshot", f"Downloaded JSON backup ({len(snapshot['tables'])} tables)")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return Response(
            json.dumps(snapshot, default=_json_default, ensure_ascii=False, indent=2),
            mimetype="application/json",
            headers={"Content-Disposition": f"attachment; filename=eduguide_backup_{ts}.json"},
        )

    @app.route("/system/interviews")
    @roles_required("system")
    def manage_interviews():
        return render_template("admin_interviews.html", is_ar=(session.get("lang") == "ar"))

    # ==========================================================
    # SYSTEM INTEGRATIONS (RESTRICTED TO SYSTEM ADMIN)
    # ==========================================================
    @app.route("/admin/integrations", methods=["GET", "POST"])
    @roles_required("system")
    def admin_integrations():
        managed_keys = [
            "GROQ_API_KEY",
            "GEMINI_API_KEY",
            "PAYMOB_API_KEY",
            "PAYMOB_INTEGRATION_ID_CARD",
            "PAYMOB_INTEGRATION_ID_FAWRY",
        ]
        if request.method == "POST":
            new_keys = {}
            for k in managed_keys:
                val = (request.form.get(k) or "").strip()
                if val and not val.startswith("••••"):
                    new_keys[k] = val

            lines = []
            if os.path.exists(ENV_PATH):
                with open(ENV_PATH, "r", encoding="utf-8") as f:
                    lines = f.readlines()

            with open(ENV_PATH, "w", encoding="utf-8") as f:
                written = set()
                for line in lines:
                    if "=" in line:
                        key = line.split("=", 1)[0].strip()
                        if key in new_keys:
                            f.write(f"{key}={new_keys[key]}\n")
                            written.add(key)
                        else:
                            f.write(line)
                    else:
                        f.write(line)
                for key, val in new_keys.items():
                    if key not in written:
                        f.write(f"{key}={val}\n")

            reload_ai_clients()
            log_audit("INTEGRATIONS_UPDATED", "system", "env", f"Updated keys: {', '.join(new_keys.keys()) or 'none'}")
            flash("Integration settings updated and reloaded!", "success")
            return redirect(url_for("admin_integrations"))

        # Mask sensitive keys for UI display
        keys = {}
        for k in managed_keys:
            val = os.getenv(k, "")
            if val and "KEY" in k and len(val) > 8:
                keys[k] = "••••••••" + val[-6:]
            else:
                keys[k] = val
        return render_template("admin_integrations.html", keys=keys)
