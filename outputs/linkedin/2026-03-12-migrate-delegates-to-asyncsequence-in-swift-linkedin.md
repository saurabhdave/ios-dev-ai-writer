Too many delegate callbacks? We migrated delegate-based APIs to AsyncSequence streams incrementally so callers could adopt at their own pace — without a risky big‑bang rewrite.

Practical guidance for teams doing this:
- Consider wrapping delegate callbacks with AsyncStream / AsyncThrowingStream + a Continuation (e.g., for CoreLocation or peripheral callbacks). Ensure you finish the continuation when the underlying object is torn down.
- Start with a dual‑API adapter: proxy delegate calls to both the existing protocol and the new AsyncSequence so consumers can migrate gradually.
- Validate parity in tests: write unit tests that consume the stream with for‑await‑in and add integration tests exercising both the delegate and stream paths. Use XCTest and runtime tools to watch for leaks.
- Make cancellation explicit: wire continuation.onTermination and Task.cancel to stop the underlying work (for example, stop location updates or BLE sessions) when consumers cancel.

Small Swift hint:
cont.onTermination = { _ in manager.stopUpdatingLocation() }
manager.startUpdatingLocation()

How have you staged rollouts for framework or library APIs? Any lessons learned?

#iOS #Swift #Architecture #Concurrency #CoreLocation
