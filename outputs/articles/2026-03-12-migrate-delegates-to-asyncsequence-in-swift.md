# Migrate Delegates to AsyncSequence in Swift

My app started with dozens of delegate protocols — UI, networking, peripheral callbacks — tangled into state machines that were expensive to reason about and brittle to test. Recasting delegate callbacks as AsyncSequence instances lets you turn noisy callback graphs into composable, testable streams without a big rewrite. This article shows a pragmatic, production-minded path to migrate delegates to AsyncSequence while preserving compatibility and minimizing release risk.

## Why This Matters for iOS Teams Right Now

Modern Swift concurrency includes async/await primitives and AsyncSequence types that make it easier to express asynchronous event streams. For teams, that means an opportunity to reduce callback complexity and improve testability.

- Tooling and primitives to consider: AsyncStream / AsyncThrowingStream, Continuation APIs, Task and Task.cancel.
- Decision criterion: Prefer incremental adapters when many external clients depend on existing delegates; prefer full replacement only in modules you fully control.
- Operational note: Plan rollouts with feature flags and telemetry to detect behavioral divergence and resource leaks.

> Replace brittle callback graphs with typed streams to make flow control and termination explicit.

## 1. Map Delegate Events to AsyncSequence

### Pattern: Adapter with AsyncStream
Wrap delegate events in an adapter that exposes an AsyncStream<Event> or AsyncThrowingStream<Event, Error>. Route delegate callbacks into the stream using the stream’s Continuation.yield and call continuation.finish() on teardown or terminal errors.

- APIs to use: AsyncStream, AsyncThrowingStream, AsyncStream<Element>.Continuation.
- When to choose which: Use AsyncStream for continuous, non-throwing event sequences. Use AsyncThrowingStream when the delegate surface can emit terminal errors consumers must react to.
- Observability/testing note: Emit lifecycle logs when you create, yield, and finish continuations. Unit test the adapter by driving delegate callbacks and consuming the AsyncSequence with for-await-in to verify ordering and termination.

Implementation tips:
- Keep the delegate reference weak in proxy objects to avoid retain cycles between the adapter and the original owner.
- Respect buffering: choose AsyncStream.BufferingPolicy to match expected burstiness and memory characteristics.
- Ensure continuation.finish() is called when the underlying producer stops or when the adapter is deinitialized to avoid retained, never-finished streams.

## 2. Incremental Migration and Backward Compatibility

### Pattern: Dual API with Proxying
Expose a new async API alongside the existing delegate protocol. Internally install a delegate proxy that both forwards to the old delegate and yields events into the AsyncStream. This preserves behavior for existing consumers while enabling new clients to adopt streams.

- APIs to use: withCheckedContinuation for single-shot conversions; AsyncStream for streaming conversions.
- When to choose this vs alternatives: Use the dual-API adapter when public APIs are consumed by external teams or by code that cannot adopt async/await yet. If the module is internal and you can update all consumers, a direct replacement can simplify maintenance.
- Operational/testing note: Create integration tests asserting parity between delegate callbacks and stream emissions under identical stimuli. Track a metric for active streams so you can detect unexpectedly long-lived subscriptions.

Implementation tips:
- Ensure ordering and error semantics between delegate and stream paths match as closely as possible.
- Stage rollouts behind feature flags for widely-used libraries; flip the flag after testing confirms behavior parity.
- If Objective‑C or non-async consumers exist, maintain delegate callbacks until they can migrate or provide a compatibility wrapper that continues to call delegate methods.

## 3. API Design and Consumer Ergonomics

### Pattern: Raw Stream + Small Helpers
Offer the raw AsyncSequence for composition and convenience helpers for common patterns (e.g., next() async, await-first-event). Make cancellation semantics explicit—document whether cancelling a consuming Task cancels the producer and how resources are cleaned up.

- APIs to use: Task, Task.cancel, continuation.onTermination.
- When to choose this vs alternatives: Expose the raw sequence if you expect consumers to compose streams or implement backpressure. Provide helper async functions for callers that expect single-shot or simple sequential usage.
- Observability/testing note: Unit test helpers for correct cancellation propagation. Use continuation.onTermination to log and clean up resources when consumers cancel.

Implementation tips:
- Use continuation.onTermination to perform cleanup and cancel any underlying work or subscriptions when the consumer cancels or the sequence is otherwise terminated.
- Prefer creating Tasks in the owning actor or controlled context so cancellation propagates predictably; use detached tasks only when producers must explicitly outlive their creator.
- Clearly document whether multiple consumers are allowed and what semantics (multicast vs single-consumer) apply; AsyncStream is single-subscription by default unless you implement your own multicast behavior.

## 4. Tradeoffs and Common Pitfalls

### Pattern: Observe Lifecycle and Memory
AsyncSequence centralizes lifecycle management. If continuations aren’t handled correctly, you can get leaks, missed completions, or behavior differences versus delegates.

- Tools to use: XCTest for unit tests, Instruments for allocations and leak detection.
- When to choose this vs alternatives: Replace delegates entirely in modules where you control all consumers to avoid dual-path complexity. Use adapter-first for public APIs or frameworks with broad consumers.
- Operational/testing note: Run allocation and leak checks after migration. Add unit tests that assert continuations finish on teardown and that resources are released.

Common pitfalls:
- Forgetting to call continuation.finish() in teardown or deinit — leads to retained continuations and waiting consumers.
- Strong captures of self inside continuation closures — causes retain cycles; prefer weak captures or actor-isolated ownership.
- Divergent semantics between delegate callbacks and AsyncSequence emissions — can be missed without integration tests comparing both paths.

