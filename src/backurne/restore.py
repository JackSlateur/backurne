import dateutil.parser
import os
import os.path
import tempfile

from .log import log as Log
from .ceph import Ceph
from .disk import get_fs_info, get_mapped, wait_dev
import sh


class Restore():
	def __init__(self, rbd=None, snap=None):
		self.ceph = Ceph(None).backup
		self.dev = None

		self.rbd = rbd
		self.snap = snap
		self.extsnap = f'{self.rbd}@{self.snap}'

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

	def get_tmpdir(self):
		tmp_dir = tempfile.mkdtemp()
		return tmp_dir

	def __mount_part(self, path, fstype):
		if fstype is None or fstype == 'swap':
			return

		tmp_dir = self.get_tmpdir()
		Log.debug(f'mounting {path} as {fstype} into {tmp_dir}')
		if fstype == 'xfs':
			Log.debug(f'xfs_repair -L {path}')
			sh.Command('xfs_repair')('-L', path)
		Log.debug(f'mount {path} {tmp_dir}')
		try:
			sh.Command('mount')(path, tmp_dir)
		except Exception as e:
			Log.warning(e)
			pass

	def mount_dev(self, dev):
		try:
			wait_dev(dev)
			info = get_fs_info(dev)[0]
			self.__mount_part(dev, info['fstype'])
			if 'children' not in info:
				return
			for child in info['children']:
				self.mount_dev(child['name'])
		except Exception as e:
			Log.warning(e)

	def mount(self):
		Log.info(f'Mapping {self.extsnap} ..')
		self.ceph.protect(self.extsnap)
		self.clone = self.ceph.clone(self.extsnap)
		self.dev = self.ceph.map(self.clone)

		if self.dev is None:
			Log.error(f'Cannot map {self.clone} (cloned from {self.extsnap})')
			return

		self.mount_dev(self.dev)
		return

	def umount_tree(self, tree):
		for child in tree.children:
			self.umount_tree(child)
		if tree.name.mountpoint is not None:
			Log.debug(f'\t{tree.name.dev}: umount {tree.name.mountpoint}')
			sh.Command('umount')(tree.name.mountpoint)
			Log.debug(f'\t{tree.name.dev}: rmdir {tree.name.mountpoint}')
			os.rmdir(tree.name.mountpoint)
			return
		if tree.name.mapped is True:
			Log.debug(f'\t{tree.name.dev}: kpartx -dv {tree.name.dev}')
			sh.Command('kpartx')('-dv', tree.name.dev)

		if tree.name.image is not None:
			Log.debug(f'\t{tree.name.dev}: rbd unmap {tree.name.image}')
			self.ceph.unmap(tree.name.dev)
			Log.debug(f'\t{tree.name.dev}: rbd rm {tree.name.image}')
			self.ceph.rm(tree.name.image)
			Log.debug(f'\t{tree.name.dev}: rbd unprotect --snap {tree.name.parent_snap} {tree.name.parent_image}')
			self.ceph.unprotect(f'{tree.name.parent_image}@{tree.name.parent_snap}')
			return
		Log.debug(f'{tree.name.dev}: Nothing to do ?')

	def umount(self, recursed=False):
		Log.info(f'Unmapping {self.extsnap} ..')
		for i in get_mapped():
			part = i.name
			if part.parent_image != self.rbd or part.parent_snap != self.snap:
				continue
			self.umount_tree(i)
