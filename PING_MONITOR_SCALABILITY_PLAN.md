# Ping Monitor scalability and UX plan

## Objective

Make Ping Monitor comfortable at 5,000 simultaneously monitored targets,
including approximately 150 APs and 4,850 clients. A 30-second polling round
must finish in less than 20 seconds with a two-second response deadline, and a
30-second network-configuration snapshot interval must remain usable.

The browser must remain responsive while it filters, sorts, paginates, and
calculates exact aggregate availability, p50, and p99 values from the selected
targets. The design must work without a separate service outside the virtual
ZoneDirector.

## Decisions already made

- Parse the ZoneDirector client XML once per ping round.
- Send ICMP requests concurrently in bounded groups rather than starting a
  process for every target.
- Use gzip level 6 for browser-facing and retained compressed data.
- Encode each observation in one byte: zero for no attempted ping, 255 for a
  timeout, and monotonically increasing latency codes 1 through 254. Preserve
  exact millisecond values through 100 ms and use logarithmic spacing above
  100 ms through the two-second response deadline.
- Keep APs and clients in the same target table and aggregate them together
  unless the operator filters them.
- Calculate exact percentile ranks from the retained codes; do not use sampled
  or approximate aggregation algorithms. The documented logarithmic encoding
  error above 100 ms is the only intended loss of latency precision.
- Changing a filter returns the timeline to aggregate mode.
- The aggregate timeline heading identifies its scope, for example
  `Performance timeline — 1,842 devices`.
- Do not publish pre-aggregated timeline rollups. Every individual and filtered
  aggregate timeline, including the unfiltered 30-day view, is derived in the
  browser from the retained one-byte observations.
- Publish browser history as gzip-compressed daily chunks. Completed UTC days
  are immutable; the current UTC day is regenerated and atomically replaced.
- Publish no pre-aggregated ping metrics at all in the first implementation.
  The browser derives table counts, latest-request status, availability, p50,
  and p99 directly from the daily raw observation chunks. Reconsider this only
  after measuring the implemented path on representative devices.
- Initial page load covers only the rolling 24-hour table and timeline window,
  which normally requires the current and previous UTC-day chunks. Older daily
  chunks are loaded lazily only when the operator selects the 7-day or 30-day
  range. Completed immutable chunks remain eligible for the browser HTTP cache.
- The table defaults to 25 rows per page, with 10, 25, and 50 row options.
- Manual multi-selection is not part of the initial work.
- The latest latency column describes the latest request's outcome. It must not
  silently show an older successful response after a newer timeout.
- The network diff defaults to `Changed branches` mode.
- A canonical six-byte device MAC address is Ping Monitor's primary key. The
  browser format does not expose or depend on a SQLite row ID.
- Do not use D3, DuckDB-Wasm, Arrow, or Parquet for storage or aggregation.
  Selected D3 modules may be reconsidered later if chart interactions become
  sufficiently complex to justify them.

## Measured baseline

Synthetic tests representing 5,000 targets produced these initial results:

- A verbose browser JSON response was 43,794,641 bytes uncompressed.
- Generating that JSON from a small fixture took approximately 3.6 seconds.
- Parsing it in V8 took approximately 1.3–2.1 seconds and retained roughly
  265 MB of JavaScript heap.
- Once parsed, a filter, sort, and page operation averaged approximately
  2.5 ms. Browser-side table operations are therefore not the main problem.
- Gzip level 6 reduced that particular JSON to 208,215 bytes. Compression took
  approximately 352 ms and decompression approximately 70 ms on the Pixelbook.
- A representative 24-hour SQLite database contained 14.4 million results and
  occupied approximately 831 MB. An exact all-target grouped query took about
  133 seconds. A one-hour all-target query took about 3.4 seconds.
- Scanning the same 14.4 million outcomes in a compact `Uint16Array` and
  calculating exact aggregate percentiles took approximately 0.14–0.20 seconds
  in V8. A 1,000-target cohort took approximately 27–35 ms.
