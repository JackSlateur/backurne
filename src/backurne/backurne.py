#!/usr/bin/python3

import atexit
import datetime
import dateutil.parser
import filelock
import json
import multiprocessing
import progressbar
import requests
import setproctitle
import sqlite3
import sys

from . import pretty
from .config import config
from .log import log as Log
from .ceph import Ceph
from .proxmox import Proxmox
from .restore import Restore
from .backup import Bck


class Check:
	def __init__(self, cluster):
		self.cluster = cluster
		self.err = list()

	def add_err(self, msg):
		if msg is None:
			return
		msg['cluster'] = self.cluster['name']
		self.err.append(msg)

	def check_img(self, args):
		try:
			ceph = args['ceph']
			backup = args['backup']
			rbd = args['image']

			if not ceph.backup.exists(backup.dest):
				msg = 'No backup found for %s (image does not exists)' % (rbd,)
				return {'image': rbd, 'msg': msg}

			last = ceph.get_last_shared_snap(rbd, backup.dest)
			if last is None:
				msg = 'No backup found for %s (no shared snap)' % (rbd,)
				return {'image': rbd, 'msg': msg}

			when = last.split(';')[3]
			when = dateutil.parser.parse(when)
			deadline = datetime.timedelta(days=1) + datetime.timedelta(hours=6)
			deadline = datetime.datetime.now() - deadline
			if when < deadline:
				msg = 'Backup found for %s, yet too old (created at %s)' % (rbd, when)
				return {'image': rbd, 'msg': msg}
		except Exception as e:
			Log.warning('Exception thrown while checking %s : %s' % (args, e))

	def cmp_snap(self, backup, ceph, rbd):
		live_snaps = ceph.snap(rbd)
		try:
			backup_snaps = ceph.backup.snap(backup.dest)
		except:
			backup_snaps = []
		inter = list(set(live_snaps).intersection(backup_snaps))
		for snap in inter:
			Log.debug('checking %s @ %s' % (rbd, snap))
			live = ceph.checksum(rbd, snap)
			back = ceph.backup.checksum(backup.dest, snap)
			if live == back:
				continue

			err = {
				'image': rbd,
				'msg': 'ERR: shared snapshot %s does not match\n\tOn live (image: %s): %s\n\tOn backup (image: %s): %s' % (snap, rbd, live, backup.dest, back),
			}
			self.add_err(err)


class CheckProxmox(Check):
	def __init__(self, cluster):
		super().__init__(cluster)
		self.px = Proxmox(cluster)

	def check(self):
		data = list()
		for vm in self.px.vms():
			for disk in vm['to_backup']:
				ceph = self.px.ceph_storage[disk['ceph']]
				bck = Bck(disk['ceph'], ceph, disk['rbd'], vm=vm, adapter=disk['adapter'])
				data.append({'ceph': ceph, 'backup': bck, 'image': disk['rbd']})

		self.err = list()
		with multiprocessing.Pool() as pool:
			for msg in pool.imap_unordered(self.check_img, data):
				self.add_err(msg)

		return self.err

	def check_snap(self):
		for vm in self.px.vms():
			for disk in vm['to_backup']:
				ceph = self.px.ceph_storage[disk['ceph']]
				bck = Bck(disk['ceph'], ceph, disk['rbd'], vm=vm, adapter=disk['adapter'])
				self.cmp_snap(bck, ceph, disk['rbd'])
		return self.err


class CheckPlain(Check):
	def __init__(self, cluster):
		super().__init__(cluster)
		self.ceph = Ceph(self.cluster['pool'], endpoint=self.cluster['fqdn'])

	def check(self):
		for rbd in self.ceph.ls():
			bck = Bck(self.cluster['name'], self.ceph, rbd)
			self.check_img(self.ceph, bck, rbd)
		return self.err

	def check_snap(self):
		for rbd in self.ceph.ls():
			bck = Bck(self.cluster['name'], self.ceph, rbd)
			self.cmp_snap(bck, self.ceph, rbd)
		return self.err


