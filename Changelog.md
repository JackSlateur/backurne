Version 2.0.0

**Notable changes** :
 * The `list-mapped` subcommand has been reworked to support complex mapping. Command outputs (both cli & api) has been altered to support those changes.
 * **Backurne** now supports LVM. See README.md for its specific configuration.
 * **Backurne** now supports vmware. Also see README.md.

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