- A Chromium Web Worker scanned a contiguous 432,000,000-byte `Uint8Array`
  representing 5,000 targets at 30-second intervals for 30 days. Three complete
  passes took 1.883, 1.805, and 1.872 seconds. Each pass accumulated attempts,
  replies, and 254-code histograms for 120 six-hour buckets and extracted p50
  and p99. The median throughput was approximately 231 million observations per
  second. Transferring ownership of the 432 MB buffer to the worker, starting
  it, and returning the result added approximately 19 ms beyond the three
  scans. Constructing and filling the synthetic buffer took approximately
  76 ms.
- A second, less repetitive 432,000,000-byte fixture used per-device latency
  baselines, jitter, missing attempts, timeouts, and occasional slow replies.
  Gzip level 6 reduced it to 233,872,352 bytes (54.1%). Chromium fetched it
  over localhost with native `Content-Encoding: gzip` decoding and materialized
  the 432 MB `ArrayBuffer` in 5.418 and 5.565 seconds. The response-header stage
  took 29 and 11 ms; downloading the response body, decompressing it, and
  allocating the buffer took 5.389 and 5.554 seconds. Adding the measured
  median exact worker scan gives approximately 7.29–7.44 seconds from request
  to aggregate result on the Pixelbook.
- Compressing that complete synthetic 30-day fixture at gzip level 6 took
  approximately 38.5 seconds in Node.js. This is not a ZD compression
  benchmark, but it reinforces that the complete history must not be rebuilt
  after each ping round. Only the small changing period should be recompressed.
- A follow-up Chromium test separated the browser phases by fetching a similar
  236,092,807-byte gzip file without HTTP content decoding, streaming it through
  `DecompressionStream`, and then assembling its 6,644 output chunks into one
  432,000,000-byte `Uint8Array`. Across three runs, compressed download and
  buffer materialization took 0.479–0.587 seconds, decompression into chunks
  took 1.947–1.966 seconds, and contiguous-array assembly took 0.103–0.281
  seconds. Response headers added 9–20 ms. Total load time was 2.605–2.833
  seconds, before the approximately 1.9-second aggregate scan.

These measurements are directional and were made on the Pixelbook, not on the
virtual ZD running on Frigate or Vinca. The first 432 MB worker test began with
an already resident contiguous buffer. The second included localhost HTTP
transfer, native gzip decompression, and buffer allocation, but not a real LAN,
the ZD's Appweb server, multiple-file pipelining, or mobile-browser performance.
Its compressed size depends on the synthetic latency distribution and must not
be treated as a measured production compression ratio. The explicitly phased
test was faster than the earlier native `Content-Encoding: gzip` result despite
similar compressed and decoded sizes. Treat both as directional until the same
production-derived fixture is tested through the actual Appweb delivery path;
gzip decode cost can depend on the compressed stream as well as the browser API
used to deliver it.

## Proposed data architecture

### Target metadata

Generate a small `ping-targets.json` after every completed round. It contains
identity and current configuration metadata required for display and filtering:

- canonical MAC primary key;
- device name, IP address, and target kind;
- SSID and upstream AP metadata, even though they are not displayed as table
  columns;
- enough version and generation metadata to detect mismatched observation
  files.

This payload contains no counts, percentiles, latest ping result, or other
derived ping metrics. Initially retain normal JSON objects for the metadata.
Five thousand rows should be small enough that a more complicated string-table
format is not justified. This assumption must be measured with realistic names
and metadata.

### Observation files

Store detailed ping outcomes in a versioned fixed-width binary matrix. One
unsigned byte represents each target in each round:

| Value | Meaning |
| --- | --- |
| `0` | No ping was attempted in this round |
| `1`–`100` | Successful response latency of 1–100 ms exactly |
| `101`–`254` | Successful response latency above 100 ms using a monotonic logarithmic codebook |
| `255` | Ping attempted but timed out |

