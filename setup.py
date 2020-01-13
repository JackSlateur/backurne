import setuptools

with open('README.md', 'r') as fh:
	long_description = fh.read()

setuptools.setup(
	name='backurne',
	version='2.0.0',
	author='Alexandre Bruyelles',
	author_email='backurne@jack.fr.eu.org',
	description="Backup Ceph's RBD on Ceph, with Proxmox integration",
	long_description=long_description,
	long_description_content_type='text/markdown',
	url='https://github.com/JackSlateur/backurne',
	packages=setuptools.find_packages('src'),
	package_dir={'': 'src'},
	classifiers=[
		'Programming Language :: Python :: 3',
		'License :: OSI Approved :: GNU General Public License v2 (GPLv2)',
		'Operating System :: POSIX :: Linux',
	],
	entry_points={
		'console_scripts': [
			'backurne = backurne.backurne:main',
		]
	},
	python_requires='>=3.5',
	install_requires=['termcolor', 'PTable', 'requests',
			'proxmoxer', 'sh', 'python-dateutil', 'filelock',
			'setproctitle', 'progressbar'],
)
