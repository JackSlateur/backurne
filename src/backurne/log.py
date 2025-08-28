import datetime
import logging.handlers
import sys
import syslog

from termcolor import colored

from .config import config


class ConsoleFormatter(logging.Formatter):
    def format(self, record):
        if record.levelno == logging.DEBUG:
            msg = (
                f"[{record.filename}:{record.lineno}:{record.funcName}()] {record.msg}"
            )
        else:
            msg = record.msg
        if record.levelno == logging.ERROR:
            front = colored("  CRIT:  ", "red")
        if record.levelno == logging.WARNING:
            front = colored("  WARN:  ", "yellow")
        if record.levelno == logging.INFO:
            front = colored("  INFO:  ", "green")
        if record.levelno == logging.DEBUG:
            front = colored("  DEBUG: ", "green")

        msg = f"{front}{msg}"

        record.msg = msg

        return logging.Formatter.format(self, record)


def report_to_influx(image, endpoint, duration):
    from influxdb import InfluxDBClient

    conf = config["influxdb"]

    if conf["host"] is None or conf["db"] is None:
        log.warning("influxdb: host or db are not defined, cannot do proper reporting")
        return

    if conf["mtls"] is None:
        influx = InfluxDBClient(
            conf["host"],
            conf["port"],
            database=conf["db"],
            ssl=conf["tls"],
            verify_ssl=conf["verify_tls"],
        )
    else:
        influx = InfluxDBClient(
            conf["host"],
            conf["port"],
            database=conf["db"],
            ssl=conf["tls"],
            verify_ssl=conf["verify_tls"],
            cert=conf["mtls"],
        )

    data = [
        {
            "measurement": "backurne",
            "tags": {
                "image": image,
                "endpoint": endpoint,
            },
            "time": datetime.datetime.now().replace(microsecond=0).isoformat(),
            "fields": {
                "duration": int(duration.total_seconds()),
            },
        }
    ]

    influx.write_points(data)


def report_time(image, endpoint, duration):
    if config["report_time"] is None:
        return

    msg = f"Image {image} from {endpoint} backed up, eelapsed time: {duration}"
    msg = f"{datetime.datetime.now()}: {msg}"
    if config["report_time"] == "syslog":
        syslog.syslog(syslog.LOG_INFO, msg)
    elif config["report_time"] == "influxdb":
        report_to_influx(image, endpoint, duration)
    else:
        with open(config["report_time"], "a") as f:
            f.write(f"{msg}\n")


def has_debug(log):
    return log.level == logging.DEBUG


log = logging.getLogger("backurne")

slog = logging.handlers.SysLogHandler(address="/dev/log")
detailed_formatter = logging.Formatter(
    "%(name)s[%(process)d]: %(levelname)s: [%(filename)s:%(lineno)s:%(funcName)s()] %(message)s"
)
slog.setFormatter(detailed_formatter)
log.addHandler(slog)

if sys.stdout.isatty():
    console = logging.StreamHandler()
    if config["pretty_colors"] is True:
        console.setFormatter(ConsoleFormatter())
    log.addHandler(console)

if config["log_level"] == "debug":
    log.setLevel(logging.DEBUG)
elif config["log_level"] == "info":
    log.setLevel(logging.INFO)
elif config["log_level"] == "warn":
    log.setLevel(logging.WARNING)
else:
    log.setLevel(logging.ERROR)
