import csv, sqlite3, json

conn = sqlite3.connect('go/data/jester.db')
c = conn.cursor()

# Export observations_export.csv from listing_observation table
c.execute('SELECT portal, listing_id, observed_at, content_hash, price, currency, status, payload FROM listing_observation')
obs_rows = c.fetchall()

with open('exports/observations_export.csv', 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow(['portal', 'listing_id', 'observed_at', 'title', 'price', 'currency', 'property_type', 'city', 'governorate', 'status', 'content_hash', 'gallery_hash', 'run_id'])
    for row in obs_rows:
        portal, listing_id, observed_at, content_hash, price, currency, status, payload = row
        # Parse payload JSON to extract fields
        try:
            p = json.loads(payload) if payload else {}
            title = p.get('title', '')
            prop_type = p.get('property_type', '')
            city = p.get('city', '')
            governorate = p.get('governorate', '')
        except:
            title = ''
            prop_type = ''
            city = ''
            governorate = ''
        writer.writerow([portal, listing_id, observed_at, title, price, currency, prop_type, city, governorate, status, content_hash, '', ''])

print(f'Wrote observations_export.csv with {len(obs_rows)} rows')

# Export listings_export.csv - pull from listing_observation deduplicated by listing_id
c.execute('SELECT DISTINCT portal, listing_id FROM listing_observation ORDER BY portal, listing_id')
listing_rows = c.fetchall()

with open('exports/listings_export.csv', 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow(['portal', 'listing_id', 'url', 'title', 'description', 'price', 'currency', 'property_type', 'city', 'governorate', 'seller', 'seller_type', 'status', 'published_at', 'first_seen_at', 'last_seen_at'])
    for row in listing_rows:
        portal, listing_id = row
        # Get latest observation for this listing to extract url and title
        c.execute('SELECT portal, listing_id, observed_at, content_hash, price, currency, status, payload FROM listing_observation WHERE portal=? AND listing_id=? ORDER BY observed_at DESC LIMIT 1', (portal, listing_id))
        obs = c.fetchone()
        if obs:
            _, _, _, _, _, _, _, payload = obs
            try:
                p = json.loads(payload) if payload else {}
                url = p.get('url', '')
                title = p.get('title', '')
                description = p.get('description', '')
                price_val = p.get('price', '')
                prop_type = p.get('property_type', '')
                city = p.get('city', '')
                governorate = p.get('governorate', '')
                seller = p.get('seller', '')
                seller_type = p.get('seller_type', '')
                status = p.get('status', '')
                published_at = p.get('published_at', '')
                first_seen = p.get('first_seen_at', '')
                last_seen = p.get('last_seen_at', '')
            except:
                url = title = description = ''
                price_val = prop_type = city = governorate = seller = seller_type = status = published_at = first_seen = last_seen = ''
            writer.writerow([portal, listing_id, url, title, description, price_val, currency, prop_type, city, governorate, seller, seller_type, status, published_at, first_seen, last_seen])

print(f'Wrote listings_export.csv with {len(listing_rows)} rows')

conn.close()
print('Done!')