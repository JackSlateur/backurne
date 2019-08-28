import logging
import logging.handlers
import sys
from termcolor import colored
from config import config


class ConsoleFormatter(logging.Formatter):
	def format(self, record):
		if record.levelno == logging.DEBUG:
			msg = '[%s:%s:%s()] %s' % (record.filename, record.lineno, record.funcName, record.msg)
		else:
			msg = record.msg
		if record.levelno == logging.ERROR:
			front = colored('  CRIT:  ', 'red')
		if record.levelno == logging.WARNING:
			front = colored('  WARN:  ', 'yellow')
		if record.levelno == logging.INFO:
			front = colored('  INFO:  ', 'green')
		if record.levelno == logging.DEBUG:
			front = colored('  DEBUG: ', 'green')

		msg = '%s%s' % (front, msg)

		record.msg = msg

		return logging.Formatter.format(self, record)


log = logging.getLogger('backurne')

syslog = logging.handlers.SysLogHandler(address='/dev/log')
detailed_formatter = logging.Formatter('%(name)s[%(process)d]: %(levelname)s: [%(filename)s:%(lineno)s:%(funcName)s()] %(message)s')
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
