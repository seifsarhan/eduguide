"""
Idempotent Student Portal V2 Upgrade Migration:
1. Adds university_id to courses, instructors, assignments, quizzes, marketplace_products (tenant isolation).
2. Adds phone & address to users (and backfills from students + defaults for newer universities).
3. Adds max_attempts to quizzes (default 1).
4. Enriches career_jobs & creates internship_applications table.
5. Seeds university-scoped instructors, courses, assignments, quizzes (+questions), and library books
   for AUC (2), GUC (4), ASU (5), Alexandria (6), AASTMT (7), and EELU (8).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.db import get_db


def _ensure_column(cursor, table, column, col_def):
    try:
        cursor.execute(f"SHOW COLUMNS FROM {table} LIKE %s", (column,))
        if not cursor.fetchone():
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
            print(f"  + Added {table}.{column}")
    except Exception:
        # SQLite fallback
        try:
            cursor.execute(f"PRAGMA table_info({table})")
            cols = [r["name"] for r in cursor.fetchall()]
            if column not in cols:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
                print(f"  + Added (SQLite) {table}.{column}")
        except Exception as e:
            print(f"  ! Could not add {table}.{column}: {e}")


UNI_PACKS = {
    2: {  # AUC
        "slug": "auc",
        "phone": "+20 100 220 4410",
        "address": "New Cairo Campus, 5th Settlement, Cairo",
        "instructors": [
            ("Dr.", "Nadia", "Mansour", "Corporate Finance", "n.mansour@auc.edu.eg", "Waleed C210"),
            ("Prof.", "Sherif", "Kamel", "Global Strategy", "s.kamel@auc.edu.eg", "ALJ 304"),
        ],
        "courses": [
            ("AUC-FIN310", "Corporate Finance & Valuation", 3, 3, "Fall", 60),
            ("AUC-MGT320", "Global Corporate Strategy", 3, 3, "Fall", 60),
            ("AUC-BUS340", "FinTech & Digital Banking", 3, 3, "Fall", 50),
        ],
        "assignment": ("AUC-FIN310", "DCF Valuation Case Study — Orascom Group", "Prepare a 5-year discounted cash flow valuation model in Excel/PDF with WACC sensitivity table.", "2026-12-28", 100),
        "quiz": ("AUC-BUS340", "FinTech & Digital Payments Assessment", 15, [
            ("Which mechanism underpins real-time interbank instant transfers in Egypt?", "InstaPay / IPN", "Paper Clearing House", "Manual Wire Slip", "Postal Order", "A"),
            ("What does WACC stand for in corporate valuation?", "Weighted Average Cost of Capital", "Weekly Asset Cash Cycle", "Working Account Credit Cap", "Weighted Accounting Cost Curve", "A"),
            ("Which metric best measures operating cash generation before financing?", "Free Cash Flow to Firm (FCFF)", "Par Value", "Book Equity Only", "Inventory Turnover", "A"),
        ]),
        "book": ("Corporate Finance & Egyptian Capital Markets", "Finance", 180, "Comprehensive AUC lecture handbook covering valuation, capital structure, and EGX case studies."),
    },
    4: {  # GUC
        "slug": "guc",
        "phone": "+20 101 440 8820",
        "address": "New Cairo, Main Entrance El-Tagamoa El-Khames",
        "instructors": [
            ("Prof.", "Tarek", "El-Shenawy", "Business Intelligence", "t.elshenawy@guc.edu.eg", "C3.112"),
            ("Dr.", "Yasmine", "Farouk", "Software Systems", "y.farouk@guc.edu.eg", "C7.205"),
        ],
        "courses": [
            ("GUC-BI201", "Business Intelligence & Analytics", 4, 2, "Fall", 70),
            ("GUC-CSEN401", "Software Engineering & Design Patterns", 4, 2, "Fall", 70),
            ("GUC-DMET302", "Human-Computer Interaction", 3, 2, "Fall", 60),
        ],
        "assignment": ("GUC-BI201", "Power BI Executive Sales Dashboard", "Build an interactive KPI dashboard with DAX measures and submit a PDF executive summary.", "2026-12-27", 100),
        "quiz": ("GUC-CSEN401", "Design Patterns & Clean Architecture Quiz", 15, [
            ("Which pattern ensures a class has only one global instance?", "Singleton", "Observer", "Decorator", "Adapter", "A"),
            ("In BI star schemas, quantitative metrics are stored in which table?", "Fact Table", "Lookup Index", "Audit Log", "Staging Cache", "A"),
            ("What does the 'S' in SOLID design principles represent?", "Single Responsibility Principle", "Static Routing Protocol", "Sequential Runtime Pattern", "Structured Relational Schema", "A"),
        ]),
        "book": ("Applied Business Intelligence & Software Engineering", "Engineering & BI", 160, "GUC applied manual covering ETL pipelines, star schemas, and enterprise software patterns."),
    },
    5: {  # ASU
        "slug": "asu",
        "phone": "+20 102 550 9930",
        "address": "Abbassia Square, El-Khalifa El-Maamoun St, Cairo",
        "instructors": [
            ("Prof.", "Mahmoud", "Hegazy", "Financial Accounting", "m.hegazy@asu.edu.eg", "Commerce B12"),
            ("Dr.", "Dalia", "Samir", "Management Information Systems", "d.samir@asu.edu.eg", "MIS Lab 4"),
        ],
        "courses": [
            ("ASU-ACC204", "Intermediate Financial Accounting", 3, 2, "Fall", 120),
            ("ASU-MIS210", "Enterprise Resource Planning (ERP)", 3, 2, "Fall", 100),
            ("ASU-ECO202", "Macroeconomic Policy & Analysis", 3, 2, "Fall", 120),
        ],
        "assignment": ("ASU-ACC204", "IFRS Financial Statements Analysis Report", "Analyze balance sheet liquidity and profitability ratios for a listed Egyptian enterprise.", "2026-12-26", 100),
        "quiz": ("ASU-MIS210", "ERP & Enterprise Systems Midterm Quiz", 15, [
            ("What is the primary goal of an ERP system?", "Integrate core business processes on a unified database", "Replace all internet browsers", "Eliminate accounting audits", "Increase data silos", "A"),
            ("Current Ratio equals:", "Current Assets / Current Liabilities", "Net Income / Equity", "Total Debt / Sales", "Cash / Fixed Assets", "A"),
            ("Which IFRS statement summarizes cash inflows and outflows?", "Statement of Cash Flows", "Trial Balance", "Chart of Accounts", "General Journal", "A"),
        ]),
        "book": ("Egyptian Accounting Standards & ERP Systems", "Commerce", 120, "Ain Shams Faculty of Commerce reference covering IFRS reporting and enterprise ERP workflows."),
    },
    6: {  # ALEX
        "slug": "alex",
        "phone": "+20 122 660 7740",
        "address": "Shatby Campus, El-Guish Road, Alexandria",
        "instructors": [
            ("Prof.", "Khaled", "El-Banna", "Data Structures & Algorithms", "k.elbanna@alexu.edu.eg", "Shatby CS 201"),
            ("Dr.", "Rania", "Moustafa", "Operating Systems", "r.moustafa@alexu.edu.eg", "Shatby CS 108"),
        ],
        "courses": [
            ("ALX-CS211", "Data Structures & Algorithms", 3, 2, "Fall", 90),
            ("ALX-CS224", "Operating Systems & Concurrency", 3, 2, "Fall", 90),
            ("ALX-CS230", "Computer Networks Fundamentals", 3, 2, "Fall", 80),
        ],
        "assignment": ("ALX-CS211", "Graph Shortest-Path Implementation (Dijkstra & A*)", "Implement Dijkstra's algorithm on Alexandria road graph data and submit complexity benchmarks.", "2026-12-29", 100),
        "quiz": ("ALX-CS224", "Operating Systems & Processes Quiz", 15, [
            ("What is the average time complexity of Binary Search?", "O(log n)", "O(n^2)", "O(n!)", "O(n log n)", "A"),
            ("Which condition is NOT one of Coffman's four deadlock conditions?", "Preemption Allowed", "Mutual Exclusion", "Hold and Wait", "Circular Wait", "A"),
            ("Which OSI layer handles end-to-end reliable transport (TCP)?", "Transport Layer (Layer 4)", "Physical Layer", "Data Link Layer", "Session Layer", "A"),
        ]),
        "book": ("Algorithms & Operating Systems Lab Guide", "Computer Science", 140, "Alexandria University CS manual with C++/Python lab implementations and complexity sheets."),
    },
    7: {  # AAST
        "slug": "aast",
        "phone": "+20 111 770 6650",
        "address": "Abu Qir Campus, Alexandria / Smart Village, Giza",
        "instructors": [
            ("Prof.", "Amr", "El-Masry", "Maritime Logistics & Supply Chain", "a.elmasry@aast.edu", "CIT 402"),
            ("Dr.", "Heba", "Nasser", "AI & Robotics", "h.nasser@aast.edu", "Smart Village B2"),
        ],
        "courses": [
            ("AAST-CS315", "Artificial Intelligence & Autonomous Systems", 3, 3, "Fall", 75),
            ("AAST-LOG310", "Smart Port & Maritime Supply Chains", 3, 3, "Fall", 75),
            ("AAST-SE320", "Cloud-Native Microservices", 3, 3, "Fall", 65),
        ],
        "assignment": ("AAST-LOG310", "Suez Canal Smart Logistics Optimization Model", "Submit a container routing optimization report with KPI metrics and bottleneck analysis.", "2026-12-30", 100),
        "quiz": ("AAST-CS315", "AI & Autonomous Systems Checkpoint", 15, [
            ("Which search algorithm uses both path cost g(n) and heuristic h(n)?", "A* Search", "Depth-First Search", "Random Walk", "Linear Scan", "A"),
            ("What does JIT stand for in supply chain management?", "Just-In-Time", "Joint Internal Tariff", "Junction Index Tracking", "Job Integration Table", "A"),
            ("Which container orchestrator manages cloud-native microservices at scale?", "Kubernetes", "FTP Server", "Notepad", "BIOS", "A"),
        ]),
        "book": ("Smart Logistics & Applied AI Handbook", "Engineering & CIT", 170, "AASTMT multidisciplinary workbook on autonomous systems, cloud microservices, and maritime logistics."),
    },
    8: {  # EELU
        "slug": "eelu",
        "phone": "+20 109 880 5560",
        "address": "33 El-Messaha St, Dokki, Giza (EELU HQ)",
        "instructors": [
            ("Dr.", "Hossam", "El-Din", "Artificial Intelligence & ML", "h.eldin@eelu.edu.eg", "Dokki Smart Lab 3"),
            ("Dr.", "Reem", "Nabil", "Cloud Computing & Distributed Systems", "r.nabil@eelu.edu.eg", "E-Hall B"),
        ],
        "courses": [
            ("EELU-IT301", "Artificial Intelligence & Machine Learning", 3, 3, "Fall", 80),
            ("EELU-CS304", "Cloud Computing & Distributed Systems", 3, 3, "Fall", 80),
            ("EELU-SE302", "Modern Web Engineering", 3, 3, "Fall", 80),
            ("EELU-IS305", "Database Management Systems", 3, 3, "Fall", 80),
        ],
        "assignment": ("EELU-IT301", "Build & Evaluate a Supervised ML Classifier", "Train a Logistic Regression & Random Forest classifier in Python and submit accuracy/F1 evaluation report.", "2026-12-29", 100),
        "quiz": ("EELU-CS304", "Cloud Computing & AI Fundamentals Quiz", 15, [
            ("Which cloud service model provides managed runtime without server OS management?", "PaaS (Platform as a Service)", "On-Premise Bare Metal", "Dial-Up Networking", "Local BIOS", "A"),
            ("Which metric balances Precision and Recall in imbalanced classification?", "F1-Score", "Raw File Size", "CPU Clock Speed", "RAM Latency", "A"),
            ("What does ACID ensure in relational databases?", "Atomicity, Consistency, Isolation, Durability", "Automatic Cloud Image Delivery", "Async Cache Index Deletion", "Application Code ID", "A"),
        ]),
        "book": ("EELU Blended Learning Guide: AI, Cloud & Web Engineering", "Information Technology", 135, "Official EELU digital study guide with interactive labs, SQL schemas, and cloud architecture patterns."),
    },
}


def run():
    db = get_db()
    cursor = db.cursor()
    try:
        print("=== 1. Schema Upgrades ===")
        for tbl in ("courses", "instructors", "assignments", "quizzes", "marketplace_products"):
            _ensure_column(cursor, tbl, "university_id", "INT DEFAULT NULL")

        _ensure_column(cursor, "users", "phone", "VARCHAR(50) DEFAULT ''")
        _ensure_column(cursor, "users", "address", "VARCHAR(255) DEFAULT ''")
        _ensure_column(cursor, "quizzes", "max_attempts", "INT DEFAULT 1")

        # Enrich career_jobs
        _ensure_column(cursor, "career_jobs", "company_name", "VARCHAR(150) DEFAULT 'Leading Partner'")
        _ensure_column(cursor, "career_jobs", "work_mode", "VARCHAR(50) DEFAULT 'Hybrid'")
        _ensure_column(cursor, "career_jobs", "duration", "VARCHAR(50) DEFAULT '3 Months'")

        # Create internship_applications table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS internship_applications (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                job_id INT NOT NULL,
                job_title VARCHAR(200),
                company_name VARCHAR(150),
                status VARCHAR(50) DEFAULT 'Applied',
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        print("=== 2. Backfilling Existing CU Data & User Contact Info ===")
        # Default legacy rows (university_id IS NULL) to Cairo University (3)
        for tbl in ("courses", "instructors", "assignments", "quizzes", "marketplace_products"):
            cursor.execute(f"UPDATE {tbl} SET university_id = 3 WHERE university_id IS NULL")

        # Backfill users.phone and users.address from legacy students table
        try:
            cursor.execute(
                """
                UPDATE users u
                JOIN students s ON u.student_code = s.student_id
                SET u.phone = COALESCE(NULLIF(u.phone, ''), s.phone, ''),
                    u.address = COALESCE(NULLIF(u.address, ''), s.address, '')
                """
            )
        except Exception:
            pass

        # Backfill career_jobs metadata
        job_updates = [
            ("Software", "Microsoft Egypt", "Hybrid", "3 Months"),
            ("Data", "Amazon Web Services (MENA)", "Remote", "4 Months"),
            ("Finance", "Commercial International Bank (CIB)", "On-site", "3 Months"),
            ("Account", "Deloitte Egypt", "Hybrid", "3 Months"),
            ("Market", "Vodafone Intelligent Solutions (_VOIS)", "Hybrid", "6 Months"),
            ("Cyber", "Orange Cyberdefense Egypt", "On-site", "6 Months"),
        ]
        cursor.execute("SELECT id, job_title, field, company_name FROM career_jobs")
        for job in cursor.fetchall():
            if not job.get("company_name") or job.get("company_name") == "Leading Partner":
                jt = (job.get("job_title") or "") + " " + (job.get("field") or "")
                matched = ("EDUGUIDE Industry Partner", "Hybrid", "3 Months")
                for kw, comp, mode, dur in job_updates:
                    if kw.lower() in jt.lower():
                        matched = (comp, mode, dur)
                        break
                cursor.execute(
                    "UPDATE career_jobs SET company_name=%s, work_mode=%s, duration=%s WHERE id=%s",
                    (matched[0], matched[1], matched[2], job["id"]),
                )

        print("=== 3. Seeding Isolated Content for All 6 Newer Universities ===")
        for uni_id, pack in UNI_PACKS.items():
            # Ensure student in this uni has phone & address
            cursor.execute(
                "UPDATE users SET phone=%s, address=%s WHERE university_id=%s AND (phone IS NULL OR phone='')",
                (pack["phone"], pack["address"], uni_id),
            )

            # Seed instructors
            inst_ids = []
            for title, fn, ln, spec, email, office in pack["instructors"]:
                cursor.execute("SELECT instructor_id FROM instructors WHERE email=%s", (email,))
                row = cursor.fetchone()
                if row:
                    iid = row["instructor_id"]
                    cursor.execute("UPDATE instructors SET university_id=%s WHERE instructor_id=%s", (uni_id, iid))
                else:
                    cursor.execute(
                        """
                        INSERT INTO instructors (dept_id, first_name, last_name, title, specialisation, email, office_room, university_id)
                        VALUES (1, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (fn, ln, title, spec, email, office, uni_id),
                    )
                    iid = cursor.lastrowid
                inst_ids.append(iid)

            primary_inst = inst_ids[0]

            # Seed courses
            for idx, (cid, cname, ch, ylvl, sem, cap) in enumerate(pack["courses"]):
                cursor.execute("SELECT course_id FROM courses WHERE course_id=%s", (cid,))
                if not cursor.fetchone():
                    cursor.execute(
                        """
                        INSERT INTO courses (course_id, dept_id, year_level, instructor_id, name, credit_hours, semester, max_capacity, description, university_id)
                        VALUES (%s, 1, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (cid, ylvl, inst_ids[idx % len(inst_ids)], cname, ch, sem, cap, f"{cname} — Official curriculum course.", uni_id),
                    )
                else:
                    cursor.execute("UPDATE courses SET university_id=%s WHERE course_id=%s", (uni_id, cid))

            # Seed 1 active assignment
            a_cid, a_title, a_desc, a_due, a_max = pack["assignment"]
            cursor.execute("SELECT id FROM assignments WHERE title=%s AND university_id=%s", (a_title, uni_id))
            if not cursor.fetchone():
                cursor.execute(
                    """
                    INSERT INTO assignments (course_id, instructor_id, title, description, due_date, max_score, university_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (a_cid, primary_inst, a_title, a_desc, a_due, a_max, uni_id),
                )

            # Seed 1 timed quiz with 3 questions
            q_cid, q_title, q_mins, q_items = pack["quiz"]
            cursor.execute("SELECT id FROM quizzes WHERE title=%s AND university_id=%s", (q_title, uni_id))
            if not cursor.fetchone():
                cursor.execute(
                    """
                    INSERT INTO quizzes (course_id, instructor_id, title, time_limit_mins, is_published, max_attempts, university_id)
                    VALUES (%s, %s, %s, %s, 1, 1, %s)
                    """,
                    (q_cid, primary_inst, q_title, q_mins, uni_id),
                )
                qid = cursor.lastrowid
                for qtxt, oa, ob, oc, od, corr in q_items:
                    cursor.execute(
                        """
                        INSERT INTO quiz_questions (quiz_id, question_text, option_a, option_b, option_c, option_d, correct_option)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (qid, qtxt, oa, ob, oc, od, corr),
                    )

            # Seed 1 library handbook
            b_title, b_cat, b_price, b_sum = pack["book"]
            cursor.execute("SELECT id FROM marketplace_products WHERE title=%s AND university_id=%s", (b_title, uni_id))
            if not cursor.fetchone():
                cursor.execute(
                    """
                    INSERT INTO marketplace_products (title, category, price, summary, instructor_id, course_id, university_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (b_title, b_cat, b_price, b_sum, primary_inst, pack["courses"][0][0], uni_id),
                )

            print(f"  OK seeded uni_id={uni_id} ({pack['slug'].upper()})")

        db.commit()
        print("MIGRATION V2 COMPLETE!")
    finally:
        db.close()


if __name__ == "__main__":
    run()