The initial high-latency codebook spans 101–2,000 ms. Its adjacent values are
approximately 1.97% apart, giving a maximum rounding error of approximately
0.98%. All 154 logarithmic codes map to distinct integer millisecond values. A
response below 1 ms is represented as 1 ms. Timeout remains a distinct
code so replies and attempts remain exact.

Include the 254-entry `Uint16` latency lookup table and the timeout duration in
the file header. The roughly 508-byte table is negligible, makes historical
files self-describing, and permits later tuning without making old files
unreadable. Decoders validate that the table is monotonic and within the
declared timeout. If files with different codebooks contribute to one view,
the worker maps their codes into a common millisecond histogram rather than
merging code numbers directly.

This encoding deliberately does not retain separate historical reasons for an
unattempted ping, such as not-associated, absent, disabled, or unknown address.
Validate that no planned historical UX requires those reasons before freezing
format version 1.

The initial on-disk layout is:

1. a fixed header containing a magic value, format version, flags, period
   bounds, round count, and target count;
2. ascending 32-bit UTC Unix timestamps for the rounds;
3. ascending canonical six-byte device MAC addresses;
4. one contiguous `Uint8` result series per target, ordered by the shared
   timestamps.

Multi-byte fields use explicitly documented little-endian encoding, and each
section starts at an alignment suitable for its typed-array view. The decoder
must reject unknown versions, impossible sizes, nonascending timestamps or
MAC addresses, and truncated files before allocating based on file-supplied
counts.

Target-major ordering is preferred because arbitrary filters select targets.
Each selected target then has one contiguous observation range. An all-target
aggregate still performs a sequential scan over the complete matrix. Physical
storage order must never depend on name, IP, or current UI sort order.

At 5,000 targets and 2,880 rounds per day, each daily result matrix is 14.4 MB
before compression. Thirty complete days would be about 432 MB before headers
and compression, rather than the much larger observed SQLite representation.

### File lifecycle

- Regenerate `ping-current.bin.gz` with gzip level 6 after each completed ping
  round, using a temporary file and atomic rename. It contains only the current
  UTC day.
- At a UTC day boundary, publish the completed period under a timestamped,
  immutable name such as `ping-2026-09-04.bin.gz`, then begin a new current-day
  file.
- Publish a small manifest listing the available periods, format version,
  generation, exact time bounds, and current file. The current resource is
  never served stale; immutable dated resources may be cached indefinitely.
- Delete expired immutable files as complete units according to the retention
  policy.

The browser fetches completed and current resources separately. Tests with an
actual Chromium HTTP decoder showed that a response made by byte-concatenating
independent gzip members exposed only the first member. Gzip-member
concatenation must therefore not be used as a browser delivery mechanism.

## SQLite migration strategy

The first implementation keeps SQLite as the authoritative result store and
adds a materialized flat-file cache:

1. Export a completed UTC day from SQLite once.
2. Generate the current-day cache from only the current day's rows after
   each round.
3. Serve the cache to the browser without querying older SQLite rows.
4. Measure collection, export, storage, and recovery behavior on Frigate and
   Vinca.

If SQLite remains a meaningful collection or storage cost, replace its raw
time-series table with append-only daily files. SQLite can remain for the much
smaller target identity and configuration tables. A direct file writer needs
a framed round record with a declared length and integrity check so startup can
detect and discard an incomplete final record after a crash. Only one writer
may append to the current period.

The database must not be removed until equivalence, crash recovery, retention,
and upgrade tests pass. The cache layer is deliberately reversible.

## Browser processing

Fetch binary resources as `ArrayBuffer` objects and transfer ownership to a
Web Worker:

```javascript
const buffer = await response.arrayBuffer();
worker.postMessage(buffer, [buffer]);
```

