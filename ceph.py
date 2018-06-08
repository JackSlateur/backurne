import datetime
import dateutil.parser
import json
import time
from config import config
from log import log as Log
from subprocess import Popen, PIPE, DEVNULL
import sh


class Ceph(object):
	def __init__(self, pool, is_backup=False, endpoint=None):
		self.pool = pool
		if is_backup:
			pool = config['backup_cluster']['pool']
			self.pool = pool
			self.cmd = sh.Command('rbd').bake('-p', pool)
			self.esc = False
		else:
			self.cmd = sh.Command('ssh').bake(endpoint, 'rbd', '-p', pool)
			self.esc = True

		self.json = self.cmd.bake('--format', 'json')
		self.endpoint = endpoint

		if not is_backup:
			self.backup = Ceph(None, is_backup=True)

	def __call__(self, *args):
		return self.cmd(args)

	def __fetch(self, *args):
		result = self.json(args)
		result = json.loads(result.stdout.decode('utf-8'))
		return result

	def __esc(self, snap):
		if self.esc is True:
			return "'%s'" % (snap,)
		else:
			return snap

	def ls(self):
		return self.__fetch('ls')

	def lsclone(self, image, snap):
		return self.__fetch('children', image, '--snap', snap)

	def du(self, image):
		return self.__fetch('du', image)

	def info(self, image, snap=None):
		if snap is None:
			return self.__fetch('info', image)
		else:
			return self.__fetch('info', image, '--snap', snap)

	def snap(self, image):
		snap = self.__fetch('snap', 'ls', image)
		snap = [i['name'] for i in snap]
		snap = [i for i in snap if i.startswith(config['snap_prefix'])]
		return snap

	def protect(self, image, snap):
		info = self.info(image, snap)
		if info['protected'] == 'true':
			return
		self('snap', 'protect', image, '--snap', snap)

	def unprotect(self, image, snap):
		info = self.info(image, snap)
		if info['protected'] == 'false':
			return
		self('snap', 'unprotect', image, '--snap', snap)

	def clone(self, image, snap):
		for i in range(1, 100):
			clone = 'restore-%s' % (i,)
			if not self.exists(clone):
				break
		self('clone', image, '--snap', snap, '%s/%s' % (self.pool, clone))
		return clone

	def get_mapped(self):
		result = []
		rbd_nbd = sh.Command('rbd-nbd')
		for mapped in rbd_nbd('list-mapped'):
			if mapped.startswith('pid'):
				continue
			mapped = mapped.split(' ')
			mapped = [i for i in mapped if i != '']

			parent = self.info(mapped[2])['parent']
			dev = mapped[-2].replace('/dev/', '')
			for mount in open('/proc/mounts').readlines():
				mount = mount.rstrip('\n')
				if not mount.startswith('/dev/mapper/%s' % (dev,)) and not mount.startswith('/dev/%s' % (dev,)):
					# We do not found a mountpoint, set it to None
					# to avoid polluting the output
					mount = None
					continue
				mount = mount.split(' ')[1]
				break
			result.append({
				'parent_image': parent['image'],
				'parent_snap': parent['snapshot'],
				'image': mapped[2],
				'mountpoint': mount,
				'dev': mapped[-2],
			})

		return result

	def map(self, image):
		if self.esc is True:
			Log.error('BUG: cannot map via ssh')
			exit(1)

		cmd = ['nbd', 'map', image]
		cmd = str(self.cmd).split(' ') + cmd

		Popen(cmd, stdout=DEVNULL, stderr=DEVNULL)

		# Should be enough .. right ?
		time.sleep(0.5)
		for mapped in self.get_mapped():
			if mapped['image'] == image:
				return mapped['dev']

	def unmap(self, dev):
		if self.esc is True:
			Log.error('BUG: cannot unmap via ssh')
			exit(1)

		sh.Command('rbd-nbd')('unmap', dev)

	def rm(self, image):
		Log.info('Deleting %s ..' % (image,))
		try:
			self('rm', image)
		except:
			Log.debug('%s cannot be removed, maybe someone mapped it' % (image,))

	def rm_snap(self, image, snap):
		Log.info('Deleting %s@%s .. ' % (image, snap))
		snap = self.__esc(snap)
		try:
			self('snap', 'rm', '--snap', snap, image)
		except:
			Log.debug('Cannot rm %s@%s, may be held by something' % (image, snap))

	def mk_snap(self, image, snap, vm=None):
		snap = self.__esc(snap)

		if vm is None:
			self('snap', 'create', '--snap', snap, image)
			return

		try:
			self('snap', 'create', '--snap', snap, image)
		except Exception as e:
			raise e

	def exists(self, image):
		try:
			self.cmd('info', image)
			return True
		except:
			return False

	def do_backup(self, image, snap, dest, last_snap=None):
		# On this function, we burden ourselves with Popen
		# I have not figured out how do fast data transfert
		# between processes with python3-sh
		snap = self.__esc(snap)
		export = ['export-diff', image, '--snap', snap]
		export = str(self.cmd).split(' ') + export
		if last_snap is None:
			export += ['-', ]
		else:
			last_snap = self.__esc(last_snap)
			export += ['--from-snap', last_snap, '-']

		if config['pigz_processes'] > 0:
			export += ['|', 'pigz', '-p', str(config['pigz_processes']), '-f', '-']
			imp = 'unpigz -f - | %s import-diff - "%s"' % (self.backup.cmd, dest)
		else:
			imp = '%s import-diff - "%s"' % (self.backup.cmd, dest)

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
				Log.error('%s matches %s, but we already found a match' % (i, dest))
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
