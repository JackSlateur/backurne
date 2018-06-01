# Rest API documentation

#### Note
No authentification nor authorization is made in any way. You should use a proxy, with basic auth and TLS.\
Lastly, the API code **must** be run as root (well, it must CAP_SYS_ADMIN), because it will handle block devices, mount filesystems etc.

## Listing backed up disks
```
12% [jack@jack:~]curl -s http://localhost:5000/backup/ | python -mjson.tool
[
    {
        "disk": "vm-136-disk-1",
        "ident": "test-backurne",
        "uuid": "8eb4f698-afdc-45bb-9f6c-1833c42ae368;vm-136-disk-1;test-backurne"
    }
]
```

## Listing snapshot for a disk
```
11% [jack@jack:~]curl -s "http://localhost:5000/backup/8eb4f698-afdc-45bb-9f6c-1833c42ae368;vm-136-disk-1;test-backurne/" | python -mjson.tool
[
    {
        "creation_date": "2018-06-01 15:44:26.072348",
        "uuid": "backup;daily;30;2018-06-01T15:44:26.072348"
    },
    {
        "creation_date": "2018-06-01 15:44:26.499066",
        "uuid": "backup;hourly;48;2018-06-01T15:44:26.499066"
    }
]
```

## Map a snapshot
```
11% [jack@jack:~]curl -s "http://localhost:5000/map/8eb4f698-afdc-45bb-9f6c-1833c42ae368;vm-136-disk-1;test-backurne/" | python -mjson.tool
{
    "path": "tmp4_6ipuaw",
    "success": true
}
```
The files can then be explored via a webgui at http://localhost:5000/explore/tmp4_6ipuaw/

## Listing currently mounted snapshots
```
11% [jack@jack:~]curl -s "http://localhost:5000/mapped/" | python -mjson.tool
[
    {
        "dev": "/dev/nbd0",
        "image": "restore-1",
        "mountpoint": "/tmp/tmp4_6ipuaw",
        "parent_image": "8eb4f698-afdc-45bb-9f6c-1833c42ae368;vm-136-disk-1;test-backurne",
        "parent_snap": "backup;hourly;48;2018-06-01T15:44:26.499066"
    }
]
```

## Cleaning things up
```
18% [jack@jack:~]curl -s "http://localhost:5000/unmap/8eb4f698-afdc-45bb-9f6c-1833c42ae368;vm-136-disk-1;test-backurne/" | python -mjson.tool
{
    "success": true
}
```
