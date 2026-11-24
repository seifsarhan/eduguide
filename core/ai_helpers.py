from datetime import datetime
from core import config

UNIVERSITY_INFO_CACHE = None

ARABIC_HINTS = [
    "ا", "ب", "ت", "ث", "ج", "ح", "خ", "د", "ذ", "ر", "ز", "س", "ش",
    "ص", "ض", "ط", "ظ", "ع", "غ", "ف", "ق", "ك", "ل", "م", "ن", "ه", "و", "ي", "ة",
]

STOP_WORDS = {
    "how", "are", "you", "the", "a", "an", "is", "it", "to", "in",
    "of", "and", "or", "for", "with", "on", "at", "by", "from",
}


def is_arabic_text(text: str) -> bool:
    return any(ch in text for ch in ARABIC_HINTS)


def ask_gemini(prompt: str) -> str:
    if not config.gemini_client:
        return "__ERROR__"
    tried = set()
    last_err = None
    for model_name in getattr(config, "GEMINI_FALLBACK_MODELS", [config.GEMINI_MODEL_NAME]):
        if not model_name or model_name in tried:
            continue
        tried.add(model_name)
        try:
            response = config.gemini_client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            if hasattr(response, "text") and response.text:
                return response.text.strip()
            return "No response from AI."
        except Exception as e:
            last_err = e
            error_text = str(e).lower()
            if "429" in error_text or "quota" in error_text or "limit" in error_text:
                return "__QUOTA__"
            if "404" in error_text or "not_found" in error_text or "no longer available" in error_text:
                continue
            break
    if last_err:
        print(f"Gemini API Error: {last_err}")
    return "__ERROR__"


def build_context(cursor, n_id, student_code: str = None) -> str:
    global UNIVERSITY_INFO_CACHE

    cursor.execute("SELECT * FROM users WHERE id=%s", (n_id,))
    user = cursor.fetchone() or {}
    s_code = student_code or user.get("student_code")

    cursor.execute("SELECT subject_name, day_name, start_time, end_time, room, doctor_name FROM schedules WHERE user_id=%s ORDER BY id ASC LIMIT 8", (n_id,))
    schedules = cursor.fetchall()

    cursor.execute("SELECT subject_name, exam_date, exam_time, location FROM exams WHERE user_id=%s ORDER BY id ASC LIMIT 8", (n_id,))
    exams = cursor.fetchall()

    # Fixed column names: payment_date & payment_status
    cursor.execute("SELECT amount, payment_type, payment_status, payment_date FROM payments WHERE user_id=%s ORDER BY id DESC LIMIT 3", (n_id,))
    payments = cursor.fetchall()

    # Fixed column names: courses.name, courses.credit_hours, enrollments.student_id = student_code
    courses = []
    if s_code:
        try:
            cursor.execute(
                """
                SELECT c.name AS course_name, c.course_id, c.credit_hours
                FROM enrollments e
                JOIN courses c ON e.course_id = c.course_id
                WHERE e.student_id = %s
                """,
                (s_code,),
            )
            courses = cursor.fetchall()
        except Exception:
            courses = []

    cursor.execute("SELECT title, message, status FROM student_requests WHERE user_id=%s ORDER BY id DESC LIMIT 3", (n_id,))
    reqs = cursor.fetchall()

    cursor.execute("SELECT title, content FROM student_notes WHERE user_id=%s ORDER BY id DESC LIMIT 5", (n_id,))
    notes = cursor.fetchall()

    if UNIVERSITY_INFO_CACHE is None:
        try:
            cursor.execute("SELECT category, answer FROM university_info")
            u_rows = cursor.fetchall()
            UNIVERSITY_INFO_CACHE = "\n".join([f"[{r['category']}]: {r['answer']}" for r in u_rows])
        except Exception:
            UNIVERSITY_INFO_CACHE = "No university info available."

    return f"""### IDENTITY & PERSONA
You are EDUGUIDE AI — the smart, friendly, and dedicated AI academic concierge for students at this university.
- Tone: Warm, supportive, and conversational (like a senior mentor).
- Language: Match the student's language (Egyptian Arabic, English, or a mix).
- Critical: Never say "I am a language model" or "I don't have feelings". Stay in character as EDUGUIDE AI at all times.

### YOUR CAPABILITIES
You have direct access to the student's academic record, schedule, exams, payments, and university info. Use this data to provide specific, personalized help.

STUDENT DATA (use ONLY when directly relevant):
- Name: {user.get('full_name', 'Student')}
- Code: {s_code or 'N/A'} | GPA: {user.get('gpa', 'N/A')} | Faculty: {user.get('faculty', 'N/A')} | Level: {user.get('level', 'N/A')}
- Enrolled Courses: {courses if courses else 'None'}
- Schedule: {schedules if schedules else 'None'}
- Exams: {exams if exams else 'None'}
- Payments: {payments if payments else 'None'}
- Recent Requests: {reqs if reqs else 'None'}
- Latest Notes: {notes if notes else 'None'}

UNIVERSITY INFO:
{UNIVERSITY_INFO_CACHE}

Date: {datetime.now().strftime('%A, %d %B %Y')}""".strip()


def get_university_answer(cursor, message: str):
    try:
        cursor.execute("SELECT category, keywords, answer FROM university_info")
        rows = cursor.fetchall()
        msg_words = set(message.lower().strip().split())
        best_answer = None
        best_score = 0
        for row in rows:
            keywords = set((row.get("keywords") or "").lower().split())
            matches = (msg_words & keywords) - STOP_WORDS
            score = len(matches)
            if score > best_score:
                best_score = score
                best_answer = row["answer"]
        if best_score >= 1:
            return best_answer
    except Exception:
        pass
    return None
