import sqlite3

con = sqlite3.connect("chaosflow.db")
tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
print("Tables:", tables)
for t in tables:
    count = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"\n--- {t} ({count} rows) ---")
    for row in con.execute(f"SELECT * FROM {t} LIMIT 5"):
        print(row)
