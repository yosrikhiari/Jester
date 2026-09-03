#!/usr/bin/env python
import yaml

profile_path = 'config/profiles/seloger.yaml'
with open(profile_path) as f:
    d = yaml.safe_load(f)

d['extract'] = {
    'mode': 'nextdata',
    'data_path': 'props.pageProps.searchResults.properties'
}
with open(profile_path, 'w') as f:
    yaml.dump(d, f, default_flow_style=False)
print('Profile reset')