class Backup:
	def __init__(self, cluster, queue, status_queue):
		self.cluster = cluster
		self.queue = queue
		self.status_queue = status_queue

	def is_expired(snap, last=False):
		splited = snap.split(';')
		created_at = dateutil.parser.parse(splited[-1])
		profile = splited[-3]
		value = int(splited[-2])
		if profile == 'daily':
			expiration = datetime.timedelta(days=value)
		elif profile == 'hourly':
			expiration = datetime.timedelta(hours=value)
		else:
			Log.warning('Unknown profile found, no action taken: %s' % (profile,))
			return False

		expired_at = created_at + expiration
		if last is True:
			expired_at += datetime.timedelta(days=config['extra_retention_time'])

		now = datetime.datetime.now()
		if expired_at > now:
			return False
		return True

	def _create_snap(self, bck, profiles):
		todo = list()
		filename = bck.build_dest().replace('/', '')
		filename = '%s/%s' % (config['lockdir'], filename)
		Log.debug('locking %s' % (filename,))
		lock = filelock.FileLock(filename, timeout=0)

		try:
			with lock:
				for profile, value in profiles:
					self.status_queue.put('add_item')
					if not bck.check_profile(profile):
						self.status_queue.put('done_item')
						continue

					dest, last_snap, snap_name = bck.make_snap(profile, value['count'])
					if dest is not None:
						todo.append({
							'dest': dest,
							'last_snap': last_snap,
							'snap_name': snap_name,
							'backup': bck,
						})
		except filelock.Timeout as e:
			Log.debug(e)
			return
		Log.debug('releasing lock %s' % (filename,))
		if len(todo) != 0:
			self.queue.put(todo)

	def create_snaps(self):
		items = self.list()
		with multiprocessing.Pool(config['live_worker']) as pool:
			for i in pool.imap_unordered(self.create_snap, items):
				pass

	def _expire_item(self, ceph, disk, vm=None):
		self.status_queue.put('add_item')
		self.status_queue.put('done_item')

		if vm is not None:
			bck = Bck(disk['ceph'], ceph, disk['rbd'], vm=vm, adapter=disk['adapter'])
			rbd = disk['rbd']
		else:
			bck = Bck(self.cluster['name'], ceph, disk)
			rbd = disk

		bck.build_dest()

		backups = Ceph(None).backup.snap(bck.build_dest())

		snaps = ceph.snap(rbd)
		shared = list(set(backups).intersection(snaps))

		try:
			shared = sorted(shared).pop()
		except IndexError:
			shared = None

		by_profile = {}
		for snap in snaps:
			# The last shared snapshot must be kept
			# Also, subsequent snaps shall be kept as well,
			# because a backup may be pending elsewhere
			if snap >= shared:
				continue
			tmp = snap.split(';')
			if tmp[1] not in by_profile:
				by_profile[tmp[1]] = list()
			i = by_profile[tmp[1]]
			i.append(snap)

		to_del = list()
		for profile, snaps in by_profile.items():
			try:
				profile = config['profiles'][profile]
			except KeyError:
				# Profile no longer exists, we can drop all these snaps
				to_del += snaps
				continue
			try:
				max_on_live = profile['max_on_live']
			except KeyError:
				max_on_live = 1

			for _ in range(0, max_on_live):
				try:
					snaps.pop()
				except IndexError:
					# We do not have enough snaps on live
					# snaps is now an empty list, nothing to delete
					break

			to_del += snaps
		for i in to_del:
			ceph.rm_snap(rbd, i)

	def expire_live(self):
		items = self.list()
		with multiprocessing.Pool(config['live_worker']) as pool:
			for i in pool.imap_unordered(self.expire_item, items):
				pass

	def expire_backup(i):
		ceph = i['ceph']
		image = i['image']
		i['status_queue'].put('done_item')

		filename = image.replace('/', '')
		filename = '%s/%s' % (config['lockdir'], filename)
		Log.debug('locking %s' % (filename,))
		lock = filelock.FileLock(filename, timeout=0)

		try:
			with lock:
				snaps = ceph.backup.snap(image)
				try:
					# Pop the last snapshot
					# We will take care of it later
					last = snaps.pop()
				except IndexError:
					# We found an image without snapshot
					# Someone is messing around, or this is a bug
					# Anyway, the image can be deleted
					ceph.backup.rm(image)
					Log.debug('releasing lock %s' % (filename,))
					return

				for snap in snaps:
					if not Backup.is_expired(snap):
						continue
					ceph.backup.rm_snap(image, snap)

				snaps = ceph.backup.snap(image)
				if len(snaps) == 1:
					if Backup.is_expired(last, last=True):
						ceph.backup.rm_snap(image, snaps[0])

				if len(ceph.backup.snap(image)) == 0:
					Log.debug('%s has no snapshot left, deleting' % (image,))
					ceph.backup.rm(image)
			Log.debug('releasing lock %s' % (filename,))
		except filelock.Timeout as e:
			Log.debug(e)


