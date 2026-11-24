import os
import time
from werkzeug.utils import secure_filename
from flask import render_template, request, session, redirect, url_for, flash, abort
from core.db import get_db
from core.security import verify_password, roles_required, rate_limit, log_audit

ALLOWED_IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".svg"}

# Realistic institutional profiles for authentic university portals
UNIVERSITY_PROFILES = {
    "cu": {
        "name_ar": "جامعة القاهرة",
        "founded": "1908",
        "campus_en": "Giza Main Campus, Egypt",
        "campus_ar": "الحرم الجامعي الرئيسي — الجيزة، مصر",
        "motto_en": "Pioneer of Higher Education & Scientific Research in the Middle East",
        "motto_ar": "رائدة التعليم العالي والبحث العلمي في مصر والشرق الأوسط منذ عام ١٩٠٨",
        "welcome_en": "Welcome to the Cairo University Unified Digital Portal. Access your academic schedule, credit-hour registration, verified official transcripts, tuition services, and smart advising in one secure campus system.",
        "welcome_ar": "مرحباً بكم في البوابة الرقمية الموحدة لجامعة القاهرة. تابع جداولك الدراسية، تسجيل المقررات بالساعات المعتمدة، بيان الدرجات الموثق، المصروفات الدراسية، والإرشاد الأكاديمي الذكي في مكان واحد.",
        "notice_en": "Fall 2026–2027 Registration & QR Lecture Attendance are now active across faculties.",
        "notice_ar": "تسجيل مقررات الفصل الدراسي الأول ٢٠٢٦–٢٠٢٧ ونظام الحضور الذكي بالـ QR مفعّلان الآن بجميع الكليات.",
    },
    "auc": {
        "name_ar": "الجامعة الأمريكية بالقاهرة",
        "founded": "1919",
        "campus_en": "New Cairo Campus, Egypt",
        "campus_ar": "الحرم الجامعي — القاهرة الجديدة، مصر",
        "motto_en": "Where Global Education Meets Leadership & Innovation",
        "motto_ar": "حيث يلتقي التعليم العالمي بالقيادة والابتكار والتميز الأكاديمي منذ عام ١٩١٩",
        "welcome_en": "Welcome to The American University in Cairo Smart Campus Portal. Sign in with your AUC credentials to manage courses, faculty advising, career readiness, digital campus ID, and academic records.",
        "welcome_ar": "أهلاً بكم في البوابة الذكية للجامعة الأمريكية بالقاهرة. سجّل الدخول ببياناتك الجامعية لإدارة المقررات، الإرشاد الأكاديمي، الاستعداد المهني، الكارنيه الرقمي، وسجلك الدراسي.",
        "notice_en": "Career Center AI CV Review & Mock Interview sessions are open for Fall 2026 students.",
        "notice_ar": "خدمات مركز التوظيف الذكي لمراجعة السيرة الذاتية والمقابلات التدريبية متاحة الآن لطلاب خريف ٢٠٢٦.",
    },
    "guc": {
        "name_ar": "الجامعة الألمانية بالقاهرة",
        "founded": "2002",
        "campus_en": "New Cairo City, Egypt",
        "campus_ar": "القاهرة الجديدة — مصر",
        "motto_en": "Excellence in German Academic Standards, Engineering & Management",
        "motto_ar": "التميز في المعايير الأكاديمية الألمانية والبحث التطبيقي والإدارة منذ عام ٢٠٠٢",
        "welcome_en": "Welcome to the German University in Cairo Digital Campus. Access your courses, lab assignments, timed online quizzes, attendance standing, and official academic transcripts.",
        "welcome_ar": "مرحباً بكم في المنصة الرقمية للجامعة الألمانية بالقاهرة. تابع مقرراتك، التكليفات العملية، الاختبارات الأونلاين، نسبة الحضور والغياب، وبياناتك الأكاديمية المعتمدة.",
        "notice_en": "Academic Integrity & 25% Attendance Policy tracking is enabled for all active courses.",
        "notice_ar": "متابعة لائحة الحضور والغياب (حد الحرمان ٢٥٪) والتكليفات الدراسية مفعّلة لجميع المقررات الحالية.",
    },
    "asu": {
        "name_ar": "جامعة عين شمس",
        "founded": "1950",
        "campus_en": "Abbassia, Cairo, Egypt",
        "campus_ar": "العباسية — القاهرة، مصر",
        "motto_en": "Heritage, Scientific Excellence & Digital Campus Transformation",
        "motto_ar": "عراقة وتميز علمي وتحول رقمي متكامل لخدمة طلاب وباحثي جامعة عين شمس منذ ١٩٥٠",
        "welcome_en": "Welcome to the Ain Shams University Smart Portal. Manage course enrollment, exam timetables, student services, tuition billing, and verified academic records securely.",
        "welcome_ar": "أهلاً بكم في بوابة جامعة عين شمس الذكية. أدر تسجيل المقررات، جداول الامتحانات، الخدمات الطلابية، المصروفات الدراسية، وبيان الدرجات الرسمي بأمان كامل.",
        "notice_en": "Official QR-verified digital transcripts and electronic fee receipts are available 24/7.",
        "notice_ar": "استخراج بيان الدرجات الموثق بـ QR وإيصالات سداد المصروفات الإلكترونية متاحة على مدار الساعة.",
    },
    "alex": {
        "name_ar": "جامعة الإسكندرية",
        "founded": "1938",
        "campus_en": "Shatby, Alexandria, Egypt",
        "campus_ar": "الشاطبي — الإسكندرية، مصر",
        "motto_en": "A Mediterranean Beacon of Knowledge, Culture & Research",
        "motto_ar": "منارة العلم والمعرفة والبحث العلمي على ساحل البحر المتوسط منذ عام ١٩٣٨",
        "welcome_en": "Welcome to Alexandria University Digital Campus Portal. Connect with faculty, register courses, check exam halls, and track your cumulative GPA effortlessly.",
        "welcome_ar": "مرحباً بكم في البوابة الرقمية لجامعة الإسكندرية. تواصل مع أعضاء هيئة التدريس، سجّل مقرراتك، تابع لجان الامتحانات، واحسب معدلك التراكمي بسهولة.",
        "notice_en": "New Digital Student ID Cards with Gate & Library QR verification are now live.",
        "notice_ar": "الكارنيه الجامعي الرقمي المزود بكود QR لدخول الحرم والمكتبة واللجان متاح الآن لجميع الطلاب.",
    },
    "aast": {
        "name_ar": "الأكاديمية العربية للعلوم والتكنولوجيا والنقل البحري",
        "founded": "1972",
        "campus_en": "Abu Qir, Alexandria & Smart Village, Egypt",
        "campus_ar": "أبو قير بالإسكندرية والقرية الذكية — مصر",
        "motto_en": "League of Arab States Specialized Institution for Innovation & Maritime Excellence",
        "motto_ar": "صرح جامعة الدول العربية للعلوم والتكنولوجيا والنقل البحري والابتكار منذ عام ١٩٧٢",
        "welcome_en": "Welcome to the AASTMT Unified Smart Portal. Access academic advising, online assessments, QR lecture check-in, tuition payments, and international career services.",
        "welcome_ar": "مرحباً بكم في البوابة الذكية الموحدة للأكاديمية العربية. تابع الإرشاد الأكاديمي، الاختبارات الأونلاين، تسجيل حضور المحاضرات، سداد المصروفات، وخدمات التوظيف.",
        "notice_en": "Fall 2026 Credit-Hour Advising & Online Assessment Portal is open.",
        "notice_ar": "بوابة الإرشاد الأكاديمي للساعات المعتمدة والاختبارات الإلكترونية مفتوحة للفصل الدراسي الحالي.",
    },
    "eelu": {
        "name_ar": "الجامعة المصرية للتعلم الإلكتروني الأهلية (EELU)",
        "founded": "2008",
        "campus_en": "Dokki, Giza & 15 Study Centers Across Egypt",
        "campus_ar": "الدقي، الجيزة و١٥ مركزاً دراسياً في أنحاء الجمهورية",
        "motto_en": "Egypt's Pioneer National University for Blended & Smart E-Learning",
        "motto_ar": "الجامعة الأهلية الرائدة في التعلم الإلكتروني المدمج والتحول الرقمي في مصر منذ ٢٠٠٨",
        "welcome_en": "Welcome to the Egyptian E-Learning University (EELU) Smart Portal. Access your blended courses, online timed quizzes, assignments, QR attendance, verified transcripts, and AI career advising from any study center across Egypt.",
        "welcome_ar": "مرحباً بكم في البوابة الذكية للجامعة المصرية للتعلم الإلكتروني الأهلية (EELU). تابع مقرراتك المدمجة، الاختبارات الأونلاين، التكليفات، الحضور بالـ QR، بيان الدرجات الموثق، والمرشد الأكاديمي الذكي من أي مركز دراسي.",
        "notice_en": "Fall 2026–2027 Blended Learning Portal, Online Quizzes & AI Academic Advising are live across all 15 study centers.",
        "notice_ar": "بوابة التعلم المدمج، الاختبارات الإلكترونية، والإرشاد الأكاديمي الذكي مفعّلة الآن في جميع المراكز الدراسية الـ ١٥.",
    },
}