The worker creates aligned `Uint32Array`, `Uint16Array`, and `Uint8Array` views
without turning observations into JavaScript objects. It merge-intersects the
sorted filtered MAC list with the file's sorted MAC directory, then maps matches to
contiguous target blocks and accumulates each chart bucket into integer
latency histograms plus state counters. Walking a histogram produces exact p50
and p99 ranks without sorting individual observations. Reported values above
100 ms are the codebook's representative quantized values, not the original
integer milliseconds; counts and percentile ranks remain exact.

### Time-period aggregation

For a selected time range, define half-open chart buckets `[start, end)` in UTC.
The shared timestamp for a ping round determines the bucket for every result in
that round. Precompute the bucket number once per round timestamp rather than
performing timestamp division again for every selected device.

The active table filters produce a sorted set of selected MAC primary keys. For
each matching target block and round:

- code `0` contributes neither an attempt nor a reply;
- code `255` contributes one attempt and no reply;
- codes `1` through `254` contribute one attempt, one reply, and one value to
  that bucket's latency histogram.

Latency percentiles include successful responses only. A timeout affects
availability but is not treated as an artificial 2,000 ms latency. For each
bucket with `N` successful responses, use the nearest-rank convention:

- p50 rank is `ceil(0.50 * N)`;
- p99 rank is `ceil(0.99 * N)`.

Walk the monotonic histogram until its cumulative count reaches each rank. If
all contributing files use the same codebook, the histogram may have 254 code
bins and the selected code maps directly through the lookup table. If historical
files use different codebooks, decode their representative values into a common
0–2,000 ms histogram before combining them. A bucket with attempts but no replies
has null p50/p99; a bucket with no attempts is explicitly empty.

Aggregation pools individual observations across all selected MACs. It must not
average or take percentiles of per-device p50/p99 values, because that would give
incorrect weighting. Availability is pooled replies divided by pooled attempts.
The worker also returns the number of selected devices and, for each bucket, the
number that actually had an attempted ping so the UI can describe sparse data.

Initial bucket widths remain one minute for 1 hour, ten minutes for 24 hours,
one hour for 7 days, and six hours for 30 days. Clip the first and last files to
the requested half-open range to prevent duplicated boundary rounds.

Long ranges are processed one daily file at a time. After a file updates the
aggregate histograms, its decompressed observation buffer can be discarded.
Only the metadata, selected-target indexes, histograms, and final chart points
remain resident. The browser's HTTP cache may retain the compressed immutable
resources.

The initial 24-hour view requests only the current and preceding UTC-day
chunks. Seven- and thirty-day chunks are loaded lazily when the operator selects
those ranges; immutable completed days may then be reused from the browser's
HTTP cache.

The page defaults to 24 hours and does not prefetch older history. Selecting 7
or 30 days requests the additional daily files needed by that range. Returning
to a shorter range reuses any immutable resources retained by the HTTP cache;
the mutable current-day resource is always revalidated or fetched without a
stale cache entry.

The same raw scan also derives each target's 24-hour replies and attempts,
latest ping-request timestamp, and latest result for the table. These values
exist only in browser memory and are recalculated when the relevant raw daily
chunks change; they are not emitted by the server as a summary or rollup.

The main thread owns table interaction and rendering but never scans the raw
history. Filtering and sorting operate on row indexes rather than rearranging
the observation matrix.

## Implemented UX changes

The target table columns are:

1. device name;
2. MAC address;
3. IP address;
4. 24-hour replies and attempts;
5. latest ping-request timestamp;
6. latest request latency or failure state.

Support ascending and descending sorting for every displayed column, instant
case-insensitive substring filtering by device name or MAC, IP-range filtering,
and 10/25/50-row pagination. SSID and upstream AP remain available as filters
without consuming table columns. If horizontal space is still insufficient,
use a horizontally scrollable table with a sticky device-name column and
header rather than hiding values.

The timeline defaults to an exact aggregate of all currently filtered targets.
Selecting one row changes it to that target; changing any filter restores
aggregate mode. Timeline computation is debounced briefly so typing an IP or
name filter does not start work for every intermediate keystroke.