class BackupProxmox(Backup):
	def __init__(self, cluster, queue, status_queue):
		super().__init__(cluster, queue, status_queue)

	def __fetch_profiles(self, vm, disk):
		profiles = list(config['profiles'].items())

		if config['profiles_api'] is None:
			return profiles

		try:
			json = {
				'cluster': {
					'type': 'proxmox',
					'name': self.cluster['name'],
					'fqdn': self.cluster['fqdn'],
				},
				'vm': {
					'vmid': vm['vmid'],
					'name': vm['name'],
				},
				'disk': disk,
			}

			add = requests.post(config['profiles_api'], json=json)
			add.raise_for_status()
			add = add.json()

			if 'backup' in add and add['backup'] is False:
				return list()

			if 'profiles' in add:
				profiles += list(add['profiles'].items())

		except Exception as e:
			Log.warning('Exception thrown while fetching profiles for %s : %s' % (vm, e))
		return profiles

	def list(self):
		px = Proxmox(self.cluster)
		vms = px.vms()

		result = list()
		for vm in vms:
			if vm['smbios'] is None and self.cluster['use_smbios'] is True:
				if config['uuid_fallback'] is False:
					Log.warning('No smbios found, skipping')
					continue
			result.append(vm)
		return result

	def create_snap(self, vm):
		px = Proxmox(self.cluster)
		try:
			# We freeze the VM once, thus create all snaps at the same time
			# Exports are done after thawing, because it it time-consuming,
			# and we must not keep the VM frozen more than necessary
			px.freeze(vm['node'], vm)

			for disk in vm['to_backup']:
				ceph = px.ceph_storage[disk['ceph']]
				bck = Bck(disk['ceph'], ceph, disk['rbd'], vm=vm, adapter=disk['adapter'])
				profiles = self.__fetch_profiles(vm, disk)
				self._create_snap(bck, profiles)
		except Exception as e:
			Log.warning('%s thrown while freezing %s' % (e, vm))

		try:
			px.thaw(vm['node'], vm)
		except Exception as e:
			Log.warning('%s thrown while thawing %s' % (e, vm))

	def expire_item(self, vm):
		px = Proxmox(self.cluster)
		try:
			for disk in vm['to_backup']:
				ceph = px.ceph_storage[disk['ceph']]
				bck = Bck(disk['ceph'], ceph, disk['rbd'], vm=vm, adapter=disk['adapter'])
				filename = bck.build_dest().replace('/', '')
				filename = '%s/%s' % (config['lockdir'], filename)
				Log.debug('locking %s' % (filename,))
				lock = filelock.FileLock(filename, timeout=0)

				with lock:
					self._expire_item(ceph, disk, vm)
				Log.debug('releasing lock %s' % (filename,))
		except filelock.Timeout as e:
			Log.debug(e)
		except Exception as e:
			Log.warning('Expire_live on %s : %s' % (vm, e))


class BackupPlain(Backup):
	def __init__(self, cluster, queue, status_queue):
		super().__init__(cluster, queue, status_queue)
		self.ceph = Ceph(self.cluster['pool'], endpoint=self.cluster['fqdn'])

	def list(self):
		return self.ceph.ls()

	def create_snap(self, rbd):
		bck = Bck(self.cluster['name'], self.ceph, rbd)
		self._create_snap(bck, config['profiles'].items())

	def expire_item(self, item):
		try:
			self._expire_item(self.ceph, item)
		except Exception as e:
			Log.warning('Expire_live on %s : %s' % (item, e))


