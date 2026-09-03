import csv
with open('data/realestate_listings.csv', 'r') as f:
    reader = csv.reader(f)
    rows = list(reader)
    print('Total rows (incl header):', len(rows))
    if rows:
        print('Header:', rows[0])
        if len(rows) > 1:
            print('First data row:', rows[1])