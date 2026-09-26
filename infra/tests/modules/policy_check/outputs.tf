output "violations" {
  description = "Every rule violation found; empty means the policies pass."
  value = concat(local.generic_violations, local.duplicate_violations, local.unlisted_violations,
  local.mismatch_violations, local.exact_unlisted, local.exact_mismatch, local.wildcard_action_violations)
}

output "matched_keys" {
  description = "Allowlist rows matched by a wildcard statement in these policies."
  value       = sort([for s in local.wildcard_statements : s.key if contains(keys(local.rows), s.key)])
}

output "statement_count" {
  description = "Statements inspected."
  value       = length(local.statements)
}

output "allowlist_keys" {
  description = "Every allowlist row key (38 rows, DESIGN.md section 4)."
  value       = sort(keys(local.allowlist))
}

output "exact_keys" {
  description = "Every exact-match row key (non-wildcard statements)."
  value       = sort(keys(local.exact_statements))
}

output "matched_exact_keys" {
  description = "Exact-match rows matched by a statement in these policies."
  value       = sort([for s in local.exact_candidates : s.key if contains(keys(local.exact_rows), s.key)])
}
