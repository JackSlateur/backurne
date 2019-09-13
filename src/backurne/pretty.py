import sys
from prettytable import PrettyTable
from termcolor import colored


def bold(text):
	return colored(text, attrs=['bold'])


def under(text):
	return colored(text, attrs=['underline'])


def usage():
	me = bold(sys.argv[0])

	rbd = under('rbd')
	snap = '%s %s' % (rbd, under('snapshot'))
	sys.stderr.write('Usage:\t%s %s\n' % (me, bold('backup')))
	sys.stderr.write('\t%s %s\n' % (me, bold('check')))
	sys.stderr.write('\t%s %s\n' % (me, bold('check-snap')))
	sys.stderr.write('\t%s %s\n' % (me, bold('list-mapped')))
	sys.stderr.write('\t%s %s [%s]\n' % (me, bold('ls'), rbd))
	sys.stderr.write('\t%s %s %s\n' % (me, bold('map'), snap))
	sys.stderr.write('\t%s %s %s\n' % (me, bold('unmap'), snap))
	sys.stderr.write('\t%s %s\n' % (me, bold('stats')))
	exit(1)


def Pt(header):
	header = [bold(i) for i in header]
	pt = PrettyTable(header)
	pt.align = 'l'
	pt.padding_width = 2
	return pt
