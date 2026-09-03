#!/usr/bin/env python
with open('config/sources.yaml', 'rb') as f:
    data = f.read()

# Remove all null bytes
fixed = data.replace(b'\x00', b'')

with open('config/sources.yaml', 'wb') as f:
    f.write(fixed)

print(f'Fixed: removed null bytes, file now {len(fixed)} bytes')