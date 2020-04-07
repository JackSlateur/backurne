import datetime
import dateutil.parser

from .config import config
from .log import log as Log


class Bck():
	def __init__(self, name, ceph, rbd, vm=None, adapter=None):
		self.name = name
		self.ceph = ceph
		self.rbd = rbd
		self.vm = vm
		self.adapter = adapter

		self.source = f'{self.name}:{self.rbd}'

		self.dest = self.__build_dest()

		# Store here the last snapshot created via this object
		# It is not yet on the backup cluster
		self.last_created_snap = None

	def __str__(self):
		if self.mv is not None:
			return '%s/%s' % (self.vm['name'], self.rbd)
		else:
			return '%s/%s' % (self.name, self.rbd)

	def __build_dest(self):
		ident = self.name
		comment = None

		if self.vm is not None:
			comment = self.vm['name']
			if self.vm['px'].px_config['use_smbios']:
				if self.vm['smbios'] is not None:
					ident = self.vm['smbios']
					dest = f'{ident};{self.adapter};{comment}'
					return dest

		dest = f'{ident};{self.rbd};{comment}'
		return dest

	def __snap_name(self, profile, value):
		name = f'{profile};{value}'
		Log.debug(f'Processing {self.source} ({name})')
		name = f'{config["snap_prefix"]};{name}'
		return name

	def __last_snap_profile(self, profile):
		snaps = self.ceph.backup.snap(self.dest)
		good = list()
		for snap in snaps:
			split = snap.split(';')
			if split[1] != profile:
				continue
			good.append(snap)
		return self.ceph.get_last_snap(good)

	def dl_snap(self, snap_name, dest, last_snap):
		Log.debug(f'Exporting {self.source} {snap_name}')
		if not self.ceph.backup.exists(dest):
			# Create a dummy image, on our backup cluster,
			# which will receive a full snapshot
			self.ceph.backup('create', dest, '-s', '1')

		self.ceph.do_backup(self.rbd, snap_name, dest, last_snap)
		Log.debug(f'Export {self.source} {snap_name} complete')

	def check_profile(self, profile):
		try:
			last_profile = self.__last_snap_profile(profile)
		except Exception:
			# Image does not exists ?
			return True

		if profile == 'daily':
			delta = datetime.timedelta(days=1)
		else:
			delta = datetime.timedelta(hours=1)
		not_after = datetime.datetime.now() - delta
		if last_profile is not None:
			last_time = last_profile.split(';')[3]
			last_time = dateutil.parser.parse(last_time)
			if last_time > not_after:
				Log.debug('Our last backup is still young, nothing to do')
				return False
		return True

	def make_snap(self, profile, value):
		dest = self.dest
		self.snap_name = self.__snap_name(profile, value)

		self.ceph.backup.update_desc(self.source, dest)

		last_snap = None
		if self.last_created_snap is not None:
			last_snap = self.last_created_snap
		elif len(self.ceph.snap(self.rbd)) == 0:
			Log.debug(f'No snaps found on {self.source}')
		elif not self.ceph.backup.exists(dest):
			Log.debug(f'backup:{dest} does not exist')
		elif len(self.ceph.backup.snap(dest)) == 0:
			Log.debug(f'No snaps found for backup:{dest}')
		else:
			last_snap = self.ceph.get_last_shared_snap(self.rbd, dest)

		if last_snap is None:
			Log.debug(f'{self.source}: doing full backup')
		else:
			Log.debug(f'{self.source}: doing incremental backup based on {last_snap}')

		now = datetime.datetime.now().isoformat()
		snap_name = f'{self.snap_name};{now}'
		self.last_created_snap = snap_name

		self.ceph.mk_snap(self.rbd, snap_name, self.vm)

		return dest, last_snap, snap_name
