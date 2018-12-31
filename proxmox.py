import re
from config import config
from log import log as Log
from ceph import Ceph
from proxmoxer import ProxmoxAPI


class Proxmox():
	def __init__(self, px_config):
		self.px_config = px_config
		self.px = ProxmoxAPI(px_config['fqdn'],
				user=px_config['user'],
				password=px_config['passwd'],
				verify_ssl=px_config['tls'])
		self.ceph_storage = self.__get_ceph_storage()

	def __get_ceph_endpoint(self, storage):
		px = self.px_config['name']
		ceph = storage
		if px in config['ceph_endpoint']:
			if ceph in config['ceph_endpoint'][px]:
				return config['ceph_endpoint'][px][ceph]
		if 'default' in config['ceph_endpoint']:
			if ceph in config['ceph_endpoint']['default']:
				return config['ceph_endpoint']['default'][ceph]
		return storage

	def __get_ceph_storage(self):
		result = {}
		for storage in self.px.storage.get():
			if storage['type'] != 'rbd':
				continue
			name = storage['storage']
			endpoint = self.__get_ceph_endpoint(name)

			result[name] = Ceph(storage['pool'], endpoint=endpoint)
		return result

	def nodes(self):
		nodes = self.px.nodes.get()
		return [i['node'] for i in nodes]

	def vms(self):
		vms = list()
		for node in self.nodes():
			try:
				vms += self.list_qemu(node)
			except Exception as e:
				Log.error('Cannot list VMs on node %s: error %s received' % (node, e))
		return vms

	def get_smbios(self, conf):
		for key, value in conf.items():
			if not re.match('^smbios', key):
				continue
			return value.split('=')[1]
		return None

	def __extract_disk(self, key, value):
		disk = False
		if re.match('^virtio[0-9]+$', key):
			disk = True
		elif re.match('^ide[0-9]+$', key):
			disk = True
		elif re.match('^scsi[0-9]+$', key):
			disk = True
		elif re.match('^sata[0-9]+$', key):
			disk = True

		# Exclude cdrom
		if re.match('.*media=.*', str(value)):
			disk = False

		# "No backup" is set
		if re.match('.*backup=0.*', str(value)):
			return None, None, None

		if not disk:
			return None, None, None

		storage, volume = value.split(':')
		if storage not in self.ceph_storage:
			return None, None, None

		volume = volume.split(',')[0]

		match = re.match('vm-([0-9]+)-disk-[0-9]+', volume)
		if match is None:
			match = re.match('base-([0-9]+)-disk-[0-9]+', volume)
		if match.group(1) != str(self.vmid):
			return None, None, None

		return storage, volume, key

	def get_disks(self, conf):
		result = list()
		for key, value in conf.items():
			storage, volume, adapter = self.__extract_disk(key, value)
			if storage is None:
				continue
			result.append({'ceph': storage, 'rbd': volume, 'adapter': adapter})
		return result

	def list_qemu(self, name):
		node = self.px.nodes(name)

		qemu = node.qemu.get()
		for i in qemu:
			self.vmid = i['vmid']
			i['px'] = self
			i['node'] = name
			i['config'] = node.qemu(i['vmid']).config.get()
			i['smbios'] = self.get_smbios(i['config'])
			i['to_backup'] = self.get_disks(i['config'])
			if 'agent' in i['config']:
				i['qemu_agent'] = i['config']['agent']
		return qemu

	def is_running(self, qemu):
		status = qemu.status.get('current')['status']
		if status == 'stopped':
			return False
		return True

	def freeze(self, node, vm):
		if not config['fsfreeze'] or 'qemu_agent' not in vm:
			return
		if vm['qemu_agent'] != 1:
			return
		qemu = self.px.nodes(node).qemu(vm['vmid'])
		if not self.is_running(qemu):
			return

		try:
			Log.debug('Freezing %s' % (vm['vmid'],))
			qemu.agent.post('fsfreeze-freeze')
		except Exception as e:
			Log.warn('Cannot freeze %s: %s' % (vm['vmid'], e))

	def thaw(self, node, vm):
		if not config['fsfreeze'] or 'qemu_agent' not in vm:
			return
		if vm['qemu_agent'] != 1:
			return

		qemu = self.px.nodes(node).qemu(vm['vmid'])
		if not self.is_running(qemu):
			return

		try:
			Log.debug('Thawing %s' % (vm['vmid'],))
			qemu.agent.post('fsfreeze-thaw')
		except Exception as e:
			Log.warn('Cannot thaw %s: %s' % (vm['vmid'], e))
