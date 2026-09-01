# ZoneDirector analytics research

This note records the feasibility investigation for optional reporting in the
ZoneDirector 10.5.1.0.282 web interface and the implemented Ping Monitor data
path.

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

## Implemented: Ping Monitor data collector

The runtime installs a separate local ping-monitor service at
`/writable/zd1200-ping-monitor/`. It owns `pings.db`, intentionally separate
from the vendor accounting database because ping observations do not exist in
ZoneDirector's data store. The only retained ping history is the individual
raw observation: timestamp, target, response/timeout/absent state, and RTT.
Rows older than 30 days are removed. Hourly p50/p99 values are calculated only
when the static page asks for its fixed JSON snapshot; they are not stored as a
second history or roll-up table.

APs with valid IPv4 addresses are added as enabled targets from the existing
controller AP list. Clients are opt-in and are keyed by MAC address plus their
configured name and IPv4 address. A client absent from a freshly collected
stock client view is recorded as `not_associated` and is not pinged; a present
client that does not answer ICMP is a `timeout`. If the current client view
could not be collected, the observation is `unknown` and is not counted as an
ICMP failure. This avoids confusing normal laptop absence or a collector gap
with a network-quality failure.

Every five minutes a root-owned local collector authenticates only to
`https://127.0.0.1` using the optional dedicated Monitoring Admin account. It
stores *unparsed* timestamped copies of the three stock read-only XML replies:

- AP summary;
- wireless-client summary; and
- mesh view.

The collector accepts no browser input, cannot send credentials to a routed or
environment-selected destination, and never creates structured configuration
history. The Ping Monitor page shows target MAC/IP/name, 24-hour log-scale
p50-to-p99 bars, timeout/absence counts, and a browser-side difference between
any two raw snapshot timestamps. The browser parses only enough XML to match
records by MAC and compare non-volatile attributes; the preserved XML remains
the forensic source of truth for later human or LLM investigation.

The static Webs extension cannot safely write the target SQLite database. The
helper therefore exposes narrow local maintenance commands for the forthcoming
settings bridge: `add-client MAC IP NAME` and `set-enabled ID 0|1`. They accept
data values only, never a shell command. A direct Settings UI requires a
separately validated authenticated write bridge; it is deliberately not faked
by the static page.
