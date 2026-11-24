from flask import render_template, request, session, redirect, url_for, flash
from core.db import get_db
from core.security import verify_password, rate_limit, log_audit


def register_auth_routes(app):
    @app.route("/")
    def landing():
        return render_template("landing.html")

    @app.route("/login", methods=["GET", "POST"])
    @rate_limit(max_requests=15, window_seconds=60, key_prefix="login")
    def login():
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("SELECT id, name, slug, logo, primary_color, secondary_color, admin_email FROM universities ORDER BY id ASC")
            universities = cursor.fetchall()

            if request.method == "POST":
                mode = (request.form.get("mode") or "university").strip()
                identifier = (request.form.get("identifier") or request.form.get("email") or "").strip()
                password = (request.form.get("password") or "").strip()

                # ============================================================
                # MODE 1: UNIVERSITY GATEWAY (DEAN / INSTITUTIONAL EMAIL)
                # Opens that university's dedicated branded Welcome Page (/u/<slug>)
                # ============================================================
                if mode == "university":
                    cursor.execute(
                        "SELECT * FROM universities WHERE LOWER(admin_email)=%s OR LOWER(slug)=%s",
                        (identifier.lower(), identifier.lower()),
                    )
                    uni = cursor.fetchone()
                    if uni and verify_password(uni.get("admin_password", ""), password):
                        session["portal_university_id"] = uni["id"]
                        session["portal_university_slug"] = uni["slug"]
                        session["university_gateway_unlocked"] = uni["id"]
                        log_audit(
                            "UNIVERSITY_GATEWAY_OPENED",
                            "university",
                            uni["slug"],
                            f"Institutional gateway unlocked for {uni['name']}",
                            actor_override={"id": uni["admin_email"], "name": uni["name"], "role": "university"},
                        )
                        return redirect(url_for("university_welcome", slug=uni["slug"]))

                    log_audit(
                        "UNIVERSITY_GATEWAY_FAILED",
                        "university",
                        identifier,
                        "Invalid university institutional email or password",
                        actor_override={"id": identifier, "name": identifier, "role": "university"},
                    )
                    flash("Invalid university email or password. Use your institution's EDUGUIDE credentials.", "danger")
                    return render_template("login.html", universities=universities, active_mode="university")

                # ============================================================
                # MODE 2: PLATFORM SUPER ADMIN (EDUGUIDE OWNER)
                # ============================================================
                if mode == "superadmin":
                    cursor.execute(
                        "SELECT * FROM system_users WHERE email=%s OR username=%s",
                        (identifier, identifier),
                    )
                    sys_user = cursor.fetchone()
                    if sys_user and verify_password(sys_user["password"], password):
                        lang = session.get("lang", "en")
                        session.clear()
                        session["lang"] = lang
                        session["role"] = "system"
                        session["is_super_admin"] = True
                        session["tenant_scope"] = None
                        session["name"] = sys_user["full_name"]
                        session["user_id"] = f"sys_{sys_user['id']}"
                        log_audit("SUPERADMIN_LOGIN_SUCCESS", "system_user", sys_user["id"], f"Super Admin login: {identifier}")
                        return redirect(url_for("system_dashboard"))

                    log_audit(
                        "LOGIN_FAILED",
                        "system_user",
                        identifier,
                        "Invalid Super Admin credentials",
                        actor_override={"id": identifier, "name": identifier, "role": "system"},
                    )
                    flash("Invalid EDUGUIDE Super Admin credentials.", "danger")
                    return render_template("login.html", universities=universities, active_mode="superadmin")

            return render_template("login.html", universities=universities, active_mode=request.args.get("mode", "university"))
        finally:
            db.close()

    @app.route("/logout")
    def logout():
        if "user_id" in session:
            log_audit("LOGOUT", session.get("role", "user"), session.get("user_id", ""), "User logged out")
        lang = session.get("lang", "en")
        uni_slug = session.get("portal_university_slug")
        is_super = session.get("is_super_admin", False)
        session.clear()
        session["lang"] = lang
        if uni_slug and not is_super:
            return redirect(url_for("university_welcome", slug=uni_slug))
        return redirect(url_for("landing"))

    @app.route("/set_language/<lang>")
    def set_language(lang):
        if lang in ("en", "ar"):
            session["lang"] = lang
        next_url = request.args.get("next", "")
        if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
            next_url = url_for("landing")
        return redirect(next_url)

    @app.route("/about")
    def about():
        return render_template("about.html")

    @app.route("/contact")
    def contact():
        return render_template("contact.html")

    @app.route("/contact_send", methods=["POST"])
    @rate_limit(max_requests=5, window_seconds=60, key_prefix="contact")
    def contact_send():
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip()
        msg = (request.form.get("message") or "").strip()

        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS contact_messages (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    full_name VARCHAR(255),
                    email VARCHAR(255),
                    message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute(
                "INSERT INTO contact_messages (full_name, email, message) VALUES (%s, %s, %s)",
                (full_name, email, msg),
            )
            db.commit()
        finally:
            db.close()

        flash("Your message has been sent successfully!", "success")
        return redirect(url_for("contact"))
