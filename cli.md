# Command line interface

**Important note**: multiple instance of `backurne backup` **must** not be run simultaneously. The behavior is unexpected, and may lead to snapshot corruption. You should add a pgrep or something on your crontab to avoid that.


First of all, we should create some backups. Here, we have two backup policy : a daily for 30 days, and a hourly for 48 hours, this is the defaut:
```
35% [jack:~/backurne]./backurne backup
  INFO:  Processing proxmox: infrakvm1
  INFO:  Processing infraceph1:vm-136-disk-1 (daily;30)
  DEBUG: No snaps found on infraceph1:vm-136-disk-1
  INFO:  infraceph1:vm-136-disk-1: doing full backup
  INFO:  Processing infraceph1:vm-136-disk-1 (hourly;48)
  INFO:  infraceph1:vm-136-disk-1: doing incremental backup based on backup;daily;30;2018-06-01T15:44:26.072348
  INFO:  I will now download 2 snaps from px infrakvm1
  INFO:  Exporting infraceph1:vm-136-disk-1
Exporting image: 100% complete...done.
Importing image diff: 100% complete...done.
  INFO:  Export infraceph1:vm-136-disk-1 complete
  INFO:  Exporting infraceph1:vm-136-disk-1
Exporting image: 100% complete...done.
Importing image diff: 100% complete...done.
  INFO:  Export infraceph1:vm-136-disk-1 complete
  INFO:  Deleting vm-136-disk-1@backup;daily;30;2018-06-01T15:44:26.072348 .. 
  INFO:  Expiring our snapshots
```
As you can see, on the first backup is "full", the other is incremental (based on the full made seconds ago, thus very efficient).\
This is why using multiple policy does not cost much.


Let's run the command again:
```
16% [jack:~/backurne]./backurne backup
  INFO:  Processing proxmox: infrakvm1
  INFO:  Our last backup is still young, nothing to do
  INFO:  Our last backup is still young, nothing to do
  INFO:  I will now download 0 snaps from px infrakvm1
  INFO:  Expiring our snapshots
```
Nothing to do !\
You can run this command many time, as it will avoid doing backups if the previous one is not old enough.


Now, we should list our backuped disks:
```
17% [jack:~/backurne]./backurne ls
+-----------------+---------+--------------------------------------------------------------------+
|  Ident          |  Disk   |  UUID                                                              |
+-----------------+---------+--------------------------------------------------------------------+
|  test-backurne  |  scsi0  |  8eb4f698-afdc-45bb-9f6c-1833c42ae368;vm-136-disk-1;test-backurne  |
+-----------------+---------+--------------------------------------------------------------------+
```
 - `ident` is used as an identificator for human: for Proxmox's backups, this is the VM's name from the last run.
 - `Disk` is the disk adapter for proxmox, or the rbd image name for plain.
 - Finally, `UUID` is the real RBD image, as defined on Ceph, and is used as a primary key.


We can list the backups for this disk:
```
32% [jack:~/backurne]./backurne ls '8eb4f698-afdc-45bb-9f6c-1833c42ae368;vm-136-disk-1;test-backurne'
+------------------------------+-----------------------------------------------+
|  Creation date               |  UUID                                         |
+------------------------------+-----------------------------------------------+
|  2018-06-01 15:44:26.072348  |  backup;daily;30;2018-06-01T15:44:26.072348   |
|  2018-06-01 15:44:26.499066  |  backup;hourly;48;2018-06-01T15:44:26.499066  |
+------------------------------+-----------------------------------------------+
```
We see that both snapshots were created almost at the same time.


Now, we would like to inspect a snapshot's content.
```
32% [jack:~/backurne]sudo ./
backurne map 28b868e3-c145-4ea7-8dff-e5ae3b8093af\;scsi0\;nsint5 backup\;daily\;30\;2019-12-30T06\:00\:04.802699 
  INFO:  Mapping 28b868e3-c145-4ea7-8dff-e5ae3b8093af;scsi0;nsint5@backup;daily;30;2019-12-30T06:00:04.802699 ..
  INFO:  rbd 28b868e3-c145-4ea7-8dff-e5ae3b8093af;scsi0;nsint5 / snap backup;daily;30;2019-12-30T06:00:04.802699
  INFO:  └── /dev/nbd0 (fstype None, size 20G)
  INFO:      └── /dev/nbd0p1 on /tmp/tmp09nri0sh (fstype xfs, size 20G)
32% [jack:~/backurne]ls /tmp/tmp09nri0sh
bin  boot  dev  dlm  etc  home  initrd.img  initrd.img.old  lib  lib32  lib64  media  mnt  opt  proc  root  run  sbin  shared  srv  sys  tmp  usr  var  vmlinuz  vmlinuz.old
```

The `map` subcommand clone a specific snapshot, map it, maps the partitions (if any) and try to mount the filesystems.
Some things to consider:
- the subcommand must be run with CAP_SYS_ADMIN, it will handle block devices and mount filesystems.
- the mounted filesystem (or mapped block devices) is a clone of the snapshot, not the snapshot itself. It is thus writable, and will be deleted later : you can remove files or do whatever you want here without impacting the backup.

Wait, what is mounted here already ?
```
32% [jack:~/backurne]sudo ./backurne list-mapped
  INFO:  rbd 28b868e3-c145-4ea7-8dff-e5ae3b8093af;scsi0;nsint5 / snap backup;daily;30;2019-12-30T06:00:04.802699
  INFO:  └── /dev/nbd0 (fstype None, size 20G)
  INFO:      └── /dev/nbd0p1 on /tmp/tmp09nri0sh (fstype xfs, size 20G)
```

Once you have recovered your files, you should do some cleanups:
```
32% [jack:~/backurne]./backurne unmap '8eb4f698-afdc-45bb-9f6c-1833c42ae368;vm-136-disk-1;test-backurne' 'backup;hourly;48;2018-06-01T15:44:26.499066'
  INFO:  Unmapping 8eb4f698-afdc-45bb-9f6c-1833c42ae368;vm-136-disk-1;test-backurne@backup;hourly;48;2018-06-01T15:44:26.499066 ..
  INFO:  8eb4f698-afdc-45bb-9f6c-1833c42ae368;vm-136-disk-1;test-backurne@backup;hourly;48;2018-06-01T15:44:26.499066 currently mapped on /dev/nbd0
  INFO:  Deleting restore-1 ..
```

Finally, there are two subcommands for checks:
 - `check` shows errors if there is images on the live cluster without the daily snapshot.
 - `check-snap` hashes images to check if the data on the backup cluster is the same as on the live cluster (but it is slow ..)
