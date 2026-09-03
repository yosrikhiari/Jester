import csv, sqlite3, os

conn = sqlite3.connect('go/data/jester.db')
c = conn.cursor()

# Check listing table schema and data
c.execute('PRAGMA table_info(listing)')
print('Listing table columns:', [d[1] for d in c.fetchall()])

c.execute('SELECT * FROM listing LIMIT 3')
print('\nListing table sample:')
for row in c.fetchall():
    print(row)

# Check listing_observation table schema
c.execute('PRAGMA table_info(listing_observation)')
print('\nListing observation table columns:', [d[1] for d in c.fetchall()])

c.execute('SELECT * FROM listing_observation LIMIT 3')
print('\nListing observation sample:')
for row in c.fetchall():
    print(row)

# Check what fields are available from observations
c.execute('SELECT portal, listing_id, observed_at, content_hash, price, currency, status, payload FROM listing_observation LIMIT 3')
print('\nSelected obs fields:')
for row in c.fetchall():
    print(row)

conn.close()