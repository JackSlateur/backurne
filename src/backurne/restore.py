import dateutil.parser
import glob
import os
import os.path
import tempfile

from .log import log as Log
from .ceph import Ceph
from .disk import get_fs_info, get_mapped, get_next_nbd, wait_dev
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
				if len(split) != 3:
					Log.warn(f'Unknown image {i}')
					continue
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

	def __map_vmdk(self, path):
		for vmdk in glob.glob(f'{path}/*/*-flat.vmdk'):
			vmdk_file = vmdk.split('/')[-1]
			vmdk_overlay = f'/tmp/{self.clone}-{vmdk_file}.qcow2'
			Log.debug(f'qemu-img create {vmdk_overlay} over {vmdk}')
			sh.Command('qemu-img')('create', '-f', 'qcow2', '-b', vmdk, vmdk_overlay)
			next_nbd = get_next_nbd()
			Log.debug(f'qemu-nbd {vmdk_overlay} as {next_nbd}')
			sh.Command('qemu-nbd')('--connect', next_nbd, vmdk_overlay)
			wait_dev(next_nbd)
			try:
				maps = sh.Command('kpartx')('-av', next_nbd)
				for mapped in maps:
					mapped = mapped.rstrip()
					dev = mapped.split(' ')[2]
					dev = f'/dev/mapper/{dev}'
					self.mount_dev(dev)
			except Exception:
				pass

	def __mount_vmfs(self, path, tmp_dir):
		for cmd in ('vmfs-fuse', 'vmfs6-fuse'):
			try:
				Log.debug(f'{cmd} {path} {tmp_dir}')
				sh.Command(cmd)(path, tmp_dir)
				self.__map_vmdk(tmp_dir)
				return
			except Exception:
				pass

	def __mount_part(self, path, fstype):
		if fstype is None or fstype == 'swap':
			return

		if fstype == 'LVM2_member':
			# get_fs_info will see the activated LVs as children,
			# so they will be processed next
			# I'll just wait for this partition, to make sure
			# sub-devices will be there when I get to them
			wait_dev(path)
			return

		tmp_dir = self.get_tmpdir()
		Log.debug(f'mounting {path} as {fstype} into {tmp_dir}')
		if fstype == 'xfs':
			Log.debug(f'xfs_repair -L {path}')
			sh.Command('xfs_repair')('-L', path)
		if fstype == 'VMFS_volume_member':
			self.__mount_vmfs(path, tmp_dir)
		else:
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
			if tree.name.fstype == 'LVM2_member':
				Log.debug(f'\t{tree.name.dev}: lvchange -an {child.name.dev}')
				sh.Command('lvchange')('-an', child.name.dev)
		if tree.name.mountpoint is not None:
			Log.debug(f'\t{tree.name.dev}: umount {tree.name.mountpoint}')
			sh.Command('umount')(tree.name.mountpoint)
			Log.debug(f'\t{tree.name.dev}: rmdir {tree.name.mountpoint}')
			os.rmdir(tree.name.mountpoint)
			return
		if tree.name.mapped is True:
			Log.debug(f'\t{tree.name.dev}: kpartx -dv {tree.name.dev}')
			sh.Command('kpartx')('-dv', tree.name.dev)
			if tree.name.qemu_nbd is not None:
				Log.debug(f'\t{tree.name.dev}: qemu-nbd --disconnect {tree.name.dev}')
				sh.Command('qemu-nbd')('--disconnect', tree.name.dev)
				Log.debug(f'\t{tree.name.dev}: rm {tree.name.qemu_nbd}')
				os.unlink(tree.name.qemu_nbd)
				return

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
