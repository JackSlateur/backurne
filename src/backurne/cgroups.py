import os

from cgroupspy import trees


class Cgroups:
	def __init__(self):
		self.tree = trees.Tree()

		root = self.tree.get_node_by_path('/net_cls/')
		if root is None:
			raise Exception('Cannot found the net_cls cgroup - is the pseudofs mounted in /sys/fs/cgroup ?')

		our_root = self.tree.get_node_by_path('/net_cls/backurne/')
		if our_root is None:
			root.create_cgroup('backurne')
			our_root = self.tree.get_node_by_path('/net_cls/backurne/')

		self.root = our_root

	def setup_netcls(self, name, classid):
		# The cg may be created by another backurne instance
		# or by another Consumer object
		try:
			self.root.create_cgroup(name)
		except:
			pass

		cg = self.tree.get_node_by_path(f'{self.root.path.decode("utf-8")}/{name}/')

		cg.controller.class_id = classid
		cg.controller.tasks = [os.getpid(),]