class Status_updater:
	class Real_updater:
		def __init__(self, status_queue, desc):
			self.todo = 0
			self.total = 0
			self.status_queue = status_queue
			self.desc = desc

			if config['log_level'] != 'debug':
				# progressbar uses signal.SIGWINCH
				# It messes with multiprocessing, so we break it
				import signal
				signal.signal = None
				widget = [progressbar.widgets.SimpleProgress(), ' ', desc, ' (', progressbar.widgets.Timer(), ')']
				self.bar = progressbar.ProgressBar(maxval=1, widgets=widget)

		def __call__(self):
			Log.debug('Real_updater started')
			if config['log_level'] != 'debug':
				self.bar.start()
			try:
				self.__work__()
			except Exception as e:
				Log.error(e)
			if config['log_level'] != 'debug':
				self.bar.finish()
			Log.debug('Real_updater ended')

		def __work__(self):
			while True:
				msg = self.status_queue.get()
				if msg == 'add_item':
					self.total += 1
					self.todo += 1
				elif msg == 'done_item':
					self.todo -= 1
				else:
					Log.error('Unknown message received: %s' % (msg,))
				done = self.total - self.todo
				msg = f'Backurne : %s/%s {self.desc}' % (done, self.total)
				setproctitle.setproctitle(msg)

				if config['log_level'] != 'debug':
					self.bar.maxval = self.total
					self.bar.update(done)

	def __init__(self, manager, desc):
		self.status_queue = manager.Queue()
		self.desc = desc

	def __enter__(self):
		target = Status_updater.Real_updater(self.status_queue, self.desc)
		self.real_updater = multiprocessing.Process(target=target)
		atexit.register(self.real_updater.terminate)
		self.real_updater.start()
		return self.status_queue


	def __exit__(self, type, value, traceback):
		self.real_updater.terminate()


class Producer:
	def __init__(self, queue, status_queue):
		self.queue = queue
		self.status_queue = status_queue

	def __call__(self):
		Log.debug('Producer started')
		try:
			self.__work__()
		except Exception as e:
			Log.error(e)

		# We send one None per live_worker
		# That way, all of them shall die
		for i in range(0, config['live_worker']):
			try:
				self.queue.put(None)
			except:
				Log.error('cannot end a live_worker! This is a critical bug, we will never die')

		Log.debug('Producer ended')

	def __work__(self):
		for cluster in config['live_clusters']:
			Log.debug('Backuping %s: %s' % (cluster['type'], cluster['name']))
			if cluster['type'] == 'proxmox':
				bidule = BackupProxmox(cluster, self.queue, self.status_queue)
			else:
				bidule = BackupPlain(cluster, self.queue, self.status_queue)
			bidule.create_snaps()


class Consumer:
	def __init__(self, queue, status_queue):
		self.queue = queue
		self.status_queue = status_queue

	def __call__(self):
		Log.debug('Consumer started')
		try:
			self.__work__()
		except Exception as e:
			Log.error(e)
		Log.debug('Consumer ended')

	def __work__(self):
		while True:
			snaps = self.queue.get()
			if snaps is None:
				break

			filename = snaps[0]['dest'].replace('/', '')
			filename = '%s/%s' % (config['lockdir'], filename)
			lock = filelock.FileLock(filename, timeout=0)
			try:
				Log.debug('locking %s' % (filename,))
				with lock:
					for snap in snaps:
						backup = snap['backup']
						backup.dl_snap(snap['snap_name'], snap['dest'], snap['last_snap'])
			except filelock.Timeout as e:
				Log.debug(e)
			except Exception as e:
				Log.error(e)
			Log.debug('releasing lock %s' % (filename,))
			self.status_queue.put('done_item')


def get_sqlite():
	sql = sqlite3.connect(config['check_db'], isolation_level=None)
	sql.execute('create table if not exists results (date text, cluster text, disk text, msg text)')
	return sql


def print_check_results():
	sql = get_sqlite()

	failed = sql.execute('select * from results where date < strftime("%s", "now") - 7200')
	failed = [i for i in failed]

	if len(failed) > 0:
		print('Error: %s failed backups found' % (len(failed),))
		for err in failed:
			print(err[3])
		exit(2)

	print('OK: all things are backed up!')
	exit(0)


def update_check_results(check_results):
	sql = get_sqlite()

	failed_db = [i for i in sql.execute('select date, cluster, disk from results')]
	for i in failed_db:
		found = False
		for j in check_results:
			if i[1] != j['cluster']:
				continue
			if i[2] != j['image']:
				continue
			found = True
			break
		if found is False:
			sql.execute('delete from results where cluster = ? and disk = ?', (i[1], i[2]))

	for i in check_results:
		found = False
		for j in failed_db:
			if j[1] != i['cluster']:
				continue
			if j[2] != i['image']:
				continue
			found = True
			break
		if found is False:
			sql.execute('insert into results values(strftime("%s", "now"), ?, ?, ?)', (i['cluster'], i['image'], i['msg']))