The network-configuration diff receives three modes:

- all XML;
- hide unchanged lines;
- changed branches, which hides an entire XML subtree unless it or a descendant
  changed.

Changed branches is the default. Before and after panels appear side by side
when the viewport is wide enough, fall back to a vertical layout on narrow
screens, and synchronize vertical scrolling without creating a feedback loop.

Snapshot discovery follows the same daily-chunk principle as ping history, but
uses timestamp-only JSON because each complete capture has three separate XML
bodies. A small manifest advertises the retained UTC days and counts. Initial
page load reads only the current and previous day's timestamp indexes; longer
timeline ranges load older indexes lazily. The
compressed AP, client, and mesh XML is not downloaded until the user selects
two timestamps for a comparison.

## Assumptions requiring validation

### ZoneDirector and server compatibility

- **Gzip sidecar delivery:** Verify on the running ZD that requesting the base
  binary or JSON URL with `Accept-Encoding: gzip` selects its `.gz` sidecar and
  returns the correct `Content-Encoding`, `Content-Type`, `Content-Length`, and
  cache behavior. Existing `app.js`/`app.js.gz` handling suggests support, but
  the new MIME types and paths must be tested directly.
- **Fallback delivery:** If Appweb cannot negotiate gzip correctly for `.bin`,
  test fetching the literal `.bin.gz` resource and using
  `DecompressionStream('gzip')`. If any supported browser lacks that API, ship
  a small local fallback decompressor or change the response path; do not use a
  CDN dependency.
- **Gzip implementation:** Confirm the guest's BusyBox `gzip` accepts an
  explicit level 6 and measure it with realistic daily files. If it
  does not, use the smallest maintainable compressor already available in the
  build rather than silently accepting an unknown level.
- **Workers:** Verify external Web Workers run from the Ping Monitor page in
  both its legacy-page shell and the regular admin-console navigation path.
  Check MIME type and any content-security restrictions.
- **Typed-array transfer:** Verify transferable `ArrayBuffer` behavior and
  little-endian decoding in current Chrome, Firefox, and Safari on desktop,
  plus current Chrome on Android and Safari on iOS.
- **Caching:** Confirm that timestamped completed files are not re-downloaded
  unnecessarily and that the changing current file cannot be served stale.
  Unique immutable filenames remain the correctness mechanism if Appweb lacks
  modern cache-control features.

### Performance assumptions

- Repeat compression, decompression, decoding, aggregation, filtering, and
  rendering benchmarks with realistically distributed latency and failure
  states. The synthetic compression ratio is probably optimistic.
- Compare exact source p50/p99 values with the logarithmically encoded results
  using healthy, congested, and deliberately failing networks. Confirm that
  the selected 100 ms exact region, two-second maximum, and approximately 0.98%
  maximum high-latency rounding error are operationally acceptable.
- Measure gzip level 6 on the virtual ZD rather than extrapolating from the
  Pixelbook. Regenerating the growing current-day cache must not delay the next ping
  round or configuration snapshot.
- Benchmark the current-day SQLite export with 5,000 targets after nearly 2,880
  rounds. The query may need a covering index or a simpler
  ordered scan.
- Measure the two-second ICMP schedule with 5,000 targets. The present ten
  sequential groups of 512 would consume 20 seconds on total loss and therefore
  cannot meet the round budget. Test larger outstanding groups or a paced
  single outstanding set, including total-loss and slow-response cases, while
  watching controller CPU, packet loss, and network bursts.
- Benchmark a full 24-hour aggregate on representative desktop and mobile
  devices. Initial targets are less than one second of worker time on desktop,
  no main-thread task longer than 50 ms, and less than 100 MB peak incremental
  browser memory.
