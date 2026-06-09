# TODO

## Metrics

- Decide and implement OTel environment isolation for metrics exporters. ADR-0006
  currently limits first-version runtime configuration to Perago-owned
  `RuntimeConfig` fields, but the OpenTelemetry Python SDK may still read generic
  OTEL environment variables when exporter or reader constructor arguments are
  left unset. Follow-up work should explicitly decide whether Perago passes
  controlled defaults such as empty headers and timeout values, rejects unsupported
  generic OTEL variables during config parsing, or documents a narrower supported
  interaction model.
- Make OTel metrics shutdown honor Perago timeout configuration. `OtelMetricRecorder`
  currently calls `MeterProvider.shutdown()` without passing the parsed metrics
  timeout, so worker shutdown can still use the SDK default even when
  `OTEL_EXPORTER_OTLP_METRICS_TIMEOUT` is configured to a shorter value.
