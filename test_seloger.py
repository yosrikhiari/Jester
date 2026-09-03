#!/usr/bin/env python
import yaml, subprocess, os

profile_path = 'config/profiles/seloger.yaml'
with open(profile_path) as f:
    d = yaml.safe_load(f)

paths_to_try = [
    'props.pageProps.searchResults.properties',
    'props.pageProps.searchResults',
    'props.pageProps.results',
    'props.pageProps.listings',
    'props.pageProps.data',
    'props.pageProps.hits',
    'props.pageProps.items',
]

for path in paths_to_try:
    d['extract']['data_path'] = path
    with open(profile_path, 'w') as f:
        yaml.dump(d, f, default_flow_style=False)
    
    result = subprocess.run(
        ['go', 'run', './cmd/worker/', '-config', '..\\config', '-live', '-only', 'seloger', '-run', 'scrape_all'],
        cwd='go', capture_output=True, text=True, timeout=30
    )
    print(f'Path: {path}')
    print(f'  stdout: {result.stdout[-300:] if result.stdout else ""}')
    print(f'  stderr: {result.stderr[-300:] if result.stderr else ""}')
    print()