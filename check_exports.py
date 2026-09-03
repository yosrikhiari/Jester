import csv, sqlite3, os

# Check current DB state
conn = sqlite3.connect('go/data/jester.db')
c = conn.cursor()

# Count unique listings
c.execute('SELECT COUNT(DISTINCT listing_id) FROM listing_observation')
unique = c.fetchone()[0]
c.execute('SELECT COUNT(*) FROM listing_observation')
total = c.fetchone()[0]
print(f'DB: {total} total observations, {unique} unique listings')

# Check listings_export.csv
with open('exports/listings_export.csv', 'r', encoding='utf-8-sig') as f:
    reader = csv.reader(f)
    rows = list(reader)
    print(f'\nlistings_export.csv: {len(rows)} rows')
    if rows:
        print(f'Header: {rows[0]}')
        # Show first data row
        print(f'First data row: {rows[1] if len(rows) > 1 else "N/A"}')

# Check observations_export.csv
with open('exports/observations_export.csv', 'r', encoding='utf-8-sig') as f:
    reader = csv.reader(f)
    rows = list(reader)
    print(f'\nobservations_export.csv: {len(rows)} rows')
    if rows:
        print(f'Header: {rows[0]}')
        print(f'First data row: {rows[1] if len(rows) > 1 else "N/A"}')

conn.close()