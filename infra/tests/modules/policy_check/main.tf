# Checks policy documents against the IAM wildcard allowlist (DESIGN.md section 4).
# A statement with `*` or `?` in Resource or NotResource must match its allowlist row exactly:
# effect, Action/NotAction set, resource set, principal set, and every condition as
# (operator, key, exact values). Allow with Action "*" or Principal "*" always fails.

locals {
  statements = flatten([
    for pname, doc in var.policies : [
      for idx, s in flatten([jsondecode(doc).Statement]) : {
        policy     = pname
        index      = idx
        sid        = try(s.Sid, "")
        effect     = s.Effect
        action_key = can(s.NotAction) ? "NotAction" : "Action"
        actions    = sort(distinct(flatten([try(s.Action, s.NotAction, [])])))
        resources  = sort(distinct(flatten([try(s.Resource, []), try(s.NotResource, [])])))
        principals = sort(try([tostring(s.Principal)], flatten([for k, v in s.Principal : [for x in flatten([v]) : "${k}:${x}"]]), []))
        conditions = sort(flatten([for op, kv in try(s.Condition, {}) : [for k, v in kv : "${op}|${k}|${join(",", sort(flatten([v])))}"]]))
      }
    ]
  ])

  keyed = [for s in local.statements : merge(s, {
    key      = "${s.policy}|${s.sid}"
    wildcard = anytrue([for r in s.resources : length(regexall("[*?]", r)) > 0])
  })]

  allowlist = merge(local.allowlist_resource_star, local.allowlist_patterns)

  rows = {
    for k, r in local.allowlist : k => merge(r, {
      actions    = sort(distinct(r.actions))
      resources  = sort(distinct(r.resources))
      principals = sort(r.principals)
      conditions = sort([for c in r.conditions : "${c.op}|${c.key}|${join(",", sort(c.values))}"])
    })
  }

  wildcard_statements = [for s in local.keyed : s if s.wildcard]
  exact_candidates    = [for s in local.keyed : s if !s.wildcard]

  exact_rows = {
    for k, r in local.exact_statements : k => merge(r, {
      actions    = sort(distinct(r.actions))
      resources  = sort(distinct(r.resources))
      principals = sort(r.principals)
      conditions = sort([for c in r.conditions : "${c.op}|${c.key}|${join(",", sort(c.values))}"])
    })
  }

  # Every non-wildcard statement must match its exact row too; anything new fails.
  exact_unlisted = [
    for s in local.exact_candidates : "${s.key}: statement not in the exact-match table (tests/modules/policy_check/exact_statements.tf)"
    if !contains(keys(local.exact_rows), s.key)
  ]

  exact_mismatch = flatten([
    for s in local.exact_candidates : compact([
      s.effect != local.exact_rows[s.key].effect ? "${s.key} (${local.exact_rows[s.key].id}): effect ${s.effect}" : "",
      s.action_key != local.exact_rows[s.key].action_key ? "${s.key} (${local.exact_rows[s.key].id}): uses ${s.action_key}" : "",
      s.actions != local.exact_rows[s.key].actions ? "${s.key} (${local.exact_rows[s.key].id}): actions ${jsonencode(s.actions)}" : "",
      s.resources != local.exact_rows[s.key].resources ? "${s.key} (${local.exact_rows[s.key].id}): resources ${jsonencode(s.resources)}" : "",
      s.principals != local.exact_rows[s.key].principals ? "${s.key} (${local.exact_rows[s.key].id}): principals ${jsonencode(s.principals)}" : "",
      s.conditions != local.exact_rows[s.key].conditions ? "${s.key} (${local.exact_rows[s.key].id}): conditions ${jsonencode(s.conditions)}" : "",
    ]) if contains(keys(local.exact_rows), s.key)
  ])

  # An Allow whose action contains `*` must be a wildcard-allowlist row, never an exact row.
  wildcard_action_violations = [
    for s in local.keyed : "${s.key}: Allow with a wildcard action ${jsonencode(s.actions)} is not in the DESIGN.md section 4 allowlist"
    if s.effect == "Allow" && anytrue([for a in s.actions : length(regexall("[*?]", a)) > 0]) && !contains(keys(local.rows), s.key)
  ]

  generic_violations = flatten([
    for s in local.keyed : compact([
      s.sid == "" ? "${s.policy} statement ${s.index}: missing Sid" : "",
      s.effect == "Allow" && contains(s.actions, "*") ? "${s.key}: Allow with Action \"*\"" : "",
      s.effect == "Allow" && s.action_key == "NotAction" ? "${s.key}: Allow with NotAction" : "",
      s.effect == "Allow" && (contains(s.principals, "*") || contains(s.principals, "AWS:*")) ? "${s.key}: Allow with Principal \"*\"" : "",
    ])
  ])

  duplicate_violations = [
    for k in distinct([for s in local.keyed : s.key]) : "${k}: duplicate Sid in one policy"
    if length([for s in local.keyed : s if s.key == k]) > 1
  ]

  unlisted_violations = [
    for s in local.wildcard_statements : "${s.key}: wildcard resource ${jsonencode(s.resources)} not in the DESIGN.md section 4 allowlist"
    if !contains(keys(local.rows), s.key)
  ]

  mismatch_violations = flatten([
    for s in local.wildcard_statements : compact([
      s.effect != local.rows[s.key].effect ? "${s.key} (${local.rows[s.key].id}): effect ${s.effect}" : "",
      s.action_key != local.rows[s.key].action_key ? "${s.key} (${local.rows[s.key].id}): uses ${s.action_key}" : "",
      s.actions != local.rows[s.key].actions ? "${s.key} (${local.rows[s.key].id}): actions ${jsonencode(s.actions)}" : "",
      s.resources != local.rows[s.key].resources ? "${s.key} (${local.rows[s.key].id}): resources ${jsonencode(s.resources)}" : "",
      s.principals != local.rows[s.key].principals ? "${s.key} (${local.rows[s.key].id}): principals ${jsonencode(s.principals)}" : "",
      s.conditions != local.rows[s.key].conditions ? "${s.key} (${local.rows[s.key].id}): conditions ${jsonencode(s.conditions)}" : "",
    ]) if contains(keys(local.rows), s.key)
  ])
}
