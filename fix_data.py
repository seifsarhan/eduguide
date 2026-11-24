import os
import pymysql
from dotenv import load_dotenv

# Load database credentials
load_dotenv(override=True)

def get_db():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASS", ""),
        database=os.getenv("DB_NAME", "edyguide1"),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True
    )

def fix_schedules():
    try:
        db = get_db()
        cursor = db.cursor()
        
        print("DEBUG: Fetching all students from schedules...")
        
        # 1. Find all subjects that only have 'lecture' for any user
        cursor.execute("""
            SELECT DISTINCT user_id, subject_name, doctor_name, room 
            FROM schedules 
            WHERE type = 'lecture'
        """)
        lectures = cursor.fetchall()
        
        added_count = 0
        for lec in lectures:
            # Check if this user already has a section for this subject
            cursor.execute("""
                SELECT id FROM schedules 
                WHERE user_id = %s AND subject_name = %s AND type = 'section'
            """, (lec['user_id'], lec['subject_name']))
            
            if not cursor.fetchone():
                # Add a default section
                cursor.execute("""
                    INSERT INTO schedules (user_id, subject_name, day_name, start_time, end_time, room, doctor_name, type)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    lec['user_id'], 
                    lec['subject_name'], 
                    'Tuesday', # Default day
                    '10:00:00', 
                    '12:00:00', 
                    'Lab 101', 
                    'Assistant ' + lec['doctor_name'], 
                    'section'
                ))
                added_count += 1
        
        db.close()
        print(f"SUCCESS: Added {added_count} missing sections across all students.")
        return True
    except Exception as e:
        print(f"ERROR: {e}")
        return False

if __name__ == "__main__":
    fix_schedules()
