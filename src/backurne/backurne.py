import argparse
import atexit
import datetime
import dateutil.parser
import filelock
import json
import multiprocessing
import progressbar
import requests
import setproctitle
import sh
import signal
import sqlite3
import time
import queue
from functools import wraps

from . import pretty
from .config import config
from .log import log as Log
from .ceph import Ceph
from .proxmox import Proxmox
from .restore import Restore
from .backup import Bck
from .disk import print_mapped, prepare_tree_to_json, get_mapped
from . import stats


VERSION = '2.2.1'


def handle_exc(func):
	@wraps(func)
	def wrapper(*args, **kwargs):
		try:
			return func(*args, **kwargs)
		except filelock.Timeout as e:
			Log.debug(e)
		except Exception as e:
			Log.warning(f'{e} thrown while running {func.__name__}()')
	return wrapper


class Check:
	def __init__(self, cluster):
		self.cluster = cluster
		self.err = list()

	def add_err(self, msg):
		if msg is None:
			return
		msg['cluster'] = self.cluster['name']
		self.err.append(msg)

	@handle_exc
	def check_img(self, args):
		ceph = args['ceph']
		backup = args['backup']
		rbd = args['image']

		if not ceph.backup.exists(backup.dest):
			msg = f'No backup found for {backup} at {ceph} (image does not exists)'
			return {'image': rbd, 'msg': msg}

		last = ceph.get_last_shared_snap(rbd, backup.dest)
		if last is None:
			msg = f'No backup found for {backup} at {ceph} (no shared snap)'
			return {'image': rbd, 'msg': msg}

		when = last.split(';')[3]
		when = dateutil.parser.parse(when)
		deadline = datetime.timedelta(days=1) + datetime.timedelta(hours=6)
		deadline = datetime.datetime.now() - deadline
		if when < deadline:
			msg = f'Backup found for {backup} at {ceph}, yet too old (created at {when})'
			return {'image': rbd, 'msg': msg}

		snaps = ceph.backup.snap(backup.dest)
		for snap in snaps:
			if not Backup.is_expired(snap):
				continue
			msg = f'Snapshot {backup.dest} / {snap} was not deleted in time, please investigate (may be protected or mapped).'
			return {'image': rbd, 'msg': msg}

	def cmp_snap(self, backup, ceph, rbd):
		live_snaps = ceph.snap(rbd)
		try:
			backup_snaps = ceph.backup.snap(backup.dest)
		except Exception:
			backup_snaps = []
		inter = list(set(live_snaps).intersection(backup_snaps))
		for snap in inter:
			Log.debug(f'checking {rbd} @ {snap}')
			live = ceph.checksum(rbd, snap)
			back = ceph.backup.checksum(backup.dest, snap)
			if live == back:
				continue

			err = {
				'image': rbd,
				'msg': f'ERR: shared snapshot {snap} does not match\n\tOn live (image: {rbd}): {live}\n\tOn backup (image: {backup.dest}): {back}'
			}
			self.add_err(err)


class CheckProxmox(Check):
	def __init__(self, cluster):
		super().__init__(cluster)
		self.px = Proxmox(cluster)

	def check(self):
		data = list()
		for vm in self.px.vms():
			for disk, ceph, bck in vm['to_backup']:
				data.append({'ceph': ceph, 'backup': bck, 'image': disk['rbd']})

		self.err = list()
		with multiprocessing.Pool() as pool:
			for msg in pool.imap_unordered(self.check_img, data):
				self.add_err(msg)

		return self.err

	def check_snap(self):
		for vm in self.px.vms():
			for disk, ceph, bck in vm['to_backup']:
				self.cmp_snap(bck, ceph, disk['rbd'])
		return self.err


