import pymysql, os
from dotenv import load_dotenv

load_dotenv('.env')
conn = pymysql.connect(
    host=os.getenv('DB_HOST','localhost'), 
    user=os.getenv('DB_USER','root'), 
    password=os.getenv('DB_PASS',''), 
    database=os.getenv('DB_NAME','eduguide1'), 
    charset='utf8mb4'
)
cursor = conn.cursor()

glitched_chars = ['ÔÇô', 'â€“', 'ï¿½']

try:
    for g in glitched_chars:
        cursor.execute("UPDATE users SET level = REPLACE(level, %s, ' - ') WHERE level LIKE %s", (g, f"%{g}%"))
        cursor.execute("UPDATE Students SET year_level = REPLACE(year_level, %s, ' - ') WHERE year_level LIKE %s", (g, f"%{g}%"))
        cursor.execute("UPDATE AcademicYears SET label = REPLACE(label, %s, ' - ') WHERE label LIKE %s", (g, f"%{g}%"))
except Exception as e:
    print('Error:', e)

conn.commit()
print('Glitch replaced in DB')
conn.close()
