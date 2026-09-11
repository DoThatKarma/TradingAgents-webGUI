# ADR 0001: Standalone GUI repo (not a fork)

Status: accepted

Upstream is a pip dependency. Rationale: upstream updates can never overwrite
GUI work; independent release cadence; the plugin SDK lives in its own fork
where the upstream PR is prepared.
