# 013 — Local web processing and result privacy

Scope: a separate Python web service, existing deterministic Core, cookie ownership,
two-file submission, durable background queue, and public-by-link result pages.
Local implementation first; production deployment and account login are out of scope.

Acceptance criteria:
- A public `/faq` page explains the preservation-first algorithm, all eight current
  processing stages, upload storage/retention, browser-only downloads and exactly what
  is visible through shared results. It works without JavaScript and is linked from
  home, upload and result pages, with canonical/social metadata in the web service.
- FIT and GPX submit to the same-origin service and redirect to `/res/<random uid>`.
- The result page shows a loader and polls every 5 seconds, without overlapping requests.
- Queued, processing, ready, failed, expired and missing results have usable states.
- The existing detector runs without the course. Web processing explicitly enables
  `fill_missing_from_course`, with MEDIUM invalidation and reconstruction thresholds,
  as requested by the user. Core/CLI defaults stay unchanged.
- A ready report shows the geometry, repair summary and FIT diff; no fabricated output
  is offered when repair is unnecessary or refused.
- The result map has independent Original FIT / Corrected FIT / GPX course toggles,
  labelled «Исходный», «Исправленный», «Трек». Leaflet is served locally; OpenStreetMap
  tiles include attribution and an origin-only referrer, never the result UID.
- «О забеге» reuses the internal HTML report's metrics and kilometre splits, with pace
  and ascent/descent charts, missing-data states and the existing distance-quality warning.
- Home and result HTML include server-rendered description, canonical, Open Graph and
  Twitter Card metadata without JavaScript or cookies. Absolute URLs use configured
  public origin, never forwarded headers. Ready previews use only public aggregate
  metrics and retain uncertainty/partial-repair notices; pending, failed, missing and
  expired jobs get neutral state-specific previews without stale metrics. A shared
  public trail image is used, without publishing raw files or owner credentials.
- Anyone with the UID can view the public report, but downloading requires the original
  uploader's independent HttpOnly session cookie, checked server-side on every request.
- Only the owner sees the explanation that FIT download requires the browser used
  for uploading. The shared page uses neutral copy; guests see no download-access
  notice. Polling intervals are not exposed in user-facing copy.
- No raw FIT/GPX, owner secret, input names, absolute paths, device metadata, absolute
  activity timestamps or per-record sensor telemetry are exposed through public JSON, HTML or errors.
  The requested running summary explicitly publishes duration, pace, elevation totals
  and kilometre aggregates (including mean heart rate and cadence) through an allowlist.
- Original FIT/GPX are stored together in a separate private `uploads/<uid>/` directory,
  retained after successful/failed processing, and expire with the result after 7 days.
  Incomplete/rejected uploads are removed. Queued legacy inputs migrate on restart.
- A rotating private JSONL log records uploads, processor invocations and outcomes,
  using the same `pair_id` on every event. It includes file sizes/hashes, invocation
  parameters and duration, but no owner cookies or raw activity contents.
- File/request sizes, queue/storage capacity and processing duration are bounded.
- Synthetic integration tests verify actual repair, cookie isolation, expired sessions,
  guest/wrong-owner denial, invalid files, restart recovery and polling behavior.
- Existing Core tests stay green. No Core algorithms, thresholds or FIT semantics change.
