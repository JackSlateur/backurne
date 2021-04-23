import dateutil.parser
import glob
import os
import os.path
import tempfile

from .log import log as Log
from .ceph import Ceph
from .disk import get_fs_info, get_mapped, get_next_nbd, wait_dev, deactivate_vg, resolv_vmfs, resolv_qemu_nbd, filter_children
import sh


class Restore():
	def __init__(self, rbd=None, snap=None, vmdk=None):
		self.ceph = Ceph(None)
		self.dev = None

		self.rbd = rbd
		self.snap = snap
		self.vmdk = vmdk
		self.extsnap = f'{self.rbd}@{self.snap}'
		self.umounted = []

	def ls(self):
		result = list()
		if self.rbd is None:
			for i in self.ceph.ls():
				if i.startswith('restore-'):
					continue
				split = i.split(';')
				if len(split) != 3:
					Log.warning(f'Unknown image: {i}')
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

	def __map_vmdks(self, path):
		if self.vmdk is None:
			Log.debug('No vmdk specified, not mapping those')
			return

		for vmdk in glob.glob(f'{path}/{self.vmdk}/*-flat.vmdk'):
			self.__map_vmdk(vmdk)

	def __map_vmdk(self, vmdk):
		vmdk_file = vmdk.split('/')[-1]
		vmdk_overlay = f'/tmp/{self.clone}-{vmdk_file}.qcow2'
		Log.debug(f'qemu-img create {vmdk_overlay} over {vmdk}')
		sh.Command('qemu-img')('create', '-f', 'qcow2', '-b', vmdk, vmdk_overlay)
		next_nbd = get_next_nbd()
		Log.debug(f'qemu-nbd {vmdk_overlay} as {next_nbd}')
		sh.Command('qemu-nbd')('--connect', next_nbd, vmdk_overlay)
		wait_dev(next_nbd)
		try:
			sh.Command('kpartx')('-av', next_nbd)
			self.mount_dev(next_nbd)
		except Exception:
			pass

	def __mount_vmfs(self, path, tmp_dir):
		for cmd in ('vmfs-fuse', 'vmfs6-fuse'):
			try:
				Log.debug(f'{cmd} {path} {tmp_dir}')
				sh.Command(cmd)(path, tmp_dir)
				self.__map_vmdks(tmp_dir)
				return
			except Exception:
				pass

	def mount_dev(self, dev, ignore_mapped=False):
		wait_dev(dev)
		info = get_fs_info(dev)[0]
		if info['fstype'] == 'VMFS_volume_member':
			info['mountpoint'] = resolv_vmfs(dev)

		if info['fstype'] == 'swap':
			return False

		if info['fstype'] is not None and info['mountpoint'] is None and info['fstype'] != 'LVM2_member':
			tmp_dir = self.get_tmpdir()
			if info['fstype'] == 'VMFS_volume_member':
				self.__mount_vmfs(dev, tmp_dir)
				return True
			Log.debug(f'mounting {dev} as {info["fstype"]} into {tmp_dir}')
			if info['fstype'] == 'xfs':
				Log.debug(f'xfs_repair -L {dev}')
				sh.Command('xfs_repair')('-L', dev)
			Log.debug(f'mount {dev} {tmp_dir}')
			try:
				sh.Command('mount')(dev, tmp_dir)
			except Exception as e:
				os.rmdir(tmp_dir)
				Log.warning(e)
				pass

			return True

		if info['fstype'] == 'VMFS_volume_member':
			changed = False
			for vmdk in glob.glob(f'{info["mountpoint"]}/{self.vmdk}/*-flat.vmdk'):
				vmdk_file = vmdk.split('/')[-1]
				vmdk_overlay = f'/tmp/{self.clone}-{vmdk_file}.qcow2'
				nbd = resolv_qemu_nbd(vmdk_overlay)
				if nbd is None:
					self.__map_vmdk(vmdk)
					return True
				wait_dev(nbd)
				result = self.mount_dev(nbd, ignore_mapped=True)
				if result is True:
					changed = True
			if changed is True:
				return True

		if 'children' not in info:
			return False

		info['children'] = filter_children(info['children'], ignore_mapped)
		for child in info['children']:
			result = self.mount_dev(child['name'])
			if result is True:
				return True
		return False

	def clone_image(self):
		for i in get_mapped(extended=False):
			if i.name.parent_image != self.rbd or i.name.parent_snap != self.snap:
				continue
			self.clone = i.name.image
			self.dev = i.name.dev
			return

		Log.info(f'Cloning {self.extsnap} ..')
		self.ceph.protect(self.extsnap)
		self.clone = self.ceph.clone(self.extsnap)
		self.dev = self.ceph.map(self.clone)

	def mount(self):
		if self.vmdk is None:
			Log.info(f'Mapping {self.extsnap} ..')
		else:
			Log.info(f'Mapping {self.extsnap} with vmdk {self.vmdk} ..')
		self.clone_image()

		if self.dev is None:
			Log.error(f'Cannot map {self.clone} (cloned from {self.extsnap})')
			return

		while self.mount_dev(self.dev):
			Log.debug('Some progress was made, keep running')
			pass

		return

	def has_pv(self, tree):
		for i in tree.descendants:
			if i.name.fstype == 'LVM2_member':
				return True
		return False


	def umount_tree(self, tree, first_pass=False):
		for child in tree.children:
			if child.name.dev.endswith('.vmdk'):
				self.umount_tree(child, first_pass=first_pass)

		for child in tree.children:
			if child.name.dev.endswith('.vmdk'):
				continue
			self.umount_tree(child, first_pass=first_pass)
			if tree.name.fstype == 'LVM2_member':
				deactivate_vg(tree.name.dev)

		if first_pass is True and self.has_pv(tree):
			Log.debug(f'{tree.name.dev}: pv found, return')
			return

		if tree.name.mountpoint is not None:
			if tree.name.mountpoint in self.umounted:
				Log.debug(f'We already umounted {tree.name.mountpoint}')
				return

			self.umounted.append(tree.name.mountpoint)
			Log.debug(f'\t{tree.name.dev}: umount {tree.name.mountpoint}')
			sh.Command('umount')(tree.name.mountpoint)
			Log.debug(f'\t{tree.name.dev}: rmdir {tree.name.mountpoint}')
			os.rmdir(tree.name.mountpoint)
			return

		if tree.name.qemu_nbd is not None:
			Log.debug(f'\t{tree.name.dev}: kpartx -dv {tree.name.qemu_nbd}')
			sh.Command('kpartx')('-dv', tree.name.qemu_nbd)
			Log.debug(f'\t{tree.name.dev}: qemu-nbd --disconnect {tree.name.qemu_nbd}')
			sh.Command('qemu-nbd')('--disconnect', tree.name.qemu_nbd)
			Log.debug(f'\t{tree.name.dev}: rm {tree.name.dev}')
			try:
				os.unlink(tree.name.dev)
			except FileNotFoundError:
				pass
			return

		if tree.name.image is not None and first_pass is False:
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
			Log.debug('First pass: skip devices which contains PV')
			self.umount_tree(i, first_pass=True)
			Log.debug('Second pass: process all remaining devices')
			self.umount_tree(i, first_pass=False)
