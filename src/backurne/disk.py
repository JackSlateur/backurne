from anytree import Node, RenderTree
import glob
import humanize
import json
import psutil
import os
import sh
from collections import namedtuple
from sh import lsblk, rbd
from .ceph import Ceph
from .log import log as Log


fields = ['dev', 'fstype', 'mountpoint', 'vmfs_fuse', 'image', 'parent_image', 'parent_snap', 'mapped', 'qemu_nbd', 'size']
Part = namedtuple('FS', fields, defaults=(None,) * len(fields))


def get_fs_info(dev):
	info = lsblk('-I', 8, '-p', '-o', '+NAME,FSTYPE,SIZE,MOUNTPOINT', '-J', dev)
	info = json.loads(info.stdout)
	return info['blockdevices']


# vmfs-fuse shows itself as /dev/fuse, in /proc/mounts
# Thus, lsblk cannot resolve the device
# However, the cmdline is straightforward: vmfs-fuse <source dev> <mntpoint>
# We will try to list all running processes, and catch the fuse daemon
def resolv_vmfs(dev):
	for i in psutil.process_iter(attrs=['cmdline', ]):
		i = i.info['cmdline']
		if len(i) == 0:
			continue
		if 'vmfs-fuse' not in i[0]:
			continue
		if i[1] != dev:
			continue
		return i[2]


def resolv_qemu_nbd(dev):
	for i in psutil.process_iter(attrs=['cmdline', ]):
		i = i.info['cmdline']
		if len(i) == 0:
			continue
		if 'qemu-nbd' not in i[0]:
			continue
		if i[3] != dev:
			continue
		return i[2]


def get_next_nbd():
	path = '/sys/class/block/'
	for i in glob.glob(f'{path}/nbd*'):
		dev = i.split('/')[-1]
		if 'p' in dev:
			continue
		if os.path.exists(f'{path}/{dev}/pid'):
			continue
		return f'/dev/{dev}'


def get_file_size(path):
	size = os.stat(path).st_size
	return humanize.naturalsize(size, binary=True)


def add_part(part, parent, extended):
	if part['fstype'] == 'LVM2_member':
		node = Node(Part(dev=part['name'], mountpoint=part['mountpoint'], fstype=part['fstype'], size=part['size']), parent=parent)
		if 'children' not in part:
			return
		for child in part['children']:
			add_part(child, node, extended)
	elif part['fstype'] != 'VMFS_volume_member':
		node = Node(Part(dev=part['name'], mountpoint=part['mountpoint'], fstype=part['fstype'], size=part['size']), parent=parent)
	else:
		part['mountpoint'] = resolv_vmfs(part['name'])
		node = Node(Part(dev=part['name'], mountpoint=part['mountpoint'], fstype=part['fstype'], size=part['size'], vmfs_fuse=True), parent=parent)
		vmdks = '%s/*/*-flat.vmdk' % (part['mountpoint'],)
		for vmdk in glob.glob(vmdks):
			vmdk_size = get_file_size(vmdk)
			sub = Node(Part(dev=vmdk, size=vmdk_size), parent=node)
			vmdk_short = vmdk.split('/')[-1]
			qcow2 = glob.glob(f'/tmp/*{vmdk_short}*.qcow2')[0]
			nbd = resolv_qemu_nbd(qcow2)
			get_partitions(nbd, sub, extended=extended, mapped=True, qemu_nbd=qcow2)


def get_partitions(dev, node, extended=True, mapped=None, qemu_nbd=None):
	for part in get_fs_info(dev):
		if part['fstype'] is not None:
			add_part(part, node, extended)
			continue
		if 'children' not in part:
			continue

		if extended is False:
			sub_node = node
		else:
			sub_node = Node(Part(dev=dev, mapped=mapped, qemu_nbd=qemu_nbd, size=part['size']), parent=node)
		for part in part['children']:
			maj = part['maj:min'].split(':')[0]
			# We know that the device is mapped
			# We will ignore non-mapped devices, to avoid duplicates
			if mapped is True and maj != '252':
				continue
			get_partitions(part['name'], sub_node, extended, mapped, qemu_nbd)


def wait_dev(dev):
	Log.debug(f'udevadm trigger {dev} -w')
	sh.Command('udevadm')('trigger', dev, '-w')
	sh.Command('udevadm')('settle')


def print_node(pre, _node):
	node = _node.name
	if node.parent_image is not None:
		Log.info('%srbd %s / snap %s' % (pre, node.parent_image, node.parent_snap))
		return

	if node.mountpoint is not None:
		msg = 'on %s ' % (node.mountpoint,)
	else:
		msg = ''

	if node.dev.endswith('.vmdk'):
		dev = node.dev.split('/')[-2]
		dev = 'vmdk %s' % (dev,)
		fstype = 'vmfs file'
	else:
		dev = node.dev
		fstype = 'fstype %s' % (node.fstype,)
	Log.info('%s%s %s(%s, size %s)' % (pre, dev, msg, fstype, node.size))


def print_mapped(mapped):
	for tree in mapped:
		for pre, fill, node in RenderTree(tree):
			print_node(pre, node)


def prepare_tree_to_json(mapped):
	result = mapped.name._asdict()
	result['children'] = []
	for child in mapped.children:
		result['children'].append(prepare_tree_to_json(child))
	return result


def get_rbd_mapped():
	result = []
	mapped = rbd('--format', 'json', '-t', 'nbd', 'device', 'list')
	for mapped in json.loads(mapped.stdout):
		info = Ceph(None).backup.info(mapped['image'])['parent']
		part = Part(dev=mapped['device'], image=mapped['image'], parent_image=info['image'], parent_snap=info['snapshot'])
		result.append(part)
	return result


def get_mapped(extended=True):
	extended = True
	result = []
	for i in get_rbd_mapped():
		node = Node(i)
		get_partitions(i.dev, node, extended=extended)
		result.append(node)
	return result
