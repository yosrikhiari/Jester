#!/usr/bin/env python
import yaml
with open('config/sources.yaml', 'r') as f:
    d = yaml.safe_load(f)
re = [s for s in d['sources'] if s.get('platform') == 'realestate']
print('Real estate sources:', len(re))
for s in re:
    print('  ', s['name'], s.get('notes', ''))