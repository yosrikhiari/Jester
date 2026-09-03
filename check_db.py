import sqlite3
conn = sqlite3.connect('go/data/jester.db')
c = conn.cursor()
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = c.fetchall()
print("Tables:", tables)
for t in tables:
    tname = t[0]
    c.execute(f"SELECT COUNT(*) FROM {tname}")
    cnt = c.fetchone()[0]
    print(f"  {tname}: {cnt} rows")
conn.close()