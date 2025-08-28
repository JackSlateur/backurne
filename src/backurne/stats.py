import multiprocessing

import humanize

from .ceph import Ceph
from .config import config


def print_stats():
    ceph = Ceph(None)

    result = {}

    with multiprocessing.Pool(config["backup_worker"]) as p:
        for sizes in p.imap_unordered(ceph.du, ceph.ls()):
            sizes = sizes["images"]
            for i in sizes:
                try:
                    result[i["name"]] += i["used_size"]
                except KeyError:
                    result[i["name"]] = i["used_size"]

    result = [(k, result[k]) for k in sorted(result, key=result.get)]
    for key, value in result:
        print(key, humanize.naturalsize(value))