class CheckPlain(Check):
	def __init__(self, cluster):
		super().__init__(cluster)
		self.ceph = Ceph(self.cluster['pool'], endpoint=self.cluster['fqdn'], cluster_conf=self.cluster)

	def check(self):
		data = list()
		for rbd in self.ceph.ls():
			bck = Bck(self.cluster['name'], self.ceph, rbd)
			data.append({'ceph': self.ceph, 'backup': bck, 'image': rbd})

		self.err = list()
		with multiprocessing.Pool() as pool:
			for msg in pool.imap_unordered(self.check_img, data):
				self.add_err(msg)

		return self.err

	def check_snap(self):
		for rbd in self.ceph.ls():
			bck = Bck(self.cluster['name'], self.ceph, rbd)
			self.cmp_snap(bck, self.ceph, rbd)
		return self.err


def run_hook(kind, vmname, diskname):
	if config['hooks'][kind] is not None:
		sh.Command(config['hooks'][kind])(kind, vmname, diskname)


class Backup:
	def __init__(self, cluster, regular_queue, priority_queue, status_queue, args=None):
		self.cluster = cluster
		self.regular_queue = regular_queue
		self.priority_queue = priority_queue
		self.status_queue = status_queue
		self.args = args

	def is_expired(snap, last=False):
		splited = snap.split(';')
		created_at = dateutil.parser.parse(splited[-1])
		profile = splited[-3]
		value = int(splited[-2])
		if profile == 'daily':
			expiration = datetime.timedelta(days=value)
		elif profile == 'hourly':
			expiration = datetime.timedelta(hours=value)
		elif profile == 'weekly':
			expiration = datetime.timedelta(days=7 * value)
		elif profile == 'monthly':
			expiration = datetime.timedelta(days=30 * value)
		else:
			Log.warning(f'Unknown profile found, no action taken: {profile}')
			return False

		expired_at = created_at + expiration
		if last is True:
			expired_at += datetime.timedelta(days=config['extra_retention_time'])

		now = datetime.datetime.now()
		if expired_at > now:
			return False
		return True

	def _create_snap(self, bck, profiles, pre_vm_hook):
		todo = list()
		is_high_prio = False

		hooked = False

		try:
			with Lock(bck.dest):
				for profile, value in profiles:
					self.status_queue.put('add_item')
					if not self.args.force and not bck.check_profile(profile):
						self.status_queue.put('done_item')
						continue

					if pre_vm_hook is False:
						try:
							run_hook('pre_vm', bck.vm['name'], bck.rbd)
						except Exception as e:
							out = e.stdout.decode('utf-8') + e.stderr.decode('utf-8').rstrip()
							Log.warn('pre_vm hook failed on %s/%s with code %s : %s' % (bck.vm['name'], bck.rbd, e.exit_code, out))
							self.status_queue.put('done_item')
							return None
						hooked = True

					try:
						if bck.vm is not None:
							run_hook('pre_disk', bck.vm['name'], bck.rbd)
						else:
							run_hook('pre_disk', bck.source, bck.rbd)
					except Exception as e:
						out = e.stdout.decode('utf-8') + e.stderr.decode('utf-8').rstrip()
						Log.warn('pre_disk hook failed on %s/%s with code %s : %s' % (bck.vm['name'], bck.rbd, e.exit_code, out))
						self.status_queue.put('done_item')
						continue
					setproctitle.setproctitle(f'Backurne: snapshooting {bck.rbd} on {bck.name}')
					dest, last_snap, snap_name = bck.make_snap(profile, value['count'])

					try:
						run_hook('post_disk', bck.vm['name'], bck.rbd)
					except Exception:
						pass

					if dest is not None:
						todo.append({
							'dest': dest,
							'last_snap': last_snap,
							'snap_name': snap_name,
							'backup': bck,
						})

						priority = value.get('priority')
						if priority == 'high':
							is_high_prio = True
		except filelock.Timeout:
			Log.info(f'unable to acquire lock for {bck.vm["name"]}')
			pass
		if len(todo) != 0:
			if is_high_prio:
				self.priority_queue.put(todo)
			else:
				self.regular_queue.put(todo)
		setproctitle.setproctitle('Backurne idle producer')
		return hooked

	def create_snaps(self):
		items = self.list()
		with multiprocessing.Pool(config['live_worker']) as pool:
			for i in pool.imap_unordered(self.create_snap, items):
				pass

	def _custom_key(self, item):
		return item.split(';')[3]

	def _expire_item(self, ceph, disk, vm=None):
		self.status_queue.put('add_item')
		self.status_queue.put('done_item')

		if vm is not None:
			bck = Bck(disk['ceph'], ceph, disk['rbd'], vm=vm, adapter=disk['adapter'])
			rbd = disk['rbd']
		else:
			bck = Bck(self.cluster['name'], ceph, disk)
			rbd = disk

		backups = Ceph(None).snap(bck.dest)

		snaps = ceph.snap(rbd)
		shared = list(set(backups).intersection(snaps))

		try:
			shared.sort(key=self._custom_key)
			shared = shared.pop()
		except IndexError:
			shared = None

		by_profile = {}
		for snap in snaps:
			# The last shared snapshot must be kept
			# Also, subsequent snaps shall be kept as well,
			# because a backup may be pending elsewhere
			if shared is None or snap.split(';')[3] >= shared.split(';')[3]:
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

	@handle_exc
	def expire_backup(i):
		ceph = i['ceph']
		image = i['image']
		i['status_queue'].put('done_item')

		with Lock(image):
			snaps = ceph.snap(image)
			try:
				# Pop the last snapshot
				# We will take care of it later
				last = snaps.pop()
			except IndexError:
				# We found an image without snapshot
				# Someone is messing around, or this is a bug
				# Anyway, the image can be deleted
				ceph.rm(image)
				return

			for snap in snaps:
				if not Backup.is_expired(snap):
					continue
				ceph.rm_snap(image, snap)

			snaps = ceph.snap(image)
			if len(snaps) == 1:
				if Backup.is_expired(last, last=True):
					ceph.rm_snap(image, snaps[0])

			if len(ceph.snap(image)) == 0:
				Log.debug(f'{image} has no snapshot left, deleting')
				ceph.rm(image)


