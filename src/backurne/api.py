#!/usr/bin/python3

from flask import Flask
from flask import Blueprint
from flask_autoindex import AutoIndexBlueprint
import urllib.parse
import json
from .restore import Restore
from .disk import prepare_tree_to_json, get_mapped


app = Flask(__name__)


def send_json(data, code=200):
	return json.dumps(data), 200, {'Content-Type': 'application/json'}


@app.route('/backup/')
def ls():
	restore = Restore()
	data = restore.ls()

	result = list()
	for i in data:
		result.append({
			'ident': i['ident'],
			'disk': i['disk'],
			'uuid': i['uuid'],
		})

	return send_json(result)


@app.route('/backup/host/<host>/')
def get(host):
	restore = Restore()
	data = restore.ls()

	result = list()
	for i in data:
		if i['ident'] == host:
			result.append({
				'ident': i['ident'],
				'disk': i['disk'],
				'uuid': i['uuid'],
			})

	return send_json(result)


@app.route('/backup/<rbd>/')
def ls_snaps(rbd):
	rbd = urllib.parse.unquote(rbd)
	restore = Restore(rbd)
	data = restore.ls()

	result = list()
	for i in data:
		result.append({
			'creation_date': str(i['creation']),
			'uuid': i['uuid'],
		})

	return send_json(result)


@app.route('/map/<rbd>/<snap>/')
def map(rbd, snap):
	restore = Restore(rbd, snap)
	status = restore.mount()
	if status is None:
		return send_json({'success': False, 'path': None}, code=500)
	else:
		status = status.replace('/tmp/', '')
		return send_json({'success': True, 'path': status})


@app.route('/unmap/<rbd>/<snap>/')
def unmap(rbd, snap):
	restore = Restore(rbd, snap)
	restore.umount()
	return send_json({'success': True})


@app.route('/mapped/')
def mapped():
	data = get_mapped(extended=False)
	result = []
	for tree in data:
		result.append(prepare_tree_to_json(tree))
	return send_json(result)


auto_bp = Blueprint('auto_bp', __name__)
# FIXME: use config or something
AutoIndexBlueprint(auto_bp, browse_root='/tmp/')

app.register_blueprint(auto_bp, url_prefix='/explore')
