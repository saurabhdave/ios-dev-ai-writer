# Migrate NotificationCenter Observers to Swift Concurrency

One change of practice many teams adopt is to treat NotificationCenter observers as explicit, cancellable tasks rather than relying on implicit observer lifetimes. Implicit lifetimes from addObserver(_:selector:) and block-based observers can lead to retained objects, unexpected updates after a component is gone, and bugs that are hard to reproduce. Using Swift Concurrency APIs makes lifetime and cancellation explicit and easier to observe and test when your codebase uses async/await.

## Why This Matters for iOS Teams Right Now

NotificationCenter is widely used for system events, cross-module signaling, and SDK hooks. Traditional observer APIs attach handlers whose lifetimes can be easy to mismanage. When a codebase adopts Swift Concurrency, you can express subscriber lifetime as a Task or AsyncSequence consumer, which makes cancellation an explicit event you can log, test, and monitor.

- API to consider: NotificationCenter.notifications(named:object:) (AsyncSequence)
- When to choose this: prefer AsyncSequence when you want linear, cancellable event loops without adding another reactive dependency.
- Operational note: consider adding structured logging around Task start/stop and track counts of active notification consumer Tasks to help detect leaked or long-lived subscribers.

## 1. CHOOSE THE RIGHT API: ASYNCSEQUENCE VS COMBINE VS LEGACY

### Decision Criteria and Calls-to-Action
NotificationCenter.notifications(named:object:) provides an AsyncSequence you can iterate with for-await-in and cancel by cancelling the enclosing Task. If your codebase already uses Combine and relies on operator pipelines (debounce, buffer, map, etc.), keeping NotificationCenter.Publisher may be more practical.

- APIs to weigh: NotificationCenter.notifications(named:object:), NotificationCenter.Publisher, AsyncStream/AsyncThrowingStream
- When to choose each:
 - AsyncSequence: adopt async/await broadly, need linear handlers, and want Task cancellation semantics.
 - Combine: keep when you have existing operator-heavy pipelines and cancellation tied to Combine subscriptions.
 - AsyncStream/AsyncThrowingStream: use when you need custom buffering or to bridge asynchronous callback-based code into async/await.
- Testing/Observability:
 - Log Task creation and cancellation.
 - Emit a correlation identifier with events when helpful for tracing.

### Practical Considerations
If handlers access shared mutable state, run them inside an actor or call actor-isolated methods to avoid data races and to make state access and cancellation explicit. Prefer Tasks that are managed by a logical owner (view, view model, or actor) rather than detached Tasks for application-wide consumers so you have a clear cancellation and ownership model.

## 2. MIGRATION PATTERNS: FROM addObserver TO for-await-in

### Step-by-Step Migration Pattern
Tackle small, well-scoped modules first. Replace addObserver(_:selector:) or block observers with an async Task that iterates NotificationCenter.notifications(named:).

- Primitives to use: Task, NotificationCenter.notifications(named:object:)
- Scope patterns:
 - View-scoped: create a Task tied to the view lifecycle and cancel it when the view is dismissed or let SwiftUI view Task lifecycles handle cancellation.
 - Service-scoped: use an actor or a service object that spawns a supervised Task and exposes a cancel method for clean shutdown.
- Testing/Observability:
 - Unit-test by posting notifications and awaiting sequence emissions.
 - Add logs on receipt and verify Task cancellation in tests.

Example pattern (conceptual):
1. Spawn a Task when the lifecycle starts (viewDidLoad / onAppear).
2. Use for await notification in NotificationCenter.notifications(named:).
3. Handle the notification, checking for cancellation where appropriate.
4. Cancel the Task explicitly on lifecycle end or rely on structured concurrency lifecycles.

Replace implicit observer lifetime with an explicit Task lifetime so cancellation points are visible in code and logs.

## 3. TRADEOFFS AND PITFALLS

### Key Tradeoffs
AsyncSequence provides straightforward cancellation semantics and simple code for sequential handling, but it does not include the operator set that Combine provides. If you depend on buffering, backpressure, or complex transformations, you may need to keep Combine for those paths or implement equivalent behavior with AsyncStream or manual buffers.

- When to stick with Combine: when operator composition and backpressure semantics are central to your logic.
- When to prefer AsyncSequence: when linear processing and explicit Task-based cancellation are primary concerns.
- Testing/operational note: changing how events are processed can affect timing and memory behavior; validate with tests and runtime observation.

### Common Pitfalls and Failure Modes
- Forgotten cancellation: A Task left running can cause the same problems as an unremoved observer.
- Mixed paradigms: Interleaving Combine and async/await without clear ownership of cancellation can complicate debugging.
- Third-party integrations: Some SDKs expect observers added with addObserver; you may need bridging code and tests for mixed approaches.

