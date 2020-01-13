import datetime
import dateutil.parser
import json
import time
from .config import config
from .log import log as Log
from subprocess import Popen, PIPE, DEVNULL
import sh


class Ceph():
	def __init__(self, pool, is_backup=False, endpoint=None):
		self.pool = pool
		if is_backup:
			pool = config['backup_cluster']['pool']
			self.pool = pool
			self.cmd = sh.Command('rbd').bake('-p', pool)
			self.esc = False
		else:
			self.cmd = sh.Command('ssh').bake('-n', endpoint, 'rbd', '-p', pool)
			self.esc = True

		self.json = self.cmd.bake('--format', 'json')
		self.endpoint = endpoint

		if not is_backup:
			self.backup = Ceph(None, is_backup=True)

	def __str__(self):
		return 'pool: %s, endpoint: %s' % (self.pool, self.endpoint)

	def __call__(self, *args):
		return self.cmd(args)

	def __fetch(self, *args):
		result = self.json(args)
		result = json.loads(result.stdout.decode('utf-8'))
		return result

	def __esc(self, snap):
		if self.esc is True:
			return f"'{snap}'"
		else:
			return snap

	def info(self, image):
		return self.__fetch('info', image)

	def ls(self):
		return self.__fetch('ls')

	def du(self, image):
		return self.__fetch('du', image)

	def snap(self, image):
		snap = self.__fetch('snap', 'ls', image)
		snap = [i['name'] for i in snap]
		snap = [i for i in snap if i.startswith(config['snap_prefix'])]
		return snap

	def protect(self, extsnap):
		info = self.info(extsnap)
		if info['protected'] == 'true':
			return
		self('snap', 'protect', extsnap)

	def unprotect(self, extsnap):
		info = self.info(extsnap)
		if info['protected'] == 'false':
			return
		self('snap', 'unprotect', extsnap)

	def clone(self, extsnap):
		for i in range(1, 100):
			clone = f'restore-{i}'
			if not self.exists(clone):
				break
		self('clone', extsnap, f'{self.pool}/{clone}')
		return clone

	def map(self, image):
		# lazy import to avoid circular imports
		from .disk import get_rbd_mapped

		if self.esc is True:
			Log.error('BUG: cannot map via ssh')
			exit(1)

		cmd = ['nbd', 'map', image]
		cmd = str(self.cmd).split(' ') + cmd

		Popen(cmd, stdout=DEVNULL, stderr=DEVNULL)

		# Should be enough .. right ?
		time.sleep(1)
		for mapped in get_rbd_mapped():
			if mapped.image == image:
				return mapped.dev

	def unmap(self, dev):
		if self.esc is True:
			Log.error('BUG: cannot unmap via ssh')
			exit(1)

		sh.Command('rbd-nbd')('unmap', dev)

		# Wait a bit to make sure the dev is effectively gone
		time.sleep(1)

	def rm(self, image):
		Log.debug(f'Deleting image {image} ..')
		try:
			self('rm', image)
		except sh.ErrorReturnCode:
			Log.debug(f'{image} cannot be removed, maybe someone mapped it')

	def rm_snap(self, image, snap):
		Log.debug(f'Deleting snapshot {image}@{snap} .. ')
		snap = self.__esc(snap)
		try:
			self('snap', 'rm', '--snap', snap, image)
		except sh.ErrorReturnCode:
			Log.debug(f'Cannot rm {image}@{snap}, may be held by something')

	def mk_snap(self, image, snap, vm=None):
		snap = self.__esc(snap)

		Log.debug(f'Creating snapshot {image}@{snap} .. ')

		if vm is None:
			self('snap', 'create', '--snap', snap, image)
			return

		self('snap', 'create', '--snap', snap, image)

	def exists(self, image):
		try:
			self.cmd('info', image)
			return True
		except sh.ErrorReturnCode:
			return False

	def do_backup(self, image, snap, dest, last_snap=None):
		# On this function, we burden ourselves with Popen
		# I have not figured out how do fast data transfert
		# between processes with python3-sh
		snap = self.__esc(snap)
		export = ['export-diff', '--no-progress', image, '--snap', snap]
		export = str(self.cmd).split(' ') + export
		if last_snap is None:
			export += ['-', ]
		else:
			last_snap = self.__esc(last_snap)
			export += ['--from-snap', last_snap, '-']

		if config['download_compression'] is True:
			export += ['|', 'gzip']
			imp = f'gunzip | {self.backup.cmd} import-diff --no-progress - "{dest}"'
		else:
			imp = f'{self.backup.cmd} import-diff --no-progress - "{dest}"'

		p1 = Popen(export, stdout=PIPE)

		p2 = Popen(imp, stdin=p1.stdout, shell=True)

		p1.stdout.close()
		p2.communicate()

	def get_last_snap(self, snaps):
		last_date = datetime.datetime.fromtimestamp(0)
		last = None
		for snap in snaps:
			split = snap.split(';')
			date = dateutil.parser.parse(split[3])
			if date > last_date:
				last_date = date
				last = snap
		return last

	def get_last_shared_snap(self, image, dest):
		live_snaps = self.snap(image)
		backup_snaps = self.backup.snap(dest)

		inter = list(set(live_snaps).intersection(backup_snaps))
		return self.get_last_snap(inter)

	def update_desc(self, source, dest):
		split = dest.split(';')
		found = False
		for i in self.ls():
			snap = i.split(';')
			if snap[0] != split[0] or snap[1] != split[1]:
				continue

			if snap[2] == split[2]:
				# This is my image, nothing to do
				continue

			if found is True:
				Log.error(f'{i} matches {dest}, but we already found a match')
			found = True
			self('mv', i, dest)

	def checksum(self, image, snap):
		snap = self.__esc(snap)
		cmd = ['export', image, '--snap', snap, '-']
		cmd = str(self.cmd).split(' ') + cmd

		if self.esc is True:
			# via ssh
			cmd += ['|', config['hash_binary']]
			p1 = Popen(cmd, stdout=PIPE, stderr=DEVNULL)
		else:
			p2 = Popen(cmd, stdout=PIPE, stderr=DEVNULL)
			p1 = Popen([config['hash_binary'], ], stdin=p2.stdout, stdout=PIPE, stderr=DEVNULL)
		out = p1.communicate()[0]
		out = out.decode('utf-8').split(' ')[0]
		return out
