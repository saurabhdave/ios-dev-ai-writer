# Migrate Delegates to Swift Concurrency in UIKit

Delegation is everywhere in UIKit: data sources, permission prompts, coordination between views and controllers. Swift Concurrency (async/await, AsyncSequence, Task) can give clearer control flow, structured cancellation, and safer composition — but converting a large codebase from delegates requires concrete choices, compatibility planning, and observability. Below is a practical migration guide for shipping this safely in production.

## Why This Matters for iOS Teams Right Now

Many platform and third‑party APIs now offer async APIs, and teams are adopting Task and AsyncSequence in new code. Replacing delegates can reduce nested callbacks and make cancellation semantics more explicit, which can simplify maintenance.

Migration is an architectural change: do it incrementally, preserve behavior, and add telemetry. Expect friction around Objective‑C interoperability, main‑thread requirements for UIKit, and rollout coordination across modules.

> Migrate incrementally: keep behavior identical, add observability, and gate changes behind rollout flags.

## 1. Replace SIMPLE Delegates with Continuations or AsyncStream

### Concrete approach (APIs)
- Use withCheckedContinuation / withCheckedThrowingContinuation for single‑response delegates (permission prompts, authentication callbacks).
- Use AsyncStream / AsyncThrowingStream for repeated events (location updates, audio meters).

### When to choose this
- Use continuations when a delegate maps to one response and the lifetime is short.
- Use AsyncStream when the delegate emits an ordered stream of events and the consumer benefits from async iteration and cancellation.
- Keep the delegate when the callback must run synchronously inside a run loop microtask or when third‑party Objective‑C code requires the delegate API.

### Operational and testing notes
- Continuations should be resumed exactly once. Add debug assertions and unit tests using XCTest async to verify resume and timeout behavior.
- Resume continuations on the main actor when adapting UIKit‑facing code (use @MainActor or Task { @MainActor in … }).
- Implement a timeout path (for example with Task.sleep) to avoid leaked continuations in failure modes.

## 2. Convert LIFECYCLE‑HEAVY Delegates into AsyncSequence + Structured Tasks

### Concrete approach (APIs & patterns)
- Expose long‑lived delegate output as an AsyncStream and consume it from a Task scoped to the view controller’s lifecycle (Task { @MainActor in … }).
- For background work that must not touch UIKit, use Task.detached; otherwise prefer Tasks bound to an actor or to @MainActor where UIKit access is needed.

### When to choose this
- Choose AsyncSequence + structured Tasks when you need explicit cancellation tied to lifecycle events (viewWillDisappear / deinit).
- Retain synchronous delegates when immediate synchronous UI changes are required (for example, layout invalidation during animation).

### Operational and testing notes
- Cancel Tasks in viewWillDisappear and in deinit. Write unit tests that assert cancellation behavior and that streams close correctly.
- Use bufferingPolicy (for example .bufferingNewest(1)) to avoid unbounded queues in high‑rate streams.
- Monitor retained Tasks and streams with runtime tooling (Allocations, Leaks) and add runtime logging of Task creation/termination for debugging.

## 3. Interop and BACKWARD COMPATIBILITY with Objective‑C and Older Modules

### Concrete approach (APIs & compatibility)
- Offer dual APIs: keep the delegate interface while adding an async wrapper that uses the delegate internally.
- Mark legacy delegate APIs as deprecated when you are ready to guide callers toward async alternatives, and document migration paths in code comments.
- Where the codebase already uses Combine, consider exposing a Combine publisher as an interim bridge to async code.

### When to choose this
- Keep both delegate + async when other modules or external SDKs still depend on the delegate.
- Convert to async‑only when the module is internal, versioned, and you can coordinate dependent consumers.

### Operational and testing notes
- Preserve Objective‑C compatibility where consumers expect @objc APIs.
- Track adoption with compile‑time diagnostics where possible and with runtime telemetry for which path (delegate vs async) is used.
- Provide clear migration notes and examples in library or framework docs.

## 4. Tradeoffs and COMMON PITFALLS

### Concrete tradeoffs
- Synchronous predictability vs. structured concurrency: delegates often run synchronously and preserve immediate ordering. Continuations introduce yield points; if ordering and synchronous behavior matter, resume the continuation on the expected actor or queue.
- Debuggability: async call stacks and Tasks add new dimensions to traces. Use logging and Task labels to surface context.

### Common pitfalls and operational notes
- Double‑resuming or never‑resuming continuations — add debug‑only assertions and unit tests that simulate cancellation and error paths.
- Forgetting main‑thread resumes for UIKit mutations — use @MainActor, Task { @MainActor in … }, or explicit DispatchQueue.main.async where necessary.
- Long‑lived AsyncStreams without cancellation — ensure producers check Task.isCancelled and close streams on lifecycle teardown.
- Observability for async flows: log Task identifiers, stream identifiers, and transition points so you can link runtime traces back to code paths.

## 5. Validation, TESTING, AND OPERATIONS

### Concrete tools and tests
- Unit tests: XCTest supports async/await. Assert ordering, cancellations, timeouts, and that continuations are resumed once.
- Runtime observability: use OSLog for Task lifecycle and use profiling tools (Allocations, Leaks) to detect retained Tasks or leaked continuations.
- End‑to‑end: include device‑based smoke tests that exercise migration surfaces under realistic latencies.

### When to gate and rollout
- Use staged rollouts / feature flags when customer‑facing flows or cross‑module dependencies exist.
- Convert immediately only when you control the entire stack and tests cover failure modes.

### Operational notes
- Add runtime metrics for error rates and latencies on the new async paths. Compare against delegate paths during rollout.
- Expand logging during the rollout window to include Task and stream identifiers for traceability.

