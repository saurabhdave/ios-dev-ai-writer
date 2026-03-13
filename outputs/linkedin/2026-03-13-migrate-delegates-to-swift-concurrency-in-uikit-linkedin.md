Delegates are foundational in UIKit, but as codebases grow they can make control flow and cancellation harder to follow. I recently published a practical guide for migrating delegates to Swift Concurrency with a phased rollout in mind.

Key takeaways:
- For single‑response callbacks, consider withChecked(Throwing)Continuation; add timeouts and test with XCTest async.
- For repeated events, AsyncStream works well. Drive consumption from a Task that’s scoped to your view controller or coordinator lifecycle.
- Instrument Task and stream lifetimes (e.g., OSLog) and validate behavior with Instruments (Allocations/Leaks) to look for unexpected retention.
- During rollout, keep dual APIs so consumers can migrate at their own pace — e.g., continue supporting @objc delegates alongside new async APIs.

A practical caution: delegates are often synchronous and ordering‑sensitive. If you need to preserve immediate run‑loop semantics, resume continuations on the main actor or retain delegate callbacks where appropriate.

Small example pattern:
coordinator.continuation = cont

If this resonates, I’d love to hear where you’ve hit friction migrating large modules — rollout strategies, telemetry, or testing approaches. Who’s tried a phased switch in a multi‑module app?

#iOS #Swift #UIKit #Concurrency #Architecture