def _enrich_university(app, uni: dict) -> dict:
    if not uni:
        return uni
    uni = dict(uni)
    uni_dir = os.path.join(app.static_folder, "images", "universities")
    logo_name = uni.get("logo") or ""
    bg_name = uni.get("background_image") or ""
    uni["logo_url"] = (
        url_for("static", filename=f"images/universities/{logo_name}")
        if logo_name and os.path.exists(os.path.join(uni_dir, logo_name))
        else None
    )
    uni["bg_url"] = (
        url_for("static", filename=f"images/universities/{bg_name}")
        if bg_name and os.path.exists(os.path.join(uni_dir, bg_name))
        else None
    )
    words = [w for w in (uni.get("name") or "U").split() if w[0].isalpha()]
    uni["initials"] = "".join(w[0].upper() for w in words[:3]) or "UNI"

    slug = (uni.get("slug") or "").lower()
    prof = UNIVERSITY_PROFILES.get(slug, {})
    uni["name_ar"] = prof.get("name_ar") or uni["name"]
    uni["founded"] = prof.get("founded") or "2026"
    uni["campus_en"] = prof.get("campus_en") or "Main University Campus, Egypt"
    uni["campus_ar"] = prof.get("campus_ar") or "الحرم الجامعي الرئيسي، مصر"
    uni["motto_en"] = prof.get("motto_en") or "Smart Digital Campus & Academic Excellence"
    uni["motto_ar"] = prof.get("motto_ar") or "منظومة جامعية ذكية للتميز الأكاديمي والمهني"
    uni["welcome_en"] = uni.get("welcome_tagline") or prof.get("welcome_en") or (
        f"Welcome to the official {uni['name']} Smart Portal. Sign in with your institutional credentials to access courses, grades, attendance, and AI advising."
    )
    uni["welcome_ar"] = prof.get("welcome_ar") or (
        f"مرحباً بكم في البوابة الرقمية الرسمية لـ {uni['name_ar']}. سجّل الدخول بحسابك الجامعي لمتابعة مقرراتك ودرجاتك وحضورك والإرشاد الأكاديمي."
    )
    uni["notice_en"] = prof.get("notice_en") or "Academic Term 2026–2027 digital services & QR attendance are live."
    uni["notice_ar"] = prof.get("notice_ar") or "الخدمات الرقمية للعام الدراسي ٢٠٢٦–٢٠٢٧ ونظام الحضور بالـ QR مفعّلان الآن."
    return uni