## Checklist for Migration (practical, copyable)
- Inventory delegates: classify as single‑response | event stream | lifecycle‑heavy.
- Single‑response: implement withCheckedContinuation(+Throwing), add timeout, unit test.
- Streams: implement AsyncStream/AsyncThrowingStream with bounded buffering, support cancellation on lifecycle events.
- Dual API: provide delegate + async, mark delegate deprecated with availability attributes when appropriate.
- Tests: add XCTest async tests for ordering, cancellation, and timeouts.
- Observability: add OSLog for Task lifecycle and use profiling tools to detect leaks.
- Rollout: gate changes with feature flags and phased client updates.

Closing takeaway

Migrating delegates to Swift Concurrency can make control flow and cancellation clearer, but it requires discipline: pick continuations for single responses, AsyncSequence + Tasks for streams, and preserve dual APIs while you roll forward. Back changes with async XCTest assertions, runtime profiling, and logging. Make threading and ownership assumptions explicit in small experiments before wide rollout; where available, consider newer Swift state‑management features (for example Swift’s observation models) as complementary tools for view‑model synchronization.

## Swift/SwiftUI Code Example

```swift
import SwiftUI
import UIKit
import Observation

// MARK: - Async UIImagePicker wrapper using continuation + UIKit delegate

enum ImagePickerError: Error {
    case cancelled
    case noImage
    case presentationFailed
}

final class ImagePickerCoordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
    var continuation: CheckedContinuation<UIImage, Error>?

    // Allow creating a coordinator without a continuation for temporary attachment.
    init(continuation: CheckedContinuation<UIImage, Error>? = nil) {
        self.continuation = continuation
    }

    func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
        continuation?.resume(throwing: ImagePickerError.cancelled)
        continuation = nil
        picker.dismiss(animated: true)
    }

    func imagePickerController(_ picker: UIImagePickerController, didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey : Any]) {
        defer {
            continuation = nil
            picker.dismiss(animated: true)
        }

        if let image = info[.originalImage] as? UIImage {
            continuation?.resume(returning: image)
        } else {
            continuation?.resume(throwing: ImagePickerError.noImage)
        }
    }
}

actor PickerPresenter {
    // Presents a view controller on the main actor and returns a coordinator bound to it.
    func presentImagePicker(on presenting: UIViewController) async throws -> (UIImagePickerController, ImagePickerCoordinator) {
        try await MainActor.run {
            guard UIImagePickerController.isSourceTypeAvailable(.photoLibrary) else {
                throw ImagePickerError.presentationFailed
            }
            let picker = UIImagePickerController()
            picker.sourceType = .photoLibrary
            picker.modalPresentationStyle = .fullScreen
            return (picker, ImagePickerCoordinatorWrapper.wrap(picker))
        }
    }
}

// Helper to create a coordinator when we don't yet have the continuation.
// We need to attach the real coordinator later when starting the async operation.
fileprivate enum ImagePickerCoordinatorWrapper {
    static func wrap(_ picker: UIImagePickerController) -> ImagePickerCoordinator {
        // Temporary coordinator with nil continuation; will be replaced by the real one
        let dummy = ImagePickerCoordinator()
        picker.delegate = dummy
        return dummy
    }
}

// MARK: - Public async API

struct UIKitImagePicker {
    // Presents a system UIImagePickerController and returns the chosen UIImage, using Swift concurrency.
    // Must be called from any thread; presentation happens on the main actor.
    static func pickImage(from presentingViewController: UIViewController) async throws -> UIImage {
        return try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<UIImage, Error>) in
            Task { @MainActor in
                guard UIImagePickerController.isSourceTypeAvailable(.photoLibrary) else {
                    continuation.resume(throwing: ImagePickerError.presentationFailed)
                    return
                }

                let picker = UIImagePickerController()
                picker.sourceType = .photoLibrary
                picker.modalPresentationStyle = .fullScreen

                // Create a coordinator that captures the continuation
                let coordinator = ImagePickerCoordinator(continuation: continuation)
                picker.delegate = coordinator

                // Present
                presentingViewController.present(picker, animated: true)
            }
        }
    }

    // Convenience: obtains a top-most view controller and calls pickImage
    static func pickImageFromTopController() async throws -> UIImage {
        guard let top = UIApplication.shared.connectedScenes
                .compactMap({ $0 as? UIWindowScene })
                .flatMap({ $0.windows })
                .first(where: { $0.isKeyWindow })?.rootViewController else {
            throw ImagePickerError.presentationFailed
        }

        // Find the top-most presented view controller
        var presenting = top
        while let presented = presenting.presentedViewController {
            presenting = presented
        }

        return try await pickImage(from: presenting)
    }
}

// MARK: - Observable model using Swift Observation

@Observable
final class PhotoPickerModel {
    var selectedImage: UIImage?
    var isLoading: Bool = false
    var lastErrorMessage: String?

    // Async method that uses the UIKitImagePicker; keeps UI-state consistent
    func pickPhoto() async {
        isLoading = true
        lastErrorMessage = nil
        defer { isLoading = false }

        do {
            let image = try await UIKitImagePicker.pickImageFromTopController()
            selectedImage = image
        } catch {
            switch error {
            case ImagePickerError.cancelled:
                lastErrorMessage = "Selection cancelled."
            case ImagePickerError.noImage:
                lastErrorMessage = "No image was selected."
            case ImagePickerError.presentationFailed:
                lastErrorMessage = "Failed to present image picker."
            default:
                lastErrorMessage = error.localizedDescription
            }
        }
    }
}
```

## References

- No verified external references were available this run.
