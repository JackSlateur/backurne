import json

from sh import tc


class Tc:
	def __init__(self, iface, cluster):
		self.iface = iface
		self.minor = cluster['tc_minor']
		self.bwlimit = cluster['bwlimit']

	def setup(self):
		if not self.qdisc_exists():
			self.create()
			return

	def fetch_qdisc(self):
		qdisc = tc('-j', 'qdisc', 'ls').stdout.decode('utf-8')
		qdisc = json.loads(qdisc)
		return qdisc

	def qdisc_exists(self):
		qdisc = self.fetch_qdisc()
		for i in qdisc:
			if i['dev'] == self.iface and i['kind'] == 'htb' and i['handle'] == '10:':
				return True
		return False

	def create(self):
		tc('qdisc', 'add', 'dev', self.iface, 'root', 'handle', '10:', 'htb')
		tc('class', 'add', 'dev', self.iface, 'parent', '10:', 'classid', f'10:{self.minor}', 'htb', 'rate', self.bwlimit)
		tc('filter', 'add', 'dev', self.iface, 'parent', '10:', 'protocol', 'ip', 'prio', '10', 'handle', f'{self.minor}:', 'cgroup')