class BackupProxmox(Backup):
	def __init__(self, cluster, regular_queue, priority_queue, status_queue, args):
		super().__init__(cluster, regular_queue, priority_queue, status_queue, args)

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
			Log.warning(f'{e} thrown while fetching profiles for {vm}')
		return profiles

	def list(self):
		result = list()

		try:
			px = Proxmox(self.cluster)
			for vm in px.vms():
				if vm['smbios'] is None and self.cluster['use_smbios'] is True:
					if config['uuid_fallback'] is False:
						Log.warning('No smbios found, skipping')
						continue
				result.append(vm)
		except Exception as e:
			Log.error(f'{e} thrown while listing vm on {self.cluster["name"]}')
		return result

	def filter_profiles(self, profiles, _filter):
		if _filter is None:
			return profiles

		result = list()
		for profile in profiles:
			if profile[0] == _filter:
				result.append(profile)
			else:
				Log.debug(f'Skipping profile {profile[0]}, due to --profile')
		return result

	@handle_exc
	def create_snap(self, vm):
		setproctitle.setproctitle('Backurne idle producer')

		if self.args.vmid is not None:
			if vm['vmid'] != self.args.vmid:
				Log.debug(f'Skipping VM {vm["vmid"]}, due to --vmid')
				return

		px = Proxmox(self.cluster)
		# We freeze the VM once, thus create all snaps at the same time
		# Exports are done after thawing, because it it time-consuming,
		# and we must not keep the VM frozen more than necessary
		px.freeze(vm['node'], vm)

		pre_vm_hook = False

		for disk, ceph, bck in vm['to_backup']:
			profiles = self.__fetch_profiles(vm, disk)
			profiles = self.filter_profiles(profiles, self.args.profile)
			hooked = self._create_snap(bck, profiles, pre_vm_hook)
			if hooked is None:
				# pre_vm hook failed, we skip all its disks
				break

			if hooked is True:
				pre_vm_hook = True

		if pre_vm_hook is True:
			run_hook('post_vm', bck.vm['name'], bck.rbd)

		px.thaw(vm['node'], vm)

	@handle_exc
	def expire_item(self, vm):
		for disk, ceph, bck in vm['to_backup']:
			if self.args.vmid is not None:
				if vm['vmid'] != self.args.vmid:
					Log.debug(f'Skipping VM {vm["vmid"]}, due to --vmid')
					return

			with Lock(bck.dest):
				self._expire_item(ceph, disk, vm)


