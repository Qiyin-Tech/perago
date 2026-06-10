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
