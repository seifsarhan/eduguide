import pymysql, os
from dotenv import load_dotenv
load_dotenv('.env')

conn = pymysql.connect(host=os.getenv('DB_HOST','localhost'), user=os.getenv('DB_USER','root'), password=os.getenv('DB_PASS',''), database=os.getenv('DB_NAME','eduguide1'))
cursor = conn.cursor(pymysql.cursors.DictCursor)
cursor.execute("SELECT level FROM users WHERE full_name LIKE %s", ('%Dina%',))
res = cursor.fetchone()
print(f"Level content: {res}")
if res:
    val = res['level']
    print(f"Type: {type(val)}")
    print(f"Value: {val}")
    # Print hex to see exact bytes
    try:
        print(f"Hex: {val.encode('utf-8').hex()}")
    except:
        pass
conn.close()
