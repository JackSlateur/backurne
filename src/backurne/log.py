import logging
import logging.handlers
import sys
import syslog
from termcolor import colored
from .config import config


class ConsoleFormatter(logging.Formatter):
	def format(self, record):
		if record.levelno == logging.DEBUG:
			msg = f'[{record.filename}:{record.lineno}:{record.funcName}()] {record.msg}'
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

		msg = f'{front}{msg}'

		record.msg = msg

		return logging.Formatter.format(self, record)

def report_time(image, endpoint, duration):
	if config['report_time'] is None:
		return

	msg = f'Image {image} from {endpoint} backed up, elasped time: {duration}'
	if config['report_time'] == 'syslog':
		syslog.syslog(syslog.LOG_INFO, msg)
	else:
		with open('/tmp/log', 'a') as f:
			f.write(f'{msg}\n')


log = logging.getLogger('backurne')

slog = logging.handlers.SysLogHandler(address='/dev/log')
detailed_formatter = logging.Formatter('%(name)s[%(process)d]: %(levelname)s: [%(filename)s:%(lineno)s:%(funcName)s()] %(message)s')
slog.setFormatter(detailed_formatter)
log.addHandler(slog)

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
