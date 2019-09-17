import dateutil.parser
import os
import tempfile
import time

from .log import log as Log
from .ceph import Ceph
import sh


class Restore():
	def __init__(self, rbd=None, snap=None):
		self.ceph = Ceph(None).backup
		self.tmp_dir = None
		self.dev = None

		self.rbd = rbd
		self.snap = snap
		self.extsnap = f'{self.rbd}@{self.snap}'

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
				Log.info(f'\tmount /dev/mapper/{nbd}pX {self.tmp_dir}')
				Log.info(f'\t# Inspect {self.tmp_dir} and look at your files')
				return
			part = f'/dev/mapper/{nbd}p1'

		time.sleep(0.5)
		try:
			sh.Command('mount')(part, self.tmp_dir)
			Log.info(f'Please find our files in {self.tmp_dir}')
			return self.tmp_dir
		except Exception:
			Log.warning(f'mount {part} {self.tmp_dir} failed')

	def mount(self):
		Log.info(f'Mapping {self.extsnap} ..')
		for i in self.ceph.get_mapped():
			if i['parent_image'] != self.rbd or i['parent_snap'] != self.snap:
				continue
			Log.info(f'Already mapped on {i["dev"]}, and possibly mounted on {i["mountpoint"]}')
			return i['mountpoint']

		self.ceph.protect(self.extsnap)
		clone = self.ceph.clone(self.extsnap)
		self.dev = self.ceph.map(clone)

		if self.dev is None:
			Log.error(f'Cannot map {clone} (cloned from {self.extsnap})')
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
				# Dirty hack
				# We need to zero the xfs logs
				# However, a full xfs_repair can be quite long
				# As the zero log is really fast, 30sec should
				# be enough
				try:
					sh.Command('timeout')('30', 'xfs_repair', '-L', self.dev)
				except sh.ErrorReturnCode:
					# If xfs_repair timed out, an
					# Exception is thrown. Do not care.
					pass

		self.tmp_dir = tempfile.mkdtemp()
		try:
			return self.mount_rbd(kpartx)
		except Exception:
			pass

	def umount(self):
		Log.info(f'Unmapping {self.extsnap} ..')
		for i in self.ceph.get_mapped():
			if i['parent_image'] != self.rbd or i['parent_snap'] != self.snap:
				continue
			Log.info(f'{self.extsnap} currently mapped on {i["dev"]}')
			if i['mountpoint'] is not None:
				try:
					sh.Command('umount')(i['mountpoint'])
				except sh.ErrorReturnCode:
					Log.warning(f'Cannot umount {i["mountpoint"]}, maybe someone is using it ?')
					continue
				os.rmdir(i['mountpoint'])
			sh.Command('kpartx')('-dv', i['dev'])
			self.ceph.unmap(i['dev'])
			self.ceph.rm(i['image'])
			self.ceph.unprotect(self.extsnap)
