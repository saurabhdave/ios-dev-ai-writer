NotificationCenter observers are a frequent source of lifecycle bugs — make their cancellation and ownership explicit.

I wrote a short guide for teams migrating observers to Swift Concurrency. The payoff can include explicit cancellation, easier testing, and clearer ownership — but there are tradeoffs to weigh.

Key points:
- Replace addObserver(selector:) or addObserver(forName:object:queue:using:) patterns with NotificationCenter.notifications(named:) and consume the AsyncSequence from a Task you cancel from the owner.
- Run handler work on an appropriate actor (or MainActor) to avoid data races when touching shared state.
- Keep Combine for operator-heavy pipelines (debounce, map, buffer); use AsyncSequence/AsyncStream when simple async iteration or custom buffering fits better.
- During rollout, monitor behavior and resource use — e.g., track active tasks and observe memory/retain patterns to catch leaks early.

Tradeoff to call out: AsyncSequence-based handlers simplify cancellation and linear async code, but they don’t provide Combine’s built-in operator library — if your implementation relies on complex reactive transforms, you may keep Combine or bridge specific pieces with AsyncStream.

Small illustrative pattern:
for await notification in NotificationCenter.default.notifications(named: .keyboardWillChangeFrame) {
 await MainActor.run { /* update state */ }

Have you started migrating observers in your app? I’d love to hear patterns that worked (or didn’t) for your teams.

#iOS #Swift #Architecture #SwiftConcurrency #Observability
