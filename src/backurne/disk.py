from anytree import Node, RenderTree
import json
import sh
from collections import namedtuple
from sh import lsblk, rbd
from .ceph import Ceph
from .log import log as Log


fields = ['dev', 'fstype', 'mountpoint', 'image', 'parent_image', 'parent_snap', 'mapped', 'size']
Part = namedtuple('FS', fields, defaults=(None,) * len(fields))


def get_fs_info(dev):
	info = lsblk('-I', 8, '-p', '-o', '+NAME,FSTYPE,SIZE,MOUNTPOINT', '-J', dev)
	info = json.loads(info.stdout)
	return info['blockdevices']


def add_part(part, parent, extended):
	Node(Part(dev=part['name'], mountpoint=part['mountpoint'], fstype=part['fstype'], size=part['size']), parent=parent)


def get_partitions(dev, node, extended=True, mapped=None):
	for part in get_fs_info(dev):
		if part['fstype'] is not None:
			add_part(part, node, extended)
			continue
		if 'children' not in part:
			continue

		if extended is False:
			sub_node = node
		else:
			sub_node = Node(Part(dev=dev, mapped=mapped, size=part['size']), parent=node)
		for part in part['children']:
			maj = part['maj:min'].split(':')[0]
			# We know that the device is mapped
			# We will ignore non-mapped devices, to avoid duplicates
			if mapped is True and maj != '252':
				continue
			get_partitions(part['name'], sub_node, extended, mapped)


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