Debugging tips:
- Log Task start/stop and notification handling with correlation identifiers.
- Monitor a metric for active notification consumer Tasks and alert on sustained growth.

## 4. VALIDATION, TESTING, AND OPERATIONS

### How to Validate Correctness and Performance
Write XCTest async tests that:
- Post notifications and await expected sequence emissions.
- Cancel subscriber Tasks and assert no further handling occurs.

Use profiling tools (e.g., Leaks, Allocations) in scenarios that exercise notification-heavy flows to detect regressions in memory or lifecycle behavior.

- Test guidance:
 - Unit async tests for message ordering and cancellation behavior.
 - Integration and profiling runs for lifecycle and memory verification.
- Observability:
 - Emit structured logs for Task lifecycle events.
 - Record counts of handled notifications and active Tasks; use alerts to detect survivors.

## 5. PRACTICAL CHECKLIST FOR AN INCREMENTAL MIGRATION

### Stepwise Rollout
1. Inventory all NotificationCenter.addObserver usages and categorize by lifecycle: UI-scoped, service-scoped, global.
2. Prototype one low-risk UI-scoped module using NotificationCenter.notifications(named:).
3. Add unit tests for delivery and cancellation and add structured logs.
4. Run profiling (memory and allocations) on scenarios that exercise the change.
5. Roll out incrementally and monitor task-count metrics and logs.

- When to be conservative: prefer incremental migration for large codebases or when third-party observers are present.
- Operational note: maintain bridging tests for mixed observer styles and have a rollback plan if lifecycle regressions appear.

## Closing Takeaway

Migrating NotificationCenter observers to Swift Concurrency can make subscriber lifetimes and cancellation explicit and easier to test and observe. NotificationCenter.notifications(named:) is a convenient choice for straightforward, cancellable flows; keep Combine where its operator set is required. Ensure every Task you create has a documented owner and cancellation site, add structured logging and unit tests that validate both delivery and cancellation, and roll out changes incrementally while monitoring for lifecycle and memory regressions.

## Swift/SwiftUI Code Example

```swift
import SwiftUI
import Observation

// Model that migrates NotificationCenter observers to Swift Concurrency
@Observable
public final class KeyboardObserver {
    // Public state observed in SwiftUI
    public var keyboardHeight: CGFloat = 0
    public var isObserving: Bool = false

    // Internal task that drives the async notifications loop
    private var listenTask: Task<Void, Never>?

    public init() {}

    // Start listening for keyboard notifications using async/await
    public func startListening() {
        // Avoid starting twice
        guard listenTask == nil else { return }
        isObserving = true

        // Create a Task to consume the async sequence from NotificationCenter
        listenTask = Task { [weak self] in
            // Use NotificationCenter's async sequence
            let center = NotificationCenter.default
            // Merge willShow/willHide to compute height; stop task if cancelled
            for await note in center.notifications(named: UIResponder.keyboardWillChangeFrameNotification) {
                guard let self = self else { break }
                // Extract keyboard end frame height and update on main actor
                if Task.isCancelled { break }
                if let frameValue = note.userInfo?[UIResponder.keyboardFrameEndUserInfoKey] as? CGRect {
                    await MainActor.run {
                        self.keyboardHeight = frameValue.height
                    }
                }
            }
            // Clean up state when loop ends
            await MainActor.run {
                self.isObserving = false
            }
        }
    }

    // Stop listening by cancelling the Task
    public func stopListening() {
        listenTask?.cancel()
        listenTask = nil
        isObserving = false
    }

    deinit {
        listenTask?.cancel()
    }
}

// SwiftUI view demonstrating the migrated observer usage
struct KeyboardObserverView: View {
    // Own the observable model
    @State private var model = KeyboardObserver()

    var body: some View {
        VStack(spacing: 16) {
            Text("Keyboard height: \(Int(model.keyboardHeight))")
                .font(.headline)

            HStack(spacing: 12) {
                Button(action: { model.startListening() }) {
                    Text("Start Observing")
                }
                .disabled(model.isObserving)

                Button(action: { model.stopListening() }) {
                    Text("Stop Observing")
                }
                .disabled(!model.isObserving)
            }

            Spacer()

            // A TextField to trigger keyboard on screen
            TextField("Tap to show keyboard", text: .constant(""))
                .textFieldStyle(.roundedBorder)
                .padding()
        }
        .padding()
        .onAppear {
            // Optionally start automatically
            model.startListening()
        }
        .onDisappear {
            model.stopListening()
        }
        // Adjust layout to demonstrate keyboard height impact
        .padding(.bottom, model.keyboardHeight)
        .animation(.interactiveSpring(), value: model.keyboardHeight)
    }
}

// Preview for Xcode canvas
struct KeyboardObserverView_Previews: PreviewProvider {
    static var previews: some View {
        KeyboardObserverView()
    }
}
```

## References

- No verified external references were available this run.
