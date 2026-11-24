import io
import os
import re
import json
import secrets
import PyPDF2
from flask import render_template, request, jsonify, session
from core import config
from core.db import get_db, resolve_student_numeric_id
from core.security import login_required, roles_required, rate_limit
from core.ai_helpers import ask_gemini, build_context


ALL_SKILLS = {
    "Python": ["python"],
    "Java": ["java"],
    "JavaScript": ["javascript", "js"],
    "C++": ["c++", "cpp"],
    "C#": ["c#", "csharp"],
    "PHP": ["php"],
    "Swift": ["swift"],
    "Kotlin": ["kotlin"],
    "TypeScript": ["typescript"],
    "Go": ["golang", " go "],
    "R": [" r programming", "r studio", "rstudio"],
    "MATLAB": ["matlab"],
    "SQL": ["sql", "mysql", "postgresql", "sqlite", "oracle sql"],
    "NoSQL": ["nosql", "mongodb", "cassandra", "dynamodb"],
    "HTML/CSS": ["html", "css"],
    "React": ["react", "reactjs"],
    "Angular": ["angular"],
    "Vue.js": ["vue", "vuejs"],
    "Node.js": ["node.js", "nodejs"],
    "Flask": ["flask"],
    "Django": ["django"],
    "Spring Boot": ["spring boot", "springboot"],
    "Bootstrap": ["bootstrap"],
    "Machine Learning": ["machine learning", "ml model", "scikit-learn", "sklearn"],
    "Deep Learning": ["deep learning", "neural network", "tensorflow", "keras", "pytorch"],
    "Data Analysis": ["data analysis", "data analytics", "pandas", "numpy"],
    "Data Visualization": ["data visualization", "matplotlib", "tableau", "power bi", "powerbi"],
    "NLP": ["nlp", "natural language processing"],
    "Computer Vision": ["computer vision", "opencv", "image recognition"],
    "Statistics": ["statistics", "statistical analysis", "spss"],
    "AWS": ["aws", "amazon web services", "ec2", "s3", "lambda"],
    "Azure": ["azure", "microsoft azure"],
    "GCP": ["gcp", "google cloud", "google cloud platform"],
    "Docker": ["docker", "dockerfile", "containerization"],
    "Kubernetes": ["kubernetes", "k8s"],
    "CI/CD": ["ci/cd", "jenkins", "github actions", "gitlab ci"],
    "Linux": ["linux", "ubuntu", "bash", "shell scripting"],
    "Git": ["git", "github", "gitlab", "version control"],
    "Accounting": ["accounting", "bookkeeping", "ledger", "محاسبة"],
    "Financial Analysis": ["financial analysis", "financial modeling", "تحليل مالي"],
    "Financial Auditing": ["auditing", "audit", "مراجعة"],
    "Microsoft Excel": ["excel", "microsoft excel", "spreadsheet", "pivot table"],
    "Microsoft Word": ["microsoft word", "word processing"],
    "Microsoft PowerPoint": ["powerpoint", "presentation"],
    "ERP Systems": ["erp", "sap", "oracle erp", "odoo"],
    "QuickBooks": ["quickbooks"],
    "Taxation": ["taxation", "tax", "ضرائب"],
    "Budgeting": ["budgeting", "budget", "ميزانية"],
    "Communication": ["communication", "interpersonal", "تواصل", "تواصل فعّال"],
    "Teamwork": ["teamwork", "team player", "collaboration", "عمل فريق"],
    "Leadership": ["leadership", "team lead", "leading", "قيادة"],
    "Problem Solving": ["problem solving", "problem-solving", "حل مشكلات"],
    "Time Management": ["time management", "إدارة وقت"],
    "Project Management": ["project management", "pmp", "agile", "scrum", "kanban"],
    "Critical Thinking": ["critical thinking", "analytical thinking"],
    "Research": ["research", "بحث علمي"],
    "Customer Service": ["customer service", "خدمة عملاء"],
    "Marketing": ["marketing", "digital marketing", "seo", "social media marketing", "تسويق"],
    "Sales": ["sales", "مبيعات", "business development"],
    "Negotiation": ["negotiation", "تفاوض"],
    "Arabic": ["arabic", "العربية"],
    "English": ["english", "الإنجليزية", "toefl", "ielts"],
    "French": ["french", "الفرنسية"],
    "Photoshop": ["photoshop", "adobe photoshop"],
    "UI/UX": ["ui/ux", "user interface", "ux design", "figma", "adobe xd"],
    "AutoCAD": ["autocad"],
    "Network": ["networking", "cisco", "ccna", "network administration"],
    "Cybersecurity": ["cybersecurity", "cyber security", "information security", "penetration testing"],
}


