#!/usr/bin/python3

from flask import Flask, request
import json

app = Flask(__name__)


def send_json(data, code=200):
	return json.dumps(data), 200, {'Content-Type': 'application/json'}


@app.route('/', methods=['POST'])
def profile():
	# data is fed with something like:
	# {'cluster': {
	# 	'fqdn': 'supercluster.fqdn.org', 'name': 'supercluster', 'type': 'proxmox'},
	# 	'vm': {'name': 'super-server', 'vmid': 115},
	#	'disk': {'rbd': 'vm-115-disk-1', 'ceph': 'cephcluster'}
	# }
	data = request.get_json()

	# Add your logic here
	# As a sample, we only add profiles if the VM's name is 'super-server'
	if data['vm']['name'] == 'super-server' or True:
		# A sample output, which is roughly the same as config's profiles
		# Each profiles will be added to the config's
		# Thus, there is no replacement nor override
		json = {
			'profiles': {
				'daily': {
					'count': 365,
					'max_on_live': 10,
				},
				'hourly': {
					'count': 48,
					'max_on_live': 0,
				},
			}
		}
	else:
		# An empty dict means "no additionnal profile"
		json = {}

	# Additionally, we can disable backups by setting 'backup' to False
	# Any other values are meaningless
	if data['vm']['vmid'] == 1234:
		json['backup'] = False

	return send_json(json)
