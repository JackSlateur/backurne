from prettytable import PrettyTable
from termcolor import colored


def bold(text):
    return colored(text, attrs=["bold"])


def Pt(header):
    header = [bold(i) for i in header]
    pt = PrettyTable(header)
    pt.align = "l"
    pt.padding_width = 2
    return pt