def _handle_university_portal(app, slug):
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("SELECT * FROM universities WHERE LOWER(slug)=%s", (slug.lower(),))
        uni = cursor.fetchone()
        if not uni:
            abort(404)

        uni = _enrich_university(app, uni)
        uid = uni["id"]

        cursor.execute("SELECT COUNT(*) as c FROM users WHERE university_id=%s", (uid,))
        st_count = cursor.fetchone()["c"]
        cursor.execute("SELECT COUNT(*) as c FROM admins WHERE role='instructor' AND university_id=%s", (uid,))
        ins_count = cursor.fetchone()["c"]
        cursor.execute("SELECT COUNT(*) as c FROM faculty WHERE university_id=%s", (uid,))
        fac_count = max(1, cursor.fetchone()["c"])
        cursor.execute("SELECT COUNT(*) as c FROM courses")
        cr_count = cursor.fetchone()["c"]

        session["portal_university_id"] = uid
        session["portal_university_slug"] = uni["slug"]

        default_role = request.values.get("role", "student")
        if default_role not in ("student", "instructor", "system"):
            default_role = "student"

        if request.method == "POST":
            identifier = (request.form.get("identifier") or "").strip()
            password = (request.form.get("password") or "").strip()
            selected_role = default_role

            # ROLE: UNIVERSITY SYSTEM ADMIN (DEAN / UNIVERSITY IT)
            if selected_role == "system":
                if (
                    uni.get("admin_email")
                    and identifier.lower() == uni["admin_email"].strip().lower()
                    and verify_password(uni.get("admin_password", ""), password)
                ):
                    lang = session.get("lang", "en")
                    session.clear()
                    session["lang"] = lang
                    session["role"] = "system"
                    session["is_super_admin"] = False
                    session["tenant_scope"] = uid
                    session["university_id"] = uid
                    session["portal_university_slug"] = uni["slug"]
                    session["name"] = f"{uni['name']} Admin"
                    session["user_id"] = f"uni_admin_{uid}"
                    log_audit("UNI_ADMIN_LOGIN", "university", uni["slug"], f"Dean/Admin logged into {uni['name']}")
                    return redirect(url_for("system_dashboard"))

                cursor.execute("SELECT * FROM system_users WHERE email=%s OR username=%s", (identifier, identifier))
                sys_user = cursor.fetchone()
                if sys_user and verify_password(sys_user["password"], password):
                    lang = session.get("lang", "en")
                    session.clear()
                    session["lang"] = lang
                    session["role"] = "system"
                    session["is_super_admin"] = True
                    session["tenant_scope"] = None
                    session["university_id"] = uid
                    session["portal_university_slug"] = uni["slug"]
                    session["name"] = sys_user["full_name"]
                    session["user_id"] = f"sys_{sys_user['id']}"
                    log_audit("SUPERADMIN_UNI_LOGIN", "university", uni["slug"], f"Super Admin entered {uni['name']}")
                    return redirect(url_for("system_dashboard", university_id=uid))

                flash("Invalid university administrator credentials.", "danger")
                return render_template(
                    "university_welcome.html",
                    uni=uni, st_count=st_count, ins_count=ins_count, fac_count=fac_count, cr_count=cr_count,
                    default_role=selected_role,
                )

            # ROLE: INSTRUCTOR (TENANT-ISOLATED)
            if selected_role == "instructor":
                cursor.execute(
                    "SELECT * FROM admins WHERE (email=%s OR username=%s) AND (role='instructor' OR role IS NULL)",
                    (identifier, identifier),
                )
                instructor = cursor.fetchone()
                if instructor and verify_password(instructor["password"], password):
                    inst_uni = instructor.get("university_id") or 3
                    if int(inst_uni) != int(uid):
                        flash(f"This instructor account belongs to another university, not {uni['name']}.", "danger")
                        return render_template(
                            "university_welcome.html",
                            uni=uni, st_count=st_count, ins_count=ins_count, fac_count=fac_count, cr_count=cr_count,
                            default_role=selected_role,
                        )
                    lang = session.get("lang", "en")
                    session.clear()
                    session["lang"] = lang
                    session["role"] = "instructor"
                    session["name"] = instructor["full_name"]
                    session["admin_name"] = instructor["full_name"]
                    session["user_id"] = f"ins_{instructor['id']}"
                    session["university_id"] = uid
                    session["portal_university_slug"] = uni["slug"]
                    log_audit("UNI_INSTRUCTOR_LOGIN", "instructor", instructor["id"], f"Logged into {uni['name']}")
                    return redirect(url_for("admin_dashboard"))

                if int(uid) == 3:
                    cursor.execute("SELECT * FROM facusers WHERE email=%s OR user_id=%s", (identifier, identifier))
                    fac_user = cursor.fetchone()
                    if fac_user and verify_password(fac_user["password"], password):
                        cursor.execute("SELECT * FROM instructors WHERE instructor_id=%s OR email=%s", (fac_user["user_id"], identifier))
                        irow = cursor.fetchone()
                        if irow:
                            lang = session.get("lang", "en")
                            session.clear()
                            session["lang"] = lang
                            session["role"] = "instructor"
                            session["name"] = f"{irow['first_name']} {irow['last_name']}"
                            session["admin_name"] = session["name"]
                            session["user_id"] = fac_user["user_id"]
                            session["university_id"] = uid
                            session["portal_university_slug"] = uni["slug"]
                            log_audit("UNI_INSTRUCTOR_LOGIN", "instructor", fac_user["user_id"], f"Logged into {uni['name']}")
                            return redirect(url_for("admin_dashboard"))

                flash("Invalid instructor credentials for this university.", "danger")
                return render_template(
                    "university_welcome.html",
                    uni=uni, st_count=st_count, ins_count=ins_count, fac_count=fac_count, cr_count=cr_count,
                    default_role=selected_role,
                )

            # ROLE: STUDENT (TENANT-ISOLATED)
            cursor.execute("SELECT * FROM users WHERE email=%s OR student_code=%s", (identifier, identifier))
            local_user = cursor.fetchone()
            if local_user and verify_password(local_user["password"], password):
                st_uni = local_user.get("university_id") or 3
                if int(st_uni) != int(uid):
                    flash(f"Access denied: this student account is not registered under {uni['name']}.", "danger")
                    return render_template(
                        "university_welcome.html",
                        uni=uni, st_count=st_count, ins_count=ins_count, fac_count=fac_count, cr_count=cr_count,
                        default_role=selected_role,
                    )
                lang = session.get("lang", "en")
                session.clear()
                session["lang"] = lang
                session["role"] = "student"
                session["name"] = local_user["full_name"]
                session["user_id"] = local_user["student_code"]
                session["profile_pic"] = local_user.get("profile_pic", "")
                session["university_id"] = uid
                session["portal_university_slug"] = uni["slug"]
                log_audit("UNI_STUDENT_LOGIN", "student", local_user["student_code"], f"Logged into {uni['name']}")
                return redirect(url_for("dashboard"))

            flash("Invalid student email/code or password for this university.", "danger")
            return render_template(
                "university_welcome.html",
                uni=uni, st_count=st_count, ins_count=ins_count, fac_count=fac_count, cr_count=cr_count,
                default_role=selected_role,
            )

        return render_template(
            "university_welcome.html",
            uni=uni, st_count=st_count, ins_count=ins_count, fac_count=fac_count, cr_count=cr_count,
            default_role=default_role,
        )
    finally:
        db.close()


