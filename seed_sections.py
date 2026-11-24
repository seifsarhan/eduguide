import pymysql, os
from dotenv import load_dotenv
load_dotenv('.env')

conn = pymysql.connect(host=os.getenv('DB_HOST','localhost'), user=os.getenv('DB_USER','root'), password=os.getenv('DB_PASS',''), database=os.getenv('DB_NAME','eduguide1'))
cursor = conn.cursor()

try:
    cursor.execute("ALTER TABLE schedules ADD COLUMN type ENUM('Lecture', 'Section') DEFAULT 'Lecture'")
except Exception as e:
    print('Column may already exist:', e)

cursor.execute("UPDATE schedules SET type='Lecture' WHERE type IS NULL")

cursor.execute("SELECT count(*) FROM schedules WHERE user_id=1 AND type='Section'")
if cursor.fetchone()[0] == 0:
    sections = [
        (1, 'Advanced Auditing', 'Monday', '14:00', '16:00', 'Lab 2', 'Eng. Hassan', 'Section'),
        (1, 'Business Ethics & Governance', 'Tuesday', '10:00', '12:00', 'Room 12', 'Eng. Sara', 'Section'),
        (1, 'Financial Markets', 'Wednesday', '12:00', '14:00', 'Lab 4', 'Eng. Ahmed', 'Section'),
        (1, 'International Marketing', 'Thursday', '10:00', '12:00', 'Room 5', 'Eng. Mona', 'Section')
    ]
    cursor.executemany("INSERT INTO schedules (user_id, subject_name, day_name, start_time, end_time, room, doctor_name, type) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", sections)
    print('Sections seeded for user 1')

conn.commit()
conn.close()