def register_ai_routes(app):
    @app.route("/chat")
    @login_required
    def chat_page():
        user_id = session["user_id"]
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("SELECT id, full_name, email FROM users WHERE student_code=%s", (user_id,))
            local_user = cursor.fetchone()
            cursor.execute("SELECT * FROM vw_studentprofile WHERE student_id=%s", (user_id,))
            user_profile = cursor.fetchone()
        finally:
            db.close()

        user_data = {}
        if local_user:
            user_data["id"] = local_user["id"]
            user_data["full_name"] = local_user["full_name"]
        elif user_profile:
            user_data["full_name"] = user_profile.get("full_name", "Student")
            user_data["id"] = 0
        else:
            user_data["full_name"] = session.get("name", "Student")
            user_data["id"] = 0

        return render_template("chat.html", user=user_data)

    @app.route("/chat/history", methods=["GET"])
    @login_required
    def get_chat():
        db = get_db()
        cursor = db.cursor()
        chat = []
        try:
            n_id = resolve_student_numeric_id(cursor, session["user_id"])
            if n_id:
                cursor.execute(
                    "SELECT sender, message, created_at FROM chatbot_messages WHERE user_id = %s ORDER BY created_at ASC, id ASC",
                    (n_id,),
                )
                chat = cursor.fetchall()
        except Exception:
            chat = []
        finally:
            db.close()
        return jsonify(chat)

    @app.route("/chat/clear", methods=["POST"])
    @login_required
    def clear_chat():
        db = get_db()
        cursor = db.cursor()
        try:
            n_id = resolve_student_numeric_id(cursor, session["user_id"])
            if n_id:
                cursor.execute("DELETE FROM chatbot_messages WHERE user_id=%s", (n_id,))
                db.commit()
        except Exception as e:
            print("Clear chat error:", e)
        finally:
            db.close()
        return jsonify({"status": "cleared"})

    @app.route("/chat/send", methods=["POST"])
    @login_required
    @rate_limit(max_requests=25, window_seconds=60, key_prefix="ai_chat")
    def send_message():
        payload = request.get_json(silent=True) or {}
        msg = (payload.get("message") or "").strip()
        if not msg:
            return jsonify({"error": "Empty message."}), 400
        if len(msg) > 2000:
            return jsonify({"error": "Message too long (max 2000 chars)."}), 400

        db = get_db()
        cursor = db.cursor()
        try:
            n_id = resolve_student_numeric_id(cursor, session["user_id"])
            context = ""
            try:
                context = build_context(cursor, n_id, student_code=session["user_id"])
            except Exception as e:
                print("Context Error:", e)

            history_rows = []
            if n_id:
                cursor.execute(
                    "SELECT sender, message FROM chatbot_messages WHERE user_id=%s ORDER BY created_at DESC LIMIT 12",
                    (n_id,),
                )
                history_rows = list(cursor.fetchall())
                history_rows.reverse()

            messages_for_groq = [{"role": "system", "content": context}]
            for h in history_rows:
                role = "user" if h["sender"] == "student" else "assistant"
                messages_for_groq.append({"role": role, "content": h["message"]})
            messages_for_groq.append({"role": "user", "content": msg})

            bot_reply = None
            if config.groq_client:
                try:
                    completion = config.groq_client.chat.completions.create(
                        messages=messages_for_groq,
                        model=config.GROQ_MODEL_NAME,
                        temperature=0.7,
                        max_tokens=450,
                    )
                    bot_reply = completion.choices[0].message.content
                except Exception as e:
                    print("Groq Error:", e)

            if not bot_reply and config.GEMINI_API_KEY:
                flat = context + "\n\nCONVERSATION:\n"
                for h in history_rows:
                    role_label = "Student" if h["sender"] == "student" else "Assistant"
                    flat += f"{role_label}: {h['message']}\n"
                flat += f"Student: {msg}\nAssistant:"
                gemini_reply = ask_gemini(flat)
                if gemini_reply == "__QUOTA__":
                    bot_reply = "الذكاء الاصطناعي مشغول حالياً، جرب تاني بعد شوية."
                elif gemini_reply == "__ERROR__":
                    bot_reply = "حدث خطأ ما."
                else:
                    bot_reply = gemini_reply

            if not bot_reply:
                bot_reply = "عذراً، مش قادر أجاوبك دلوقتي."

            if n_id:
                try:
                    cursor.execute("INSERT INTO chatbot_messages (user_id, sender, message) VALUES (%s, %s, %s)", (n_id, "student", msg))
                    cursor.execute("INSERT INTO chatbot_messages (user_id, sender, message) VALUES (%s, %s, %s)", (n_id, "bot", bot_reply))
                    db.commit()
                except Exception as e:
                    print("Error saving message:", e)
        finally:
            db.close()

        return jsonify({"bot_reply": bot_reply})

    @app.route("/chat/upload-pdf", methods=["POST"])
    @login_required
    @rate_limit(max_requests=8, window_seconds=60, key_prefix="ai_pdf")
    def chat_upload_pdf():
        f = request.files.get("pdf_file")
        if not f or not f.filename:
            return jsonify({"error": "No PDF uploaded."}), 400
        if not f.filename.lower().endswith(".pdf"):
            return jsonify({"error": "Only PDF lecture files are supported."}), 400
        pdf_bytes = f.read()
        if len(pdf_bytes) > 8 * 1024 * 1024:
            return jsonify({"error": "PDF too large (max 8 MB)."}), 400
        try:
            reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
            text = ""
            for page in reader.pages[:15]:
                text += (page.extract_text() or "") + "\n"
        except Exception as e:
            return jsonify({"error": f"Could not read PDF: {e}"}), 400

        if not text.strip():
            return jsonify({"error": "No readable text found in PDF (scanned image?)."}), 400

        lang = session.get("lang", "en")
        lang_hint = "Write the summary in clear Egyptian/Modern Arabic with key English technical terms." if lang == "ar" else "Write the summary clearly in English."
        prompt = (
            f"You are EDUGUIDE AI Study Tutor. A student uploaded lecture file '{f.filename}'.\n"
            f"{lang_hint}\n"
            "Provide:\n1) 5 concise key takeaways (bullets)\n2) 3 likely exam practice questions.\n\n"
            f"LECTURE EXCERPT:\n{text[:5500]}"
        )
        summary = None
        if config.groq_client:
            try:
                comp = config.groq_client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model=config.GROQ_MODEL_NAME,
                    temperature=0.4,
                    max_tokens=650,
                )
                summary = comp.choices[0].message.content
            except Exception as e:
                print("Groq PDF summary error:", e)
        if not summary and config.GEMINI_API_KEY:
            g = ask_gemini(prompt)
            if g not in ("__QUOTA__", "__ERROR__"):
                summary = g
        if not summary:
            summary = f"Extracted {len(text.split())} words from **{f.filename}**. Ask me any question about it!"

        db = get_db()
        cursor = db.cursor()
        try:
            n_id = resolve_student_numeric_id(cursor, session["user_id"])
            if n_id:
                cursor.execute("INSERT INTO chatbot_messages (user_id, sender, message) VALUES (%s, %s, %s)", (n_id, "student", f"📄 Uploaded lecture PDF: {f.filename}"))
                cursor.execute("INSERT INTO chatbot_messages (user_id, sender, message) VALUES (%s, %s, %s)", (n_id, "bot", summary))
                db.commit()
        finally:
            db.close()

        return jsonify({"bot_reply": summary, "file_name": f.filename})

    @app.route("/cv/analyze-pdf", methods=["POST"])
    @login_required
    @rate_limit(max_requests=10, window_seconds=60, key_prefix="ai_cv")
    def cv_analyze():
        f = request.files.get("cv_file")
        target_field = (request.form.get("target_field") or "Auto").strip()
        if not f or not f.filename:
            return jsonify({"success": False, "error": "No file uploaded"}), 400
        if not f.filename.lower().endswith(".pdf"):
            return jsonify({"success": False, "error": "Only PDF files are supported."}), 400

        try:
            pdf_bytes = f.read()
            if len(pdf_bytes) > 8 * 1024 * 1024:
                return jsonify({"success": False, "error": "PDF file too large (max 8 MB)."}), 400
            reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
            full_text = ""
            for page in reader.pages:
                try:
                    full_text += page.extract_text() or ""
                except Exception:
                    pass
            full_text_lower = full_text.lower()
        except Exception as e:
            return jsonify({"success": False, "error": f"Could not read PDF: {str(e)}"}), 400

        if not full_text.strip():
            return jsonify({"success": False, "error": "Could not extract text from this PDF. Make sure it is not a scanned image."}), 400

        # Track-aware baseline skill extraction
        found_skills = [name for name, kws in ALL_SKILLS.items() if any(kw in full_text_lower for kw in kws)]
        tech_set = {"Python", "Java", "JavaScript", "SQL", "HTML/CSS", "React", "Flask", "Machine Learning", "Data Analysis", "Git", "Docker", "AWS", "Linux", "Cybersecurity"}
        biz_set = {"Accounting", "Financial Analysis", "Financial Auditing", "Microsoft Excel", "ERP Systems", "Budgeting", "Marketing", "Project Management", "Communication", "Leadership", "Problem Solving", "English"}

        # Detect major if Auto
        student_faculty = ""
        try:
            db_f = get_db()
            c_f = db_f.cursor()
            nid_f = resolve_student_numeric_id(c_f, session["user_id"])
            if nid_f:
                c_f.execute("SELECT faculty FROM users WHERE id=%s", (nid_f,))
                u_f = c_f.fetchone()
                student_faculty = (u_f or {}).get("faculty") or ""
            db_f.close()
        except Exception:
            pass

        inferred_track = target_field if target_field != "Auto" else (student_faculty or "General")
        ai_result = None

        # Primary: Real AI analysis via Groq Llama 3.3 70B
        if config.groq_client:
            try:
                cv_prompt = (
                    f"You are an expert ATS recruiter and career coach evaluating a university student's CV.\n"
                    f"Target Track / Major: {inferred_track}\n"
                    f"CV TEXT:\n{full_text[:5500]}\n\n"
                    "Return ONLY a valid JSON object (no markdown fences) with these exact keys:\n"
                    '{"score": <int 30-98>, "detected_field": "<short track name>", '
                    '"found_skills": [<up to 10 concrete skills found in CV>], '
                    '"missing_skills": [<6 high-impact skills missing for this specific track only>], '
                    '"strengths": [<3 short bullet strings>], '
                    '"improvements": [<3 concrete actionable rewrite suggestions>], '
                    '"summary": "<2-sentence encouraging ATS verdict>"}'
                )
                comp = config.groq_client.chat.completions.create(
                    messages=[{"role": "user", "content": cv_prompt}],
                    model=config.GROQ_MODEL_NAME,
                    temperature=0.3,
                    max_tokens=700,
                )
                raw = comp.choices[0].message.content or ""
                m = re.search(r"\{.*\}", raw, flags=re.S)
                if m:
                    parsed = json.loads(m.group(0))
                    if isinstance(parsed, dict) and "score" in parsed:
                        ai_result = parsed
            except Exception as e:
                print("AI CV Analysis fallback:", e)

        if ai_result:
            score = max(20, min(98, int(ai_result.get("score") or 70)))
            detected_field = ai_result.get("detected_field") or inferred_track
            out_found = ai_result.get("found_skills") or found_skills[:10]
            out_missing = ai_result.get("missing_skills") or []
            strengths = ai_result.get("strengths") or []
            improvements = ai_result.get("improvements") or []
            summary = ai_result.get("summary") or ""
            ai_powered = True
        else:
            # Smart track-scoped fallback (never penalize Finance students for missing Docker!)
            is_biz = any(k in inferred_track.lower() for k in ("commerce", "business", "finance", "account", "econ"))
            relevant_pool = biz_set if is_biz else tech_set
            matched_rel = [s for s in found_skills if s in relevant_pool]
            missing_rel = [s for s in relevant_pool if s not in found_skills]
            ratio = len(matched_rel) / max(1, min(8, len(relevant_pool)))
            word_bonus = 12 if len(full_text.split()) >= 150 else 5
            score = max(35, min(94, int(45 + ratio * 42 + word_bonus)))
            detected_field = inferred_track
            out_found = found_skills[:12]
            out_missing = missing_rel[:8]
            strengths = [
                f"Detected {len(found_skills)} transferable & domain skills relevant to {detected_field}.",
                f"Clean ATS-readable PDF format ({len(full_text.split())} words extracted).",
            ]
            improvements = [
                "Quantify project outcomes with measurable numbers (e.g., improved speed by 30%, analyzed 10k rows).",
                "Add a crisp 2-line Professional Summary tailored to your target internship role.",
                "Highlight top coursework projects or GitHub/Portfolio links near the top of page 1.",
            ]
            summary = f"Your CV aligns well with {detected_field}. Adding the recommended skills and measurable results will raise your ATS pass rate."
            ai_powered = False

        # Persist review history
        try:
            db = get_db()
            cursor = db.cursor()
            n_id = resolve_student_numeric_id(cursor, session["user_id"])
            if n_id:
                cursor.execute("INSERT INTO cv_reviews (user_id, score) VALUES (%s, %s)", (n_id, score))
                db.commit()
            db.close()
        except Exception:
            pass

        return jsonify({
            "success": True,
            "ai_powered": ai_powered,
            "score": score,
            "detected_field": detected_field,
            "file_name": f.filename,
            "found_skills": out_found,
            "missing_skills": out_missing[:10],
            "strengths": strengths,
            "improvements": improvements,
            "summary": summary,
            "total_words": len(full_text.split()),
        })

    @app.route("/interview/start")
    @login_required
    @rate_limit(max_requests=10, window_seconds=60, key_prefix="ai_interview")
    def interview_start():
        field = request.args.get("field", "General")
        if config.groq_client:
            try:
                system_prompt = (
                    f"You are a professional hiring manager conducting a job interview for a {field} position at a top company.\n"
                    "Start with a warm, professional greeting and then ask your FIRST interview question.\n"
                    "Keep it concise (2-3 sentences max). Be friendly but professional.\n"
                    "Do NOT say you are an AI. Act as a real human interviewer named 'Alex'."
                )
                completion = config.groq_client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": "Start the interview."},
                    ],
                    model=config.GROQ_MODEL_NAME,
                    max_tokens=200,
                )
                return jsonify({"success": True, "opening": completion.choices[0].message.content})
            except Exception as e:
                print("Groq Interview Start Error:", e)

        return jsonify({
            "success": True,
            "opening": "Hello! I'm Alex, your interviewer today. Let's start — could you please tell me a bit about yourself and your background?",
        })

    def _evaluate_interview_scorecard(field: str, history: list) -> dict:
        user_msgs = [m.get("content", "") for m in history if isinstance(m, dict) and m.get("role") == "user"]
        total_chars = sum(len(t) for t in user_msgs)
        if not user_msgs or total_chars < 40:
            return {
                "score": 40,
                "communication": 45,
                "technical": 35,
                "confidence": 40,
                "strengths": ["Attempted the mock interview session"],
                "improvements": ["Provide fuller answers using the STAR method (Situation, Task, Action, Result)"],
                "feedback": "Answers were very brief. Try expanding on concrete examples and technical details in your next practice session.",
            }

        eval_prompt = (
            f"You are a senior hiring manager evaluating a candidate's mock interview for a {field} role.\n"
            f"TRANSCRIPT: {json.dumps(history[-16:], ensure_ascii=False)}\n\n"
            "Return ONLY valid JSON (no markdown) with keys:\n"
            '{"score": <int 35-98>, "communication": <int 35-98>, "technical": <int 35-98>, "confidence": <int 35-98>, '
            '"strengths": [<2-3 short strings>], "improvements": [<2-3 short actionable strings>], '
            '"feedback": "<2-3 sentence constructive summary>"}'
        )
        raw = None
        if config.groq_client:
            try:
                comp = config.groq_client.chat.completions.create(
                    messages=[{"role": "user", "content": eval_prompt}],
                    model=config.GROQ_MODEL_NAME,
                    temperature=0.3,
                    max_tokens=450,
                )
                raw = comp.choices[0].message.content
            except Exception as e:
                print("Groq scorecard fallback:", e)
        if not raw and config.GEMINI_API_KEY:
            g = ask_gemini(eval_prompt)
            if g not in ("__QUOTA__", "__ERROR__"):
                raw = g

        if raw:
            m = re.search(r"\{.*\}", raw, flags=re.S)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                    if isinstance(parsed, dict) and "score" in parsed:
                        return {
                            "score": max(20, min(98, int(parsed.get("score") or 72))),
                            "communication": max(20, min(98, int(parsed.get("communication") or 75))),
                            "technical": max(20, min(98, int(parsed.get("technical") or 70))),
                            "confidence": max(20, min(98, int(parsed.get("confidence") or 74))),
                            "strengths": parsed.get("strengths") or ["Clear engagement throughout the interview"],
                            "improvements": parsed.get("improvements") or ["Include quantified achievements in examples"],
                            "feedback": parsed.get("feedback") or "Solid performance overall!",
                        }
                except Exception:
                    pass

        base = min(88, 60 + len(user_msgs) * 4)
        return {
            "score": base,
            "communication": base + 2,
            "technical": base - 2,
            "confidence": base,
            "strengths": ["Responded consistently to interview questions", f"Demonstrated interest in {field}"],
            "improvements": ["Use concrete metrics and STAR examples to strengthen technical depth"],
            "feedback": f"Good {field} practice session! Keep refining concise, example-driven answers.",
        }

    def _save_interview_session(field: str, scorecard: dict, history: list):
        try:
            db = get_db()
            cursor = db.cursor()
            n_id = resolve_student_numeric_id(cursor, session["user_id"])
            if n_id:
                cursor.execute(
                    """
                    INSERT INTO mock_interview_sessions (user_id, field, score, feedback, chat_history)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (n_id, field, scorecard["score"], scorecard["feedback"], json.dumps(history, ensure_ascii=False)),
                )
                db.commit()
            db.close()
        except Exception as e:
            print(f"CRITICAL DB SAVE ERROR: {e}")

    @app.route("/interview/finish", methods=["POST"])
    @login_required
    @rate_limit(max_requests=10, window_seconds=60, key_prefix="ai_interview")
    def interview_finish():
        data = request.get_json(silent=True) or {}
        field = data.get("field", "General")
        history = data.get("history", [])
        if not isinstance(history, list):
            history = []
        scorecard = _evaluate_interview_scorecard(field, history)
        _save_interview_session(field, scorecard, history)
        return jsonify({"success": True, **scorecard})

    @app.route("/interview/reply", methods=["POST"])
    @login_required
    @rate_limit(max_requests=25, window_seconds=60, key_prefix="ai_interview")
    def interview_reply():
        data = request.get_json(silent=True) or {}
        field = data.get("field", "General")
        history = data.get("history", [])
        user_answer = data.get("answer", "")
        question_num = int(data.get("question_num", 0))
        total_questions = int(data.get("total_questions", 10))

        system_prompt = (
            f"You are 'Alex', an expert hiring manager for {field} roles at EDUGUIDE.\n"
            "1. You are a REAL human interviewer - never reveal you are an AI.\n"
            "2. Evaluate the candidate's answer briefly (1 sentence).\n"
            "3. Ask your NEXT interview question naturally.\n"
            "4. Keep responses SHORT (3-4 sentences max).\n"
            "5. Ask progressively deeper technical questions."
        )

        messages = [{"role": "system", "content": system_prompt}]
        for h in history:
            if isinstance(h, dict) and "role" in h and "content" in h:
                messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": user_answer})

        is_final = question_num >= total_questions
        scorecard = {"score": 0, "feedback": "", "communication": 0, "technical": 0, "confidence": 0, "strengths": [], "improvements": []}

        if is_final:
            full_hist = (history if isinstance(history, list) else []) + [{"role": "user", "content": user_answer}]
            scorecard = _evaluate_interview_scorecard(field, full_hist)
            _save_interview_session(field, scorecard, full_hist)

        models_to_try = [
            {"provider": "groq", "model": config.GROQ_MODEL_NAME},
            {"provider": "groq", "model": config.GROQ_FALLBACK_MODEL},
            {"provider": "gemini", "model": config.GEMINI_MODEL_NAME},
        ]
        reply = None
        for attempt in models_to_try:
            try:
                if attempt["provider"] == "groq" and config.groq_client:
                    completion = config.groq_client.chat.completions.create(
                        messages=messages, model=attempt["model"], max_tokens=300
                    )
                    reply = completion.choices[0].message.content
                    if reply:
                        break
                elif attempt["provider"] == "gemini" and config.GEMINI_API_KEY:
                    history_text = "\n".join([f"{m['role']}: {m['content']}" for m in messages])
                    reply = ask_gemini(f"Respond as Alex for {field}. INTERVIEW HISTORY: {history_text}")
                    if reply and reply not in ("__QUOTA__", "__ERROR__"):
                        break
            except Exception:
                continue

        if reply:
            return jsonify({
                "success": True,
                "reply": reply,
                "is_final": is_final,
                **scorecard,
            })
        return jsonify({"success": False, "error": "AI busy."}), 503

    @app.route("/career/match")
    @login_required
    def career_match():
        db = get_db()
        cursor = db.cursor()
        try:
            n_id = resolve_student_numeric_id(cursor, session["user_id"])
            student_gpa = 3.0
            if n_id:
                cursor.execute("SELECT gpa FROM users WHERE id=%s", (n_id,))
                u = cursor.fetchone()
                if u and u.get("gpa"):
                    student_gpa = float(u["gpa"])

            cursor.execute("SELECT * FROM career_jobs ORDER BY min_gpa DESC LIMIT 6")
            jobs = cursor.fetchall()
            for j in jobs:
                min_g = float(j.get("min_gpa") or 2.0)
                diff = student_gpa - min_g
                j["score"] = max(60, min(98, int(85 + diff * 12)))
                cursor.execute(
                    """
                    SELECT cs.skill_name FROM job_required_skills jrs
                    JOIN career_skills cs ON jrs.skill_id = cs.id
                    WHERE jrs.job_id = %s
                    """,
                    (j["id"],),
                )
                skills = [r["skill_name"] for r in cursor.fetchall()]
                j["matched_skills"] = skills or ["Analytical Thinking", "Excel"]
        finally:
            db.close()
        return jsonify(jobs)

    @app.route("/speech-to-text", methods=["POST"])
    @login_required
    @rate_limit(max_requests=15, window_seconds=60, key_prefix="ai_stt")
    def transcribe_speech():
        if not config.groq_client:
            return jsonify({"error": "Groq client not configured"}), 500
        if "file" not in request.files:
            return jsonify({"error": "No audio file"}), 400

        audio_file = request.files["file"]
        temp_dir = os.path.join(config.BASE_DIR, "temp_uploads")
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, f"speech_{secrets.token_hex(8)}.webm")
        audio_file.save(temp_path)

        try:
            with open(temp_path, "rb") as f:
                transcription = config.groq_client.audio.transcriptions.create(
                    file=(os.path.basename(temp_path), f.read()),
                    model="whisper-large-v3",
                    response_format="json",
                    language="ar",
                    prompt="تحدث الطالب باللهجة المصرية العامية عن مواضيع جامعية مثل: جدول المحاضرات، الامتحانات، النتيجة، المعدل التراكمي (GPA)، المذاكرة، المواد الدراسية، السيرة الذاتية (CV)، والمشاريع.",
                    temperature=0.0,
                )
            return jsonify({"text": transcription.text})
        except Exception as e:
            print(f"Transcription Error: {e}")
            return jsonify({"error": "Transcription failed"}), 500
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    @app.route("/api/admin/interviews")
    @roles_required("system", "instructor")
    def admin_interviews():
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute(
                """
                SELECT s.id, u.full_name, s.field, s.score, s.feedback, s.chat_history, s.created_at
                FROM mock_interview_sessions s
                JOIN users u ON s.user_id = u.id
                ORDER BY s.created_at DESC LIMIT 50
                """
            )
            interviews = cursor.fetchall()
            for i in interviews:
                if i.get("created_at") and hasattr(i["created_at"], "strftime"):
                    i["date_str"] = i["created_at"].strftime("%Y-%m-%d %H:%M")
                else:
                    i["date_str"] = str(i.get("created_at") or "")
        finally:
            db.close()
        return jsonify(interviews)

    @app.route("/api/admin/smart-stats")
    @roles_required("system", "instructor")
    @rate_limit(max_requests=10, window_seconds=60, key_prefix="ai_stats")
    def admin_smart_stats():
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("SELECT COUNT(*) as count FROM users")
            u_count = cursor.fetchone()["count"]
            cursor.execute("SELECT COUNT(*) as count FROM chatbot_messages")
            chat_count = cursor.fetchone()["count"]
            cursor.execute("SELECT COUNT(*) as count FROM payments")
            pay_count = cursor.fetchone()["count"]
        finally:
            db.close()

        prompt = (
            f"As a platform analytics AI, provide a very short (2 lines) encouraging summary of these stats for the university admin: "
            f"Total Students: {u_count}, AI Chats: {chat_count}, Total Payments: {pay_count}. Use a professional, slightly Egyptian tone."
        )
        ai_summary = ask_gemini(prompt)
        if ai_summary in ("__QUOTA__", "__ERROR__"):
            ai_summary = f"المنصة نشطة حالياً بعدد {u_count} طالب و {chat_count} محادثة ذكاء اصطناعي و {pay_count} معاملة دفع."

        return jsonify({
            "summary": ai_summary,
            "users": u_count,
            "chats": chat_count,
            "payments": pay_count,
        })