def main():
	if len(sys.argv) < 2:
		pretty.usage()

	try:
		action = sys.argv[1]
	except IndexError:
		Log.warning('nothing to do')
		exit(1)

	if '--json' in sys.argv:
		json_format = True
		sys.argv.remove('--json')
	else:
		json_format = False

	if action not in ('backup', 'precheck', 'check', 'check-snap', 'ls', 'list-mapped',
			'map', 'unmap'):
		Log.error('Action %s unknown' % (action,))
		pretty.usage()

	if action == 'check':
		print_check_results()

	if action in ('precheck', 'check-snap'):
		result = list()

		for cluster in config['live_clusters']:
			Log.info('Checking %s: %s' % (cluster['type'], cluster['name']))
			if cluster['type'] == 'proxmox':
				check = CheckProxmox(cluster)
			else:
				check = CheckPlain(cluster)
			if action == 'precheck':
				ret = check.check()
			else:
				ret = check.check_snap()
			result += ret

		update_check_results(result)
		print_check_results()

	if action == 'backup':
		Log.debug('Starting backup ..')

		manager = multiprocessing.Manager()
		atexit.register(manager.shutdown)
		queue = manager.Queue()

		with Status_updater(manager, 'images processed') as status_queue:
			producer = multiprocessing.Process(target=Producer(queue, status_queue))
			atexit.register(producer.terminate)
			producer.start()

			live_workers = list()
			for i in range(0, config['live_worker']):
				pid = multiprocessing.Process(target=Consumer(queue, status_queue))
				atexit.register(pid.terminate)
				live_workers.append(pid)
				pid.start()

			# Workers will exit upon a None reception
			# When all of them are done, we are done
			for pid in live_workers:
				pid.join()


		with Status_updater(manager, 'images cleaned up on live clusters') as status_queue:
			for cluster in config['live_clusters']:
				Log.debug('Expire snapshots from live %s: %s' % (cluster['type'], cluster['name']))
				if cluster['type'] == 'proxmox':
					bidule = BackupProxmox(cluster, None, status_queue)
				else:
					bidule = BackupPlain(cluster, None, status_queue)
				bidule.expire_live()

		Log.debug('Expiring our snapshots')
		# Dummy Ceph object used to retrieve the real backup Object
		ceph = Ceph(None)

		with Status_updater(manager, 'images cleaned up on backup cluster') as status_queue:
			data = list()
			for i in ceph.backup.ls():
				data.append({'ceph': ceph, 'image': i, 'status_queue': status_queue})
				status_queue.put('add_item')
			with multiprocessing.Pool(config['backup_worker']) as pool:
				for i in pool.imap_unordered(Backup.expire_backup, data):
					pass

		manager.shutdown()
		exit(0)

	try:
		rbd = sys.argv[2]
	except:
		rbd = None

	try:
		snap = sys.argv[3]
	except:
		snap = None

	restore = Restore(rbd, snap)
	if action == 'ls':
		data = restore.ls()
		if rbd is None:
			pt = pretty.Pt(['Ident', 'Disk', 'UUID'])

			for i in data:
				row = [i['ident'], i['disk'], i['uuid']]
				pt.add_row(row)
		else:
			pt = pretty.Pt(['Creation date', 'UUID'])

			for i in data:
				row = [i['creation'], i['uuid']]
				pt.add_row(row)

		if json_format is True:
			print(json.dumps(data, default=str))
		else:
			print(pt)
	elif action == 'list-mapped':
		data = restore.list_mapped()
		pt = pretty.Pt(['rbd', 'snap', 'mount'])
		for i in data:
			pt.add_row([i['parent_image'], i['parent_snap'], i['mountpoint']])

		if json_format is True:
			print(json.dumps(data))
		else:
			print(pt)
	elif action == 'map':
		if rbd is None or snap is None:
			pretty.usage()
		restore.mount()
	elif action == 'unmap':
		if rbd is None or snap is None:
			pretty.usage()
		restore.umount()


if __name__ == '__main__':
	main()