def register_university_portal_routes(app):
    @app.route("/u/<slug>", methods=["GET", "POST"])
    @rate_limit(max_requests=25, window_seconds=60, key_prefix="uni_portal")
    def university_welcome(slug):
        return _handle_university_portal(app, slug)

    @app.route("/u/<slug>/login", methods=["GET", "POST"])
    @rate_limit(max_requests=25, window_seconds=60, key_prefix="uni_login")
    def university_login(slug):
        return _handle_university_portal(app, slug)

    @app.route("/system/branding", methods=["GET", "POST"])
    @roles_required("system")
    def system_branding():
        db = get_db()
        cursor = db.cursor()
        try:
            tenant_scope = session.get("tenant_scope")
            if tenant_scope:
                target_uni_id = int(tenant_scope)
            else:
                req_uid = request.values.get("university_id") or session.get("university_id") or 3
                target_uni_id = int(req_uid)

            if request.method == "POST":
                primary_color = (request.form.get("primary_color") or "#003865").strip()
                secondary_color = (request.form.get("secondary_color") or "#007681").strip()
                welcome_tagline = (request.form.get("welcome_tagline") or "").strip()

                uni_dir = os.path.join(app.static_folder, "images", "universities")
                os.makedirs(uni_dir, exist_ok=True)

                cursor.execute("SELECT slug, logo, background_image FROM universities WHERE id=%s", (target_uni_id,))
                cur_u = cursor.fetchone() or {}
                logo_file = cur_u.get("logo")
                bg_file = cur_u.get("background_image")

                up_logo = request.files.get("logo_file")
                if up_logo and up_logo.filename:
                    ext = os.path.splitext(up_logo.filename)[1].lower()
                    if ext in ALLOWED_IMG_EXTS:
                        logo_file = f"{cur_u.get('slug', 'uni')}_logo_{int(time.time())}{ext}"
                        up_logo.save(os.path.join(uni_dir, secure_filename(logo_file)))

                up_bg = request.files.get("bg_file")
                if up_bg and up_bg.filename:
                    ext = os.path.splitext(up_bg.filename)[1].lower()
                    if ext in ALLOWED_IMG_EXTS:
                        bg_file = f"{cur_u.get('slug', 'uni')}_bg_{int(time.time())}{ext}"
                        up_bg.save(os.path.join(uni_dir, secure_filename(bg_file)))

                cursor.execute(
                    """
                    UPDATE universities
                    SET primary_color=%s, secondary_color=%s, accent_color=%s, welcome_tagline=%s, logo=%s, background_image=%s
                    WHERE id=%s
                    """,
                    (primary_color, secondary_color, primary_color, welcome_tagline, logo_file, bg_file, target_uni_id),
                )
                db.commit()
                log_audit("UNI_BRANDING_UPDATED", "university", target_uni_id, f"Updated theme colors/assets for university #{target_uni_id}")
                flash("University Welcome Page branding updated live!", "success")
                return redirect(url_for("system_branding", university_id=target_uni_id))

            cursor.execute("SELECT * FROM universities WHERE id=%s", (target_uni_id,))
            uni = _enrich_university(app, cursor.fetchone())

            universities = []
            if not tenant_scope:
                cursor.execute("SELECT id, name, slug FROM universities ORDER BY id ASC")
                universities = cursor.fetchall()
        finally:
            db.close()

        return render_template("system_branding.html", uni=uni, universities=universities, is_tenant_locked=bool(tenant_scope))
