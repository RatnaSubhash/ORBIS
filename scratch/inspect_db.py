import sqlite3
import json

conn = sqlite3.connect('orbis.db')
c = conn.cursor()
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print('Tables in orbis.db:', tables)

for t in ['workspace_events', 'workspace_sessions', 'restoration_plans', 'tasks']:
    if t in tables:
        count = c.execute(f'SELECT count(*) FROM {t}').fetchone()[0]
        print(f'\n--- {t} (count: {count}) ---')
        latest = c.execute(f'SELECT * FROM {t} ORDER BY rowid DESC LIMIT 5').fetchall()
        col_names = [d[0] for d in c.description]
        print('Columns:', col_names)
        for row in latest:
            row_dict = dict(zip(col_names, row))
            print('Row:', {k: (v[:60] if isinstance(v, str) and len(v) > 60 else v) for k, v in row_dict.items()})