class BackupPlain(Backup):
	def __init__(self, cluster, regular_queue, priority_queue, status_queue, args):
		super().__init__(cluster, regular_queue, priority_queue, status_queue, args)
		self.ceph = Ceph(self.cluster['pool'], endpoint=self.cluster['fqdn'], cluster_conf=self.cluster)

	def list(self):
		try:
			return self.ceph.ls()
		except Exception as e:
			Log.warning(e)
			return []

	@handle_exc
	def create_snap(self, rbd):
		setproctitle.setproctitle('Backurne idle producer')
		bck = Bck(self.cluster['name'], self.ceph, rbd)
		self._create_snap(bck, config['profiles'].items(), True)

	@handle_exc
	def expire_item(self, rbd):
		bck = Bck(self.cluster['name'], self.ceph, rbd)
		with Lock(bck.dest):
			self._expire_item(self.ceph, rbd)


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
				real_signal = signal.signal
				signal.signal = None
				widget = [progressbar.widgets.SimpleProgress(), ' ', desc, ' (', progressbar.widgets.Timer(), ')']
				self.bar = progressbar.ProgressBar(maxval=1, widgets=widget)
				signal.signal = real_signal

		@handle_exc
		def __call__(self):
			Log.debug('Real_updater started')
			if config['log_level'] != 'debug':
				self.bar.start()
			self.__work__()
			if config['log_level'] != 'debug':
				self.bar.finish()
			Log.debug('Real_updater ended')

		def __update(self):
			done = self.total - self.todo
			msg = f'Backurne : {done}/{self.total} {self.desc}'
			setproctitle.setproctitle(msg)
			if config['log_level'] != 'debug':
				self.bar.maxval = self.total
				self.bar.update(done)

		def __work__(self):
			while True:
				try:
					msg = self.status_queue.get(block=False)
				except queue.Empty:
					self.__update()
					time.sleep(1)
					continue
				if msg == 'add_item':
					self.total += 1
					self.todo += 1
				elif msg == 'done_item':
					self.todo -= 1
				else:
					Log.error(f'Unknown message received: {msg}')
				self.__update()

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
		print('')


class Lock:
	def __init__(self, path):
		path = path.replace('/', '')
		self.path = f'{config["lockdir"]}/{path}'
		self.lock = filelock.FileLock(self.path, timeout=0)

	def __enter__(self):
		Log.debug(f'locking {self.path}')
		self.lock.acquire()

	def __exit__(self, type, value, traceback):
		Log.debug(f'releasing lock {self.path}')
		self.lock.release()


class Producer:
	def __init__(self, params, args):
		self.cluster = params['cluster']
		self.regular_queue = params['regular_q']
		self.priority_queue = params['priority_q']
		self.status_queue = params['status_q']
		self.args = args

	@handle_exc
	def __call__(self):
		Log.debug('Producer started')
		setproctitle.setproctitle('Backurne Producer')
		self.__work__()
		# We send one None per live_worker
		# That way, all of them shall die
		for i in range(0, config['live_worker']):
			try:
				self.regular_queue.put(None)
				self.priority_queue.put(None)
			except:
				Log.error('cannot end a live_worker! This is a critical bug, we will never die')

		Log.debug('Producer ended')

	@handle_exc
	def __work__(self):
		for cluster in config['live_clusters']:
			if self.args.cluster is not None:
				if cluster['name'] != self.args.cluster:
					Log.debug(f'Skipping cluster {cluster["name"]} due to --cluster')
					continue
			Log.debug(f'Backuping {cluster["type"]}: {cluster["name"]}')
			if cluster['type'] == 'proxmox':
				bidule = BackupProxmox(cluster, self.regular_queue, self.priority_queue, self.status_queue, self.args)
			else:
				bidule = BackupPlain(cluster, self.regular_queue, self.priority_queue, self.status_queue, self.args)
			bidule.create_snaps()


