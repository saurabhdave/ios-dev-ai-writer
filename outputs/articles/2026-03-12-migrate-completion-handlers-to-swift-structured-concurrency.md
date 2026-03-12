# Migrate Completion Handlers to Swift Structured Concurrency

Legacy completion-handler code is common in iOS apps, but callbacks and ad‑hoc cancellation can make concurrent code noisy and brittle. Swift’s structured concurrency (async/await, Task, actors) can clarify control flow and cancellation — if you migrate carefully. This article gives a practical, team-ready path to move completion handlers to structured concurrency with attention to correctness, cancellation, and observability.

## 1. Why This Matters for iOS Teams Right Now
Completion handlers remain widespread across networking, storage, and SDK integrations. Many Apple frameworks and popular libraries offer async/await-friendly APIs alongside callbacks, and keeping old callback patterns increases maintenance surface area and cognitive load.

When to act: consider migrating when you control a shared network layer or SDK surface consumed by multiple teams, or when callback complexity is producing bugs, tangled control flow, or hard-to-reproduce resource leaks.

Operational note: migrations that ignore cancellation and cleanup can leave background work and network connections alive, which may affect battery and user experience. Use runtime tracing and profiling tools to validate behavior after changes.

## 2. Interop: Bridging Completion Handlers and async/await
### How To Bridge Single-Callback APIs
Adapt single-shot completion-handler APIs into async functions with continuations such as withCheckedThrowingContinuation or withCheckedContinuation. When you wrap an underlying cancellable task (for example, a URLSessionTask), map Task cancellation to the underlying cancel method so resources are released when the calling Task is cancelled.

Tooling/primitive: withCheckedThrowingContinuation, URLSessionTask.cancel().

Decision criterion: Use a continuation when the API has a single terminal callback (success or failure) and does not produce multiple values over time.

Operational/testing note: Add unit tests asserting the continuation resumes exactly once and instrument the adapter to log start/finish and unexpected timeouts.

### When To Use AsyncSequence Instead
For repeated events, streams, or multi-callback flows (sockets, notification streams, live updates), adapt the API to AsyncSequence rather than a single-shot continuation.

Tooling/primitive: AsyncSequence.

Decision criterion: Use AsyncSequence when values arrive over time or when you need iteration and backpressure semantics.

Operational/testing note: Test termination and cancellation of the AsyncSequence explicitly; ensure downstream Task cancellation closes the producer channel to avoid resource leaks.

Note: Resume continuations deterministically — failing to resume or double-resuming are common and serious sources of bugs.

## 3. Migration Strategies for Codebases and Teams
### Strangler Pattern and Boundary Decisions
Migrate module-by-module rather than performing piecemeal changes across unrelated modules. Offer async overloads alongside existing completion APIs in a release and deprecate callbacks on a schedule so consumers can migrate at their own pace.

Tooling/primitive: @available for deprecation annotations; package or framework boundaries to limit impact.

Decision criterion: Prefer module-level migration when teams own discrete frameworks or SDK layers. Use smaller scope refactors only for tiny, private helpers.

Operational/testing note: Run compatibility tests that exercise both old and new surfaces. Consider phased rollouts or feature flags to limit blast radius while you monitor production signals.

### API Surface and Backward Compatibility
Provide stable dual APIs during the transition and document behavior such as delivery context. Preserve expected execution context (for example, main-thread delivery) when callers rely on it.

Tooling/primitive: DispatchQueue for explicit thread delivery when needed.

Decision criterion: If callers rely on main-thread callbacks, dispatch to main explicitly; otherwise preserve caller expectations about threading.

Operational/testing note: Add CI assertions that verify delivery context and add tracing around thread hops to help diagnose surprises in runtime traces.

## 4. Tradeoffs and Common Pitfalls
### Cancellation and Resource Cleanup
Structured concurrency makes cancellation more explicit, but you must propagate it to underlying resources. Map Task.cancel() to URLSessionTask.cancel(), Operation.cancel(), or your own cleanup logic.

Tooling/primitive: Task, URLSessionTask.cancel(), OperationQueue.

Decision criterion: Use child Tasks within a Task hierarchy for cooperative cancellation; avoid Task.detached where you want cancellation to follow the parent Task.

Operational/testing note: Add unit tests that cancel parent Tasks and assert underlying resources are released. Monitor cancellation counts separately from error rates in logs.

### Continuation Misuse and Hangs
Failing to resume a continuation will hang the awaiting Task. Double-resuming a continuation will trigger runtime checks and can crash in debug builds.

Tooling/primitive: withChecked(Throwing)Continuation (checked continuations help catch misuse during development).

Decision criterion: Prefer checked continuations during development to catch double-resumes. For high-risk adapters, consider adding timeouts or explicit fallbacks.

Operational/testing note: Use XCTestExpectation with timeouts in CI to detect hangs, and add tracing around continuations to surface stalls in runtime traces.

### Actor Hopping and Scheduling Surprises
Actors serialize access to mutable state but introduce scheduling hops and potential contention. Over-actorization can add latency where low-latency reads are important.

Tooling/primitive: actors, Task.priority.

Decision criterion: Use actors for correctness-critical shared mutable state; prefer local state or immutable models for read-heavy paths to reduce contention.

Operational/testing note: Profile actor-serialized sections to detect contention and measure latency. Add lightweight tracing for actor entry/exit in hot code paths to understand scheduling costs.

## 5. Validation, Observability, and Rollout
### Test and Instrument Every Migration
Validation should include unit tests, integration tests, and runtime tracing.

Tooling/primitive: XCTest, Instruments, os_log, os_signpost.

Decision criterion: Unit test logic and continuation behavior; use integration/system tests for cancellation and cleanup behaviors.

Operational/testing note: Build a CI checklist that includes continuation resume assertions, cancellation propagation tests, and delivery-context checks. Use phased releases to observe production signals before removing old APIs.

### Production Observability Practices
Add structured logging and signposts around long-running Tasks and network adapters.

- Add os_signpost around request lifecycle and async Task boundaries.
- Log continuation start/finish with request identifiers.
- Track cancellation counts separately from errors.

Operational/testing note: Correlate signposts with runtime traces during debugging sessions; surface cancellation spikes in your monitoring system to detect regressions.

## 6. Practical Migration Checklist
### Ready-to-Run Steps
1. Inventory: find completion-style APIs and Result-based callbacks to build a migration list. Tooling such as SourceKit-based scripts or simple grep can help.
2. Categorize: mark each API as single-shot, streaming, or state callback.
3. Prototype: wrap a private single-shot API with withCheckedThrowingContinuation and map Task.cancel() to the underlying cancel.
4. Test: add XCTest cases for normal completion, error paths, cancellation, and timeout behavior.
5. Rollout: publish async overloads, deprecate completion APIs with @available annotations, and release in phases.

Team rules:
- Centralize networking behind a single adapter layer where practical.
- Use actors for shared mutable caches; prefer immutable models or local state where possible to reduce synchronization.
- Instrument all changes with os_signpost and structured logs.

## Closing Takeaway
Migrating completion handlers to Swift structured concurrency can reduce cognitive overhead and make cancellation explicit — but it introduces runtime risks if done naively. Use checked continuations for single-shot callbacks, AsyncSequence for streams, and always map Task cancellation to underlying cancel operations. Migrate module-by-module, validate with tests and runtime tracing, and gate rollout with phased releases and observability. Treat third‑party behavior as higher risk and test adapters before broad adoption.

## Swift/SwiftUI Code Example

_No validated code snippet was generated this run._

## References

- No verified external references were available this run.
