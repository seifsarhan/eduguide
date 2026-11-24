import sqlite3
import re
import os

def final_convert():
    sql_file = 'edyguide1 (5).sql'
    db_file = 'eduguide1.db'
    
    if os.path.exists(db_file):
        os.remove(db_file)
        
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    
    print(f"Reading {sql_file}...")
    try:
        with open(sql_file, 'r', encoding='utf-8') as f:
            content = f.read()
    except:
        with open(sql_file, 'r', encoding='latin-1') as f:
            content = f.read()

    print("Refining SQL for SQLite compatibility...")
    
    # 1. Clean Keys and Constraints
    lines = content.split('\n')
    cleaned_lines = []
    for line in lines:
        l = line.strip().upper()
        if l.startswith('KEY `') or l.startswith('PRIMARY KEY (`') or l.startswith('CONSTRAINT `') or l.startswith('UNIQUE KEY `'):
            continue
        cleaned_lines.append(line)
    
    content = '\n'.join(cleaned_lines)
    content = re.sub(r',\s*\)', '\n)', content)

    # 2. Fix Data Types
    content = re.sub(r'enum\(.*?\)', 'TEXT', content, flags=re.IGNORECASE)
    content = content.replace('current_timestamp()', 'CURRENT_TIMESTAMP')
    content = content.replace('`id` int(11) NOT NULL', '`id` INTEGER PRIMARY KEY AUTOINCREMENT')
    content = content.replace('`id` int(11)', '`id` INTEGER PRIMARY KEY AUTOINCREMENT')
    content = content.replace('`id` INTEGER NOT NULL', '`id` INTEGER PRIMARY KEY AUTOINCREMENT')
    
    content = content.replace('int(11)', 'INTEGER')
    content = content.replace('decimal(10,2)', 'REAL')
    content = content.replace('decimal(3,2)', 'REAL')
    content = content.replace('double', 'REAL')
    content = content.replace('datetime', 'TEXT')
    content = content.replace('timestamp', 'TIMESTAMP')
    content = content.replace('AUTO_INCREMENT', '')
    content = content.replace('ENGINE=InnoDB', '')
    content = content.replace('DEFAULT CHARSET=utf8mb4', '')
    content = content.replace('COLLATE=utf8mb4_general_ci', '')

    statements = content.split(';')
    for stmt in statements:
        s = stmt.strip()
        if not s or any(x in s.upper() for x in ['SET ', 'ALTER TABLE', 'START TRANSACTION', 'COMMIT', 'LOCK TABLES', 'UNLOCK TABLES']):
            continue
        try:
            cursor.execute(s)
        except:
            pass

    # 3. Create Correct Views (Fixed column names)
    print("Creating Essential Views with fixed column names...")
    try:
        # Use 'name' instead of 'dept_name' based on our check
        cursor.execute("CREATE VIEW IF NOT EXISTS vw_studentprofile AS SELECT s.*, d.name as department FROM students s LEFT JOIN departments d ON s.dept_id = d.dept_id")
        cursor.execute("CREATE VIEW IF NOT EXISTS vw_paymentsummary AS SELECT p.*, u.full_name as student_name FROM payments p JOIN users u ON p.user_id = u.id")
        cursor.execute("CREATE VIEW IF NOT EXISTS vw_gradereport AS SELECT sg.*, u.full_name FROM student_grades sg JOIN users u ON sg.user_id = u.id")
        cursor.execute("CREATE VIEW IF NOT EXISTS vw_deptstats AS SELECT dept_id, COUNT(*) as student_count FROM students GROUP BY dept_id")
        cursor.execute("CREATE VIEW IF NOT EXISTS vw_courseroster AS SELECT c.course_name, u.full_name as student_name FROM enrollments e JOIN courses c ON e.course_id = c.course_id JOIN users u ON e.student_id = u.id")
    except Exception as e:
        print(f"View creation error: {e}")

    conn.commit()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
    all_objects = [t[0] for t in cursor.fetchall() if t[0] != 'sqlite_sequence']
    print(f"\nTotal Objects: {len(all_objects)}")
    conn.close()

if __name__ == "__main__":
    final_convert()
