import collections
import importlib.util
import os


# Random code from https://gist.github.com/angstwad/bf22d1822c38a92ec0a9
def dict_merge(dct, merge_dct):
	for k, v in merge_dct.items():
		if k in dct and isinstance(dct[k], dict) \
				and isinstance(merge_dct[k], collections.Mapping):
			dict_merge(dct[k], merge_dct[k])
		else:
			dct[k] = merge_dct[k]
	return dct


def load_config():
	def load(path):
		source = importlib.machinery.SourceFileLoader('custom', path)
		spec = importlib.util.spec_from_loader('custom', source)
		custom = importlib.util.module_from_spec(spec)
		try:
			spec.loader.exec_module(custom)
		except FileNotFoundError:
			return None
		return custom

	# Default config
	config = {
		'snap_prefix': 'backup',
		'profiles': {},
		'profiles_api': None,
		'backup_cluster': {
			'pool': 'rbd',
		},
		'live_clusters': [],
		'extra_retention_time': 0,
		'ceph_endpoint': {},
		'download_compression': False,
		'fsfreeze': True,
		'uuid_fallback': True,
		'pretty_colors': True,
		'log_level': 'debug',
		'backup_worker': 24,
		'live_worker': 12,
		'hash_binary': '/usr/bin/md5sum',
	}

	for custom in ('custom.conf',):
		custom = load(custom)
		if custom is not None:
			break

	if custom is None:
		return config

	config = dict_merge(config, custom.config)

	prefix = config['snap_prefix']
	if ';' in prefix or ';' in prefix:
		print('''fatal: "'" or " " found in snap_prefix (%s)''' % (prefix,))
		exit(1)

	return config


config = load_config()
