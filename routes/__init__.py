from routes.auth import register_auth_routes
from routes.student import register_student_routes
from routes.instructor import register_instructor_routes
from routes.system_admin import register_system_routes
from routes.ai_services import register_ai_routes
from routes.payments import register_payment_routes
from routes.academic_suite import register_academic_suite_routes
from routes.enterprise_suite import register_enterprise_suite_routes
from routes.university_portal import register_university_portal_routes


def register_all_routes(app):
    register_auth_routes(app)
    register_student_routes(app)
    register_instructor_routes(app)
    register_system_routes(app)
    register_ai_routes(app)
    register_payment_routes(app)
    register_academic_suite_routes(app)
    register_enterprise_suite_routes(app)
    register_university_portal_routes(app)
