from ptd_data.db import get_conn

conn = get_conn(read_only=True)

res = conn.query("SELECT DISTINCT country_full, emoji FROM nationalities;")
for row in res.fetchall():
    print(row)