class Consumer:
	def __init__(self, params):
		self.cluster = params['cluster']
		self.regular_queue = params['regular_q']
		self.priority_queue = params['priority_q']
		self.status_queue = params['status_q']

		# Track the queue status
		# When both are dead, the worker can die in peace
		self.priority_alive = True
		self.regular_alive = True

	@handle_exc
	def __call__(self):
		Log.debug('Consumer started')
		setproctitle.setproctitle('Backurne Consumer')
		self.__work__()
		Log.debug('Consumer ended')

	def __work__(self):
		while True:
			setproctitle.setproctitle(f'Backurne idle consumer ({self.cluster["name"]})')

			if self.priority_alive is False and self.regular_alive is False:
				break

			snaps = []
			if self.priority_alive is True:
				try:
					snaps = self.priority_queue.get_nowait()
				except queue.Empty:
					pass

				if snaps is None:
					self.priority_alive = False
					continue

			if len(snaps) == 0 and self.regular_alive is True:
				try:
					snaps = self.regular_queue.get_nowait()
				except queue.Empty:
					pass

				if snaps is None:
					self.regular_alive = False
					continue

			if len(snaps) == 0:
				time.sleep(1)
				continue

			try:
				with Lock(snaps[0]['dest']):
					for snap in snaps:
						setproctitle.setproctitle(f'Backurne: fetching {snap["backup"].source} ({snap["snap_name"]})')
						backup = snap['backup']
						backup.dl_snap(snap['snap_name'], snap['dest'], snap['last_snap'])
			except filelock.Timeout:
				pass
			except Exception as e:
				Log.error(e)
			self.status_queue.put('done_item')
			setproctitle.setproctitle('Backurne idle consumer')


def get_sqlite():
	sql = sqlite3.connect(config['check_db'], isolation_level=None)
	sql.execute('create table if not exists results (date text, cluster text, disk text, msg text)')
	return sql


def print_check_results():
	sql = get_sqlite()

	failed = sql.execute('select * from results where date < strftime("%s", "now") - 7200')
	failed = [i for i in failed]

	if len(failed) > 0:
		print(f'Error: {len(failed)} failed backups found')
		for err in failed:
			print(f'{err[1]} : {err[3]}')
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


def get_args():
	parser = argparse.ArgumentParser()
	parser.add_argument('--debug', action='store_true')

	sub = parser.add_subparsers(dest='action', required=True)
	back = sub.add_parser('backup')
	back.add_argument('--cluster', dest='cluster', nargs='?')
	back.add_argument('--vmid', dest='vmid', nargs='?', type=int)
	back.add_argument('--profile', dest='profile', nargs='?')
	back.add_argument('--force', action='store_true')
	back.add_argument('--no-cleanup', action='store_true')
	back.add_argument('--cleanup', action='store_true')

	sub.add_parser('precheck')
	sub.add_parser('check')
	sub.add_parser('check-snap')
	sub.add_parser('stats')
	sub.add_parser('version')

	ls = sub.add_parser('list-mapped')
	ls.add_argument('--json', action='store_true')

	ls = sub.add_parser('ls')
	ls.add_argument(dest='rbd', nargs='?')
	ls.add_argument('--json', action='store_true')

	_map = sub.add_parser('map')
	_map.add_argument(dest='rbd')
	_map.add_argument(dest='snapshot')
	_map.add_argument(dest='vmdk', nargs='?')

	unmap = sub.add_parser('unmap')
	unmap.add_argument(dest='rbd')
	unmap.add_argument(dest='snapshot')
	return parser.parse_args()


def main():
	args = get_args()

	if args.debug:
		import logging
		Log.setLevel(logging.DEBUG)

	if args.action == 'stats':
		stats.print_stats()
	elif args.action == 'version':
		print(f'Backurne version {VERSION}')
	elif args.action == 'check':
		print_check_results()
	elif args.action in ('precheck', 'check-snap'):
		result = list()

		for cluster in config['live_clusters']:
			Log.info(f'Checking {cluster["type"]}: {cluster["name"]}')
			if cluster['type'] == 'proxmox':
				check = CheckProxmox(cluster)
			else:
				check = CheckPlain(cluster)
			if args.action == 'precheck':
				ret = check.check()
			else:
				ret = check.check_snap()
			result += ret

		update_check_results(result)
		print_check_results()
	elif args.action == 'backup':
		if args.vmid is not None and args.cluster is None:
			Log.error(f'--vmid has no meaning without --cluster')
			exit(1)

		manager = multiprocessing.Manager()
		atexit.register(manager.shutdown)

		live_workers = list()

		with Status_updater(manager, 'images processed') as status_queue:
			for cluster in config['live_clusters']:
				params = {
					'cluster': cluster,
					'regular_q': manager.Queue(),
					'priority_q': manager.Queue(),
					'status_q': status_queue,
				}

				producer = multiprocessing.Process(target=Producer(params, args))
				atexit.register(producer.terminate)
				producer.start()

				for i in range(0, config['live_worker']):
					pid = multiprocessing.Process(target=Consumer(params))
					atexit.register(pid.terminate)
					live_workers.append(pid)
					pid.start()

			# Workers will exit upon a None reception
			# When all of them are done, we are done
			for pid in live_workers:
				pid.join()

		if args.no_cleanup is True:
			Log.debug('not cleaning up as --no-cleanup is used')
			exit(0)

		with Status_updater(manager, 'images cleaned up on live clusters') as status_queue:
			for cluster in config['live_clusters']:
				if args.cluster is not None:
					if cluster['name'] != args.cluster:
						Log.debug(f'Skipping cluster {cluster["name"]} due to --cluster')
						continue

				Log.debug(f'Expire snapshots from live {cluster["type"]}: {cluster["name"]}')
				if cluster['type'] == 'proxmox':
					bidule = BackupProxmox(cluster, None, None, status_queue, args)
				else:
					bidule = BackupPlain(cluster, None, None, status_queue, args)
				bidule.expire_live()

		if args.cleanup or args.cluster is None and args.profile is None and args.vmid is None:
			Log.debug('Expiring our snapshots')
			# Dummy Ceph object used to retrieve the real backup Object
			ceph = Ceph(None)

			with Status_updater(manager, 'images cleaned up on backup cluster') as status_queue:
				data = list()
				for i in ceph.ls():
					data.append({'ceph': ceph, 'image': i, 'status_queue': status_queue})
					status_queue.put('add_item')
				with multiprocessing.Pool(config['backup_worker']) as pool:
					for i in pool.imap_unordered(Backup.expire_backup, data):
						pass

		manager.shutdown()
	elif args.action == 'ls':
		restore = Restore(args.rbd, None)
		data = restore.ls()
		if args.rbd is None:
			pt = pretty.Pt(['Ident', 'Disk', 'UUID'])

			for i in data:
				row = [i['ident'], i['disk'], i['uuid']]
				pt.add_row(row)
		else:
			pt = pretty.Pt(['Creation date', 'UUID'])

			for i in data:
				row = [i['creation'], i['uuid']]
				pt.add_row(row)

		if args.json is True:
			print(json.dumps(data, default=str))
		else:
			print(pt)
	elif args.action == 'list-mapped':
		data = get_mapped(extended=False)
		if args.json is True:
			result = []
			for tree in data:
				result.append(prepare_tree_to_json(tree))
			print(json.dumps(result))
		else:
			print_mapped(data)
	elif args.action == 'map':
		Restore(args.rbd, args.snapshot, args.vmdk).mount()
		for i in get_mapped(extended=False):
			if i.name.parent_image != args.rbd or i.name.parent_snap != args.snapshot:
				continue
			print_mapped([i, ])
			return

	elif args.action == 'unmap':
		restore = Restore(args.rbd, args.snapshot)
		restore.umount()


if __name__ == '__main__':
	main()
