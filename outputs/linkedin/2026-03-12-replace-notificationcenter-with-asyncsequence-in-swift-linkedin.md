Swap a NotificationCenter observer for an AsyncSequence when you want explicit ownership and cancellation of a subscription — it can make Task lifecycles clearer and reduce callback inversion across view controllers and actors.

Practical takeaways
- Use AsyncStream / AsyncThrowingStream to bridge NotificationCenter when a single Task owns the subscription.
- Tie the stream lifetime to the owning Task or component and finish the continuation (or remove the observer) in onTermination.
- Keep NotificationCenter for lightweight multicast or system broadcasts (e.g., UIApplication notifications) and for many independent listeners.
- Add async XCTest checks and logging (os_log / signposts) around the bridge while rolling it out to observe behavior.

Tradeoff / decision point
- Converting per-subscriber observers into per-subscriber AsyncStreams can increase work and affect latency for high-frequency events. For hot, high-rate streams, prefer a centralized actor or publisher that fans out to consumers.

Small Swift example (bridge pattern)
AsyncStream<Notification> { continuation in
 forName: .someNotification, object: nil, queue: nil
 ) { notification in
 continuation.yield(notification)

 continuation.onTermination = { _ in
 NotificationCenter.default.removeObserver(obs)

Have you used this pattern in your app or gated it behind a feature flag? I’d love to hear lessons learned.

#iOSDev #Swift #Architecture #Concurrency #Testing
