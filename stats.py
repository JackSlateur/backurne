#!/usr/bin/python3

import json
import humanize
from config import config
from sh import rbd

pool = config['backup_cluster']['pool']
data = rbd('--format', 'json', '-p', pool, 'du')
data = data.stdout.decode('utf-8')
data = json.loads(data)['images']

result = {}
for i in data:
	try:
		result[i['name']] += i['used_size']
	except KeyError:
		result[i['name']] = i['used_size']

result = [(k, result[k]) for k in sorted(result, key=result.get)]
for key, value in result:
	print(key, humanize.naturalsize(value))
