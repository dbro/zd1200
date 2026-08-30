# ZoneDirector analytics research

This note records the feasibility investigation and the first implemented
data path for optional reporting in the ZoneDirector 10.5.1.0.282 web
interface. The current implementation is deliberately a small read-only
snapshot rather than a finished analytics UI.

## Existing data

ZoneDirector already retains hourly accounting in
`/tmp/sqlited/statistic.db`. The hourly client statistics include client, AP,
and SSID identifiers and byte counters, so 24-hour client/AP/SSID totals and
hourly sparklines can be calculated without client polling or a second
database. Existing controller logs can also provide lower-bound 24-hour counts
for controller disconnects and mesh/radio channel changes; log rotation and
controller downtime necessarily leave gaps.

The existing `stamgr` controller service exposes aggregate traffic trends and
top-ten history reports to the stock UI. It does not expose a general query for
all client/AP/SSID hourly records, so it cannot by itself provide the proposed
tables.

## Why the first prototype was removed

The first experiment compiled a small i386 SQLite reader for the ZD guest and
attempted to reach it through a CGI wrapper. The stock Webs build does not
include `mod_cgi`; `.cgi` content is therefore served as text rather than
executed. That prototype has been removed.

Webs uses Embedthis Ejscript modules for `.jsp` pages. The stock modules have
magic `0xC7DA` and module-format revision **2**. They are precompiled `.mod`
files. Supplying a source `.jsp` does not activate a source compiler and closes
the request; it is not a supported extension mechanism on this build.

The publicly archived Ejscript 2.0 compiler was tested. Although its language
version is 2.0, it emits module-format revision **3**, which the ZD loader
rejects. Relabeling that bytecode as revision 2 would be unsafe: the module
format is explicitly internal and the vendor View/Controller ABI also differs.

## Accepted EJS toolchain

The controller's `Webs` binary identifies itself as Embedthis Appweb **3.4.2**.
The public Appweb 3.3.0 source tree (the historical `doghell/appweb-3` tag
`v3.3.0`) builds an `ajsc` compiler that emits the same module header as the
stock controller modules: magic `0xC7DA`, format revision **2**. This is a
format match, not a header rewrite.

A small View module compiled by that toolchain was copied to a temporary
`/web/admin10/analytics_probe.mod` on a running 10.5.1.0.282 controller. A
request for the corresponding `.jsp` returned the View's generated text with
HTTP 200. That demonstrates that revision-2 EJS modules are an accepted web
extension mechanism on this release.

The server accepts the View/Controller surface from `ejs.web`; it does **not**
provide the broader native EJS feature set assumed by public Appweb builds.
Temporary modules that explicitly used `ejs.db.sqlite` (`Sqlite`), `ejs.io`
(`File`), or `ejs.sys` (`System.run`) returned a clean HTTP 500 from the
request worker. The ordinary controller UI and the other workers remained
healthy. There is no `libsqlite` or installed EJS SQLite module in this image.
Consequently, an EJS page cannot directly query `statistic.db`, read a
precomputed file, or run a helper in this Webs build.

The practical deep-integration boundary is therefore a revision-2 EJS View
for presentation and navigation, paired with a separate, tightly scoped data
path that is not CGI and never forms shell commands from HTTP input. Before
adding that path, validate both the stock authentication model for custom
`admin10` routes and a way to expose the existing hourly data without adding
client/AP polling or a second history database.

## Implemented: static read-only snapshot

The runtime initramfs installs `/admin10/zd1200-analytics.html` on every guest
boot. A late, root-owned init script waits for `statistic.db`, copies it to a
private file on the writable partition, runs a small statically linked i386
helper against that stable copy, and atomically writes its JSON next to the
copy. `/web/admin10/zd1200-analytics-snapshot.json` is a symlink to that JSON,
so the page can fetch it from the same origin even though the main root
filesystem is immutable. The copy is a transient input snapshot, not a new
history database.

The helper has no command-line arguments and opens the copy with
`SQLITE_OPEN_READONLY`. It is built as a static 32-bit musl binary because a
modern glibc static binary cannot reliably access files from the controller's
Linux 2.6.32 guest. Its initial fixed output reports only database
presence/size and the row count of `statis_client_h`. It is intentionally not
a general SQL endpoint, does not poll APs or clients, and does not create a
second history database. It is not linked from the stock UI yet.

Client responsiveness/ping monitoring was deliberately not implemented. It
requires a separate design: APs could be enabled by default, while client
probes should be opt-in and record hourly latency percentiles with timeouts
treated as 1000 ms.
