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
	print('Usage:\t%s %s' % (me, bold('backup')))
	print('\t%s %s' % (me, bold('check')))
	print('\t%s %s' % (me, bold('check-snap')))
	print('\t%s %s' % (me, bold('list-mapped')))
	print('\t%s %s [%s]' % (me, bold('ls'), rbd))
	print('\t%s %s %s' % (me, bold('map'), snap))
	print('\t%s %s %s' % (me, bold('unmap'), snap))
	exit(1)


def Pt(header):
	header = [bold(i) for i in header]
	pt = PrettyTable(header)
	pt.align = 'l'
	pt.padding_width = 2
	return pt
