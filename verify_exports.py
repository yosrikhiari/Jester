import csv

# Check listings_export.csv
with open('exports/listings_export.csv', 'r', encoding='utf-8') as f:
    reader = csv.reader(f)
    rows = list(reader)
    print('listings_export.csv: {} rows (1 header + {} data)'.format(len(rows), len(rows)-1))
    if rows:
        print('Header: {}'.format(rows[0]))
        print('First data row: {}...'.format(rows[1][:8]))
        first = rows[1]
        non_empty = sum(1 for f in first if f.strip())
        print('First row has {} non-empty fields out of {}'.format(non_empty, len(first)))

print()

# Check observations_export.csv
with open('exports/observations_export.csv', 'r', encoding='utf-8') as f:
    reader = csv.reader(f)
    rows = list(reader)
    print('observations_export.csv: {} rows (1 header + {} data)'.format(len(rows), len(rows)-1))
    if rows:
        print('Header: {}'.format(rows[0]))
        print('First data row: {}...'.format(rows[1][:8]))
        first = rows[1]
        non_empty = sum(1 for f in first if f.strip())
        print('First row has {} non-empty fields out of {}'.format(non_empty, len(first)))