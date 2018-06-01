import logging
import logging.handlers
import sys
from termcolor import colored
from config import config


class ConsoleFormatter(logging.Formatter):
	def format(self, record):
		err = colored('  CRIT:  ', 'red')
		warn = colored('  WARN:  ', 'yellow')
		info = colored('  INFO:  ', 'green')
		debug = colored('  DEBUG: ', 'green')
		if record.levelno == logging.ERROR:
			record.msg = '%s%s' % (err, record.msg)
		if record.levelno == logging.WARNING:
			record.msg = '%s%s' % (warn, record.msg)
		if record.levelno == logging.INFO:
			record.msg = '%s%s' % (info, record.msg)
		if record.levelno == logging.DEBUG:
			record.msg = '%s%s' % (debug, record.msg)

		return logging.Formatter.format(self, record)


log = logging.getLogger('backurne')

syslog = logging.handlers.SysLogHandler(address='/dev/log')
detailed_formatter = logging.Formatter('%(name)s[%(process)d]: %(levelname)s: %(message)s')
syslog.setFormatter(detailed_formatter)
log.addHandler(syslog)

if sys.stdout.isatty():
	console = logging.StreamHandler()
	if config['pretty_colors'] is True:
		console.setFormatter(ConsoleFormatter())
	log.addHandler(console)

if config['log_level'] == 'debug':
	log.setLevel(logging.DEBUG)
elif config['log_level'] == 'info':
	log.setLevel(logging.INFO)
elif config['log_level'] == 'warn':
	log.setLevel(logging.WARNING)
else:
	log.setLevel(logging.ERROR)