## 5. Validation, Testing, and Rollout Checklist

### Practical Checklist
1. Unit tests: consume the AsyncSequence with for-await-in and assert event order, duplication, and termination semantics.
2. Integration tests: exercise the old delegate path and new async path with identical stimuli to ensure parity.
3. Observability: log continuation lifecycle events and export active-stream counts as telemetry to detect leaks or unexpected longevity.
4. Memory checks: use Instruments (Allocations + Leaks) to verify no retained continuations after teardown.
5. Rollout: stage the adapter behind a feature flag or ship as opt-in; enable more broadly after telemetry confirms parity.

- Tools to use: XCTest for deterministic assertions; Instruments for runtime validation.
- Operational note: Prioritize deterministic tests for behavioral parity and use telemetry and runtime tools to detect resource issues in the field.

Closing Takeaway

Treat migration from delegates to AsyncSequence as an adapter-led evolution rather than an all-in rewrite. Use AsyncStream/AsyncThrowingStream and Continuation APIs to create typed streams, keep delegate compatibility while you migrate, and instrument lifecycle and resource usage. With careful continuation handling, explicit cancellation semantics, and a disciplined validation plan (unit tests, integration tests, and runtime leak checks), you can simplify event flows and make them more testable without destabilizing releases.

## Swift/SwiftUI Code Example

```swift
import SwiftUI
import CoreLocation

// AsyncSequence that wraps CLLocationManagerDelegate to provide location updates
struct LocationUpdatesSequence: AsyncSequence {
    typealias Element = CLLocation
    typealias AsyncIterator = AsyncStream<Element>.Iterator

    struct Configuration {
        var desiredAccuracy: CLLocationAccuracy = kCLLocationAccuracyBest
        var distanceFilter: CLLocationDistance = kCLDistanceFilterNone
        var pausesLocationUpdatesAutomatically: Bool = true
        var activityType: CLActivityType = .other
    }

    private let configuration: Configuration

    init(configuration: Configuration = .init()) {
        self.configuration = configuration
    }

    func makeAsyncIterator() -> AsyncIterator {
        let stream = AsyncStream<Element> { continuation in
            let helper = CLLocationHelper(continuation: continuation, configuration: configuration)
            // retain helper via the continuation's onTermination closure until the stream ends
            continuation.onTermination = { @Sendable _ in
                helper.stop()
            }
            helper.start()
        }
        return stream.makeAsyncIterator()
    }

    private final class CLLocationHelper: NSObject, CLLocationManagerDelegate {
        private let manager: CLLocationManager
        private let configuration: Configuration
        private var continuation: AsyncStream<Element>.Continuation

        init(continuation: AsyncStream<Element>.Continuation, configuration: Configuration) {
            self.continuation = continuation
            self.configuration = configuration
            self.manager = CLLocationManager()
            super.init()
            manager.delegate = self
            applyConfiguration()
        }

        private func applyConfiguration() {
            manager.desiredAccuracy = configuration.desiredAccuracy
            manager.distanceFilter = configuration.distanceFilter
            manager.pausesLocationUpdatesAutomatically = configuration.pausesLocationUpdatesAutomatically
            manager.activityType = configuration.activityType
        }

        func start() {
            switch CLLocationManager.authorizationStatus() {
            case .notDetermined:
                manager.requestWhenInUseAuthorization()
            case .authorizedWhenInUse, .authorizedAlways:
                manager.startUpdatingLocation()
            default:
                continuation.finish()
            }
        }

        func stop() {
            manager.stopUpdatingLocation()
            continuation.finish()
        }

        func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
            for location in locations {
                continuation.yield(location)
            }
        }

        func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
            continuation.finish()
        }

        func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
            switch manager.authorizationStatus {
            case .authorizedAlways, .authorizedWhenInUse:
                manager.startUpdatingLocation()
            case .denied, .restricted:
                continuation.finish()
            default:
                break
            }
        }
    }
}

// Example SwiftUI view that consumes LocationUpdatesSequence
struct LocationView: View {
    @State private var latestLocation: CLLocation?
    @State private var isTracking = false
    @State private var taskHandle: Task<Void, Never>?

    var body: some View {
        VStack(spacing: 16) {
            if let loc = latestLocation {
                Text("Lat: \(loc.coordinate.latitude)")
                Text("Lon: \(loc.coordinate.longitude)")
                Text("Accuracy: \(Int(loc.horizontalAccuracy))m")
            } else {
                Text("No location yet")
            }

            HStack {
                Button(isTracking ? "Stop" : "Start") {
                    isTracking.toggle()
                    if isTracking {
                        startTracking()
                    } else {
                        stopTracking()
                    }
                }
                .padding()
                .background(Color.blue.opacity(0.1))
                .cornerRadius(8)
            }
        }
        .padding()
        .onDisappear {
            stopTracking()
        }
    }

    private func startTracking() {
        // Guard against multiple tasks
        guard taskHandle == nil else { return }

        // Start a Task to iterate the async sequence
        let task = Task { @MainActor [weak self] in
            // Iterate until sequence finishes or task is cancelled
            for await location in LocationUpdatesSequence() {
                // If the task was cancelled, stop processing
                if Task.isCancelled { break }
                self?.latestLocation = location
            }
            // Ensure state reflects stopped tracking when the sequence ends
            await MainActor.run {
                self?.isTracking = false
                self?.taskHandle = nil
            }
        }

        taskHandle = task
    }

    private func stopTracking() {
        taskHandle?.cancel()
        taskHandle = nil
        isTracking = false
    }
}
```

## References

- No verified external references were available this run.
