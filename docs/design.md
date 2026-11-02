# Design notes: separate intent from delivery

## Why an outbox?

A database rollback cannot undo a remote HTTP update. Treating local and remote writes as one transaction conceals partial success. The local account row records intended state; the outbox records whether that intent has reached the remote system.

The operation ID is a SHA-256 digest of the canonical account/version/owner payload. It is a stable idempotency identifier, not a secret or an authentication mechanism. The mock verifies the payload digest and stores successful receipts durably.

## Ordering and concurrency

Each account has monotonically increasing versions. Delivery excludes candidates with undelivered predecessors for that account. The remote independently enforces the expected version. A failed predecessor blocks its successors until an operator resolves the inconsistency; it must not be silently skipped.

Claim transactions are short and finish before HTTP calls. An expiring lease enables recovery after worker failure. Fencing prevents obsolete workers from updating local delivery state after a new claim. Remote idempotency still matters because fencing a local update cannot cancel a remote side effect.

## Retry policy

The demonstration uses deterministic bounded exponential delay, without jitter or `Retry-After` handling. Retry exhaustion requires reconciliation because repeated timeouts do not prove the remote operation failed. Attempts consumed by crashes are persisted, but crash loops do not by themselves transition to reconciliation until a worker records a retryable result.

## Audit boundary

Delivery outcome and outbox status commit together. Remote application commits independently. If the local status write fails after remote success, the lease expires and a replay can recover the receipt. The audit is a local operational record, not tamper-evident compliance storage.

## Extension path

Add authenticated vendor adapters and contract tests, reconciliation tooling, jitter and rate-limit handling, operational metrics, retention rules, and a production worker lifecycle. If the remote lacks idempotency, compare stable external references and read-back state before retries; do not simply claim equivalent guarantees.

## Interview discussion

Explain the four distinct outcomes: local rejection, remote rejection, confirmed delivery, and uncertain delivery. Demonstrate why a timeout belongs to the uncertain category and why the remote receipt resolves that uncertainty.
