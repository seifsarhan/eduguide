import os
from flask import Flask, render_template, session, url_for
from flask_cors import CORS

from core import config
from core.db import get_db, resolve_student_numeric_id, ensure_core_tables
from core.security import verify_same_origin_or_csrf, apply_security_headers, get_csrf_token
from routes import register_all_routes


def create_app() -> Flask:
    flask_app = Flask(__name__)
    flask_app.secret_key = config.SECRET_KEY
    flask_app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,  # 16 MB upload cap
    )
    CORS(flask_app)

    ensure_core_tables()

    @flask_app.before_request
    def _security_guard():
        return verify_same_origin_or_csrf()

    @flask_app.after_request
    def _security_headers(response):
        return apply_security_headers(response)

    @flask_app.context_processor
    def inject_global_context():
        notifs = []
        active_university = None

        if "user_id" in session:
            db = get_db()
            cursor = db.cursor()
            try:
                if session.get("role") == "student":
                    nid = resolve_student_numeric_id(cursor, session["user_id"])
                    if nid:
                        cursor.execute(
                            "SELECT title, message, created_at FROM notifications WHERE user_id=%s ORDER BY created_at DESC LIMIT 5",
                            (nid,),
                        )
                        notifs = cursor.fetchall()

                uni_id = session.get("university_id")
                if uni_id:
                    cursor.execute(
                        "SELECT id, name, slug, logo, primary_color, secondary_color, accent_color FROM universities WHERE id=%s",
                        (uni_id,),
                    )
                    active_university = cursor.fetchone()
                    if active_university:
                        logo_name = active_university.get("logo") or ""
                        uni_logo_path = os.path.join(flask_app.static_folder, "images", "universities", logo_name)
                        active_university["logo_url"] = (
                            url_for("static", filename=f"images/universities/{logo_name}")
                            if logo_name and os.path.exists(uni_logo_path)
                            else None
                        )
            except Exception as e:
                print(f"Context processor warning: {e}")
            finally:
                db.close()

        return {
            "notifications": notifs,
            "csrf_token": get_csrf_token,
            "active_university": active_university,
        }

    @flask_app.errorhandler(403)
    def forbidden_error(e):
        return render_template("error.html", message="403 — Access Denied / Forbidden."), 403

    @flask_app.errorhandler(404)
    def page_not_found(e):
        return render_template("404.html"), 404

    @flask_app.errorhandler(500)
    def internal_server_error(e):
        return render_template("500.html"), 500

    register_all_routes(flask_app)
    return flask_app


app = create_app()

if __name__ == "__main__":
    print(f"DEBUG: GROQ configured={bool(config.GROQ_API_KEY)}, GEMINI configured={bool(config.GEMINI_API_KEY)}")
    app.run(host="0.0.0.0", port=config.PORT, debug=config.DEBUG_MODE)
