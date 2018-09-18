#!/usr/bin/python3

import json
import humanize
import multiprocessing
from config import config
from sh import rbd


pool = config['backup_cluster']['pool']


def du(image):
	data = rbd('--format', 'json', '-p', pool, 'du', image)
	data = data.stdout.decode('utf-8')
	data = json.loads(data)['images']
	return data


data = rbd('--format', 'json', '-p', pool, 'ls')
data = data.stdout.decode('utf-8')
data = json.loads(data)

result = {}

with multiprocessing.Pool(config['backup_worker']) as p:
	for sizes in p.imap_unordered(du, data):
		for i in sizes:
			try:
				result[i['name']] += i['used_size']
			except KeyError:
				result[i['name']] = i['used_size']

result = [(k, result[k]) for k in sorted(result, key=result.get)]
for key, value in result:
	print(key, humanize.naturalsize(value))
