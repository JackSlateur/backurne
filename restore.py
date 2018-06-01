import dateutil.parser
import multiprocessing
import os
import tempfile
import time

from log import log as Log
from ceph import Ceph
import sh


class Restore(object):
	def __init__(self, rbd=None, snap=None):
		self.ceph = Ceph(None).backup
		self.tmp_dir = None
		self.dev = None

		self.rbd = rbd
		self.snap = snap

	def list_mapped(self):
		return self.ceph.get_mapped()

	def ls(self):
		result = list()
		if self.rbd is None:
			for i in self.ceph.ls():
				if i.startswith('restore-'):
					continue
				split = i.split(';')
				result.append({
					'ident': split[2],
					'disk': split[1],
					'uuid': i,
				})
		else:
			for i in self.ceph.snap(self.rbd):
				split = i.split(';')
				creation = dateutil.parser.parse(split[3])
				result.append({
					'creation': creation,
					'uuid': i,
				})
		return result

	def mount_rbd(self, kpartx):
		part = self.dev
		if kpartx is True:
			maps = sh.Command('kpartx')('-av', self.dev)
			for mapped in maps:
				mapped = mapped.rstrip()
				Log.info(mapped)

			nbd = self.dev.split('/')[2]

			# len(..) == 2 -> only one partition is found
			if len(maps.split('\n')) != 2:
				Log.info('You can now:')
				Log.info('\tmount /dev/mapper/%spX %s' % (nbd, self.tmp_dir))
				Log.info('\t# Inspect %s and look at your files' % (self.tmp_dir,))
				return
			part = '/dev/mapper/%sp1' % (nbd,)

		time.sleep(0.5)
		try:
			sh.Command('mount')(part, self.tmp_dir)
			Log.info('Please find our files in %s' % (self.tmp_dir,))
			return self.tmp_dir
		except Exception as e:
			Log.warn('mount %s %s failed' % (part, self.tmp_dir))

	def mount(self):
		Log.info('Mapping %s@%s ..' % (self.rbd, self.snap))
		for i in self.ceph.get_mapped():
			if i['parent_image'] != self.rbd or i['parent_snap'] != self.snap:
				continue
			Log.info('Already mapped on %s, and possibly mounted on %s' % (i['dev'], i['mountpoint']))
			return i['mountpoint']

		self.ceph.protect(self.rbd, self.snap)
		clone = self.ceph.clone(self.rbd, self.snap)
		self.dev = self.ceph.map(clone)

		if self.dev is None:
			Log.error('Cannot map %s (cloned from %s@%s)' % (clone, self.rbd, self.snap))
			return

		kpartx = False
		for part in sh.Command('wipefs')('-p', self.dev):
			if part.startswith('#'):
				continue
			part = part.rstrip()
			part_type = part.split(',')[3]
			if part_type in ('dos', 'gpt'):
				kpartx = True
				break
			if part_type == 'xfs':
				sh.Command('xfs_repair')('-L', self.dev)

		self.tmp_dir = tempfile.mkdtemp()
		try:
			return self.mount_rbd(kpartx)
		except:
			pass

	def umount(self):
		Log.info('Unmapping %s@%s ..' % (self.rbd, self.snap))
		for i in self.ceph.get_mapped():
			if i['parent_image'] != self.rbd or i['parent_snap'] != self.snap:
				continue
			Log.info('%s@%s currently mapped on %s' % (self.rbd, self.snap, i['dev']))
			if i['mountpoint'] is not None:
				try:
					sh.Command('umount')(i['mountpoint'])
				except:
					Log.warn('Cannot umount %s, maybe someone is using it ?' % (i['mountpoint'],))
					continue
				os.rmdir(i['mountpoint'])
			sh.Command('kpartx')('-dv', i['dev'])
			self.ceph.unmap(i['dev'])
			self.ceph.rm(i['image'])
			self.ceph.unprotect(self.rbd, self.snap)