- Repeat the full 30-day raw scan after loading realistic gzip-compressed period
  files through Appweb. Separate HTTP-cache lookup, transfer, decompression,
  worker transfer, and scan timings. The localhost Chromium results of roughly
  5.4–5.6 seconds to produce a 432 MB buffer and 1.9 seconds to scan it are
  desktop lower-bound references, not ZD acceptance results. Establish an
  acceptable progressive loading state for slower desktop and mobile devices
  rather than introducing a pre-aggregated timeline.
- Stream immutable period files through the worker as they arrive so download,
  decompression, and scanning can overlap and the browser need not retain the
  complete 432 MB history. Measure bounded parallel fetching rather than
  assuming either fully serial or unrestricted parallel requests is best for
  the old Appweb server.
- Measure the metadata payload's compressed size, parse time, heap use, and table render
  time with 5,000 realistic rows. Convert the metadata to a columnar string-table
  format only if normal JSON violates the responsiveness or memory targets.
- Measure daily HTTP request overhead and bounded parallel loading on the old
  Appweb HTTP stack. A rolling 30-by-24-hour interval can intersect 31 UTC-day
  files because its first and last days are partial; clip both boundary files.
- Measure real compressed disk consumption over several days before choosing
  default 30-day retention for a 5,000-target deployment.

### Correctness and resilience assumptions

- Confirm that the ZoneDirector sources expose a canonical, stable, unique MAC
  for every monitored AP and client. Normalize case and separators before
  converting it to six bytes, reject multicast or malformed identifiers, and
  test how duplicate MACs are handled rather than silently merging devices.
- Compare worker-produced counts, states, p50, and p99 against an independent
  SQLite reference calculation for the same fixture.
- Cover target arrival, disappearance, reassociation, changed IP/name, disabled
  monitoring, irregular round timestamps, missed rounds, and clock changes.
- Verify atomic publication across a forced container stop and ZoneDirector
  administrative restart. A reader must see either the old complete file or
  the new complete file, never a partial file.
- Verify format-version mismatch behavior during upgrades and retain enough
  manifest information to ignore or migrate incompatible cached periods.
- Treat all timestamps as UTC Unix time. Local timezone and daylight-saving
  changes affect labels only, never period identity or ordering.

## Implementation sequence

1. Add repeatable large fixtures and benchmark scripts. Record results rather
   than treating development-machine timings as acceptance evidence.
2. Specify and test the binary encoder/decoder, logarithmic codebook,
   corruption checks, exact counts/ranks, and bounded latency error.
3. Add current-day and immutable-day materialization while retaining SQLite.
4. Validate gzip and cache headers on the real ZD server and all supported
   browser families.
5. Add the Web Worker and switch timeline aggregation to the binary cache.
6. Replace the target table payload with metadata only; derive its ping metrics
   from raw daily chunks and implement filtering, sorting, and pagination.
7. Implement aggregate timeline scope/reset behavior and performance budgets.
8. Implement changed-branch XML diff mode and synchronized responsive panes.
9. Run 5,000-target soak, restart, retention, corruption, desktop, and mobile
   validation.
10. Decide from measurements whether to retain SQLite raw observations, add
    any pre-aggregation, or move the writer to append-only daily files.

## Release acceptance

- A 5,000-target ping round completes in less than 20 seconds with two-second
  deadlines and a 30-second start-to-start interval.
- Client XML is parsed once per round and all results are committed or
  published as one logical round.
- Thirty-second configuration snapshots continue without starvation.
- The browser does not receive or construct the old per-observation JSON object
  graph.
- No server-generated counts, percentiles, latest-result summaries, or timeline
  rollups are used; all initial ping metrics come from daily raw chunks.
- Aggregate and individual counts and percentile ranks match the reference
  results. Latency values through 100 ms are exact, and values above 100 ms
  remain within the codebook's documented error bound.
- Filtering, sorting, pagination, and chart interaction remain responsive at
  5,000 targets.
- Historical loading has bounded browser memory and does not requery immutable
  SQLite history.
- Restart, interrupted-write, retention, upgrade, and cache-staleness tests
  pass on Frigate before release-candidate deployment elsewhere.
