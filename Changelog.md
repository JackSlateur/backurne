PENDING

**Notable changes**:

Version 2.2.1

**Notable changes**:
 * gzip has been replaced by zstd.
 * fix unmap when a LV is spread across multiple PV, inside the same vmdk
 * a per backup progress is now shown in the proctitle
 * add a warning if some snapshot could not be deleted in time

Thanks to Cyllene (https://www.groupe-cyllene.com/) for sponsoring this work !

**Notable changes** :

Version 2.2.0

**Notable changes** :
 * add a --cleanup option to the `backup` subcommand.
 * fix vmfs6 support.
 * add a --debug option for one-shot verbosity.
 * rework the `map` subcommand with enhancement to the vmdk support (especially in conjunction with lvm).
 * 'Plain' cluster can now be reached not only via SSH, but also via any user-defined way. Kubernetes is the main target here, yet it should work with anything.

Version 2.1.0

**Notable changes** :
 * Backuping only a subset for a `backurne backup` invocation is now possible, as well as forcing a backup (despite being considered unneeded regarding the profile). See [cli.md](cli.md).
 * **Backurne** now reports time elapsed to process each backup, either to a plain file or via syslog. See the `report_time` configuration entry.

Version 2.0.0

**Notable changes** :
 * The `list-mapped` subcommand has been reworked to support complex mapping. Command outputs (both cli & api) has been altered to support those changes.
 * **Backurne** now supports LVM. See [README.md](README.md) for its specific configuration.
 * **Backurne** now supports vmware. Also see [README.md](README.md).

Version 1.1.0

**Notable changes** :
 * **Backurne** now supports a hook infrastructure. Action can be performed before and after specific event : for instance, stopping a database slave before backup, and starting it after.

Version 1.0.0

This version is centered around ease of use and reporting. The core algorithm has not changed much, but the release is supposed to be easier for people to use, simplier to understand etc.

**Notable changes** :
 * **Backurne** now supports per-image locks. Multiple **Backurne** can now run at the same time, safely. However, worker count is per instance (backup_worker and live_worker).
 * The source tree has been reworks to use python3-setuptools. Debian packages is supported, for easier install / updates.
 * Status reporting has been greatly improved : output is more concise, progress is shown as much as possible. Each process current task is shown in **ps**, **htop** etc.
 * Options parsing has been reworked and is more bulletproof.
