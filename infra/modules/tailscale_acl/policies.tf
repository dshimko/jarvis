# This module creates no policy documents. Any policy it ever gains goes in this map, which the
# `policies` output exports to infra/tests (the source scan requires exactly that wiring).
locals {
  policies = {}
}
