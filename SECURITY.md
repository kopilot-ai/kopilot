# Security Policy

## Reporting a vulnerability

Report vulnerabilities privately through
[GitHub security advisories](https://github.com/kopilot-ai/kopilot/security/advisories/new).
You will get an acknowledgment within 72 hours and a fix or a documented
decision within 30 days. Please do not open public issues for security
reports.

## Scope

The safety layer (approval gating, protected namespaces, blocked patterns,
the autonomy engine) is defense in depth on top of Kubernetes RBAC. The RBAC
of the service account remains the authoritative boundary. Bypasses of the
safety layer are still treated as vulnerabilities and fixed; two such
bypasses were found by adversarial review and closed in 0.2.0.

## Supported versions

Only the latest minor release receives security fixes.
