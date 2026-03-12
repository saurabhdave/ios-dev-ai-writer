Scrolling looks fine in Simulator — then the real device stutters. Sound familiar? If your app ships long, frequently-updated lists, small SwiftUI changes can often reduce visible jank without a full rewrite. 🚀

I published a concise playbook for SwiftUI List rendering focused on stabilizing IDs, shrinking view bodies, using Equatable where helpful, and being deliberate about lazy loading and images. These are targeted, testable tweaks you can iterate on and validate on real hardware.

Practical takeaways:
- Prefer stable, model-backed ids (avoid generating a new UUID() inside the view) so SwiftUI can retain row state.
- Keep view bodies minimal; move parsing/formatting off the main thread into view models where feasible.
- Use EquatableView or equatable view-models to skip unnecessary re-renders when items truly haven’t changed.
- Choose List vs ScrollView+LazyVStack based on needed behaviors, and profile on representative devices.
- Decode/resize images off the main thread and cache at the render size to reduce work during rendering.

Example reminder:
let items = feedItems // model-backed ids
List(items, id: \.id) { item in
    FeedRowView(item: item)
}

Profile first (Instruments), apply changes incrementally, and validate on real devices. These are low-risk, reviewable adjustments that can improve perceived performance.

Which of these have helped you most? I can share a checklist or review a small code snippet if you want feedback. 👇

#iOS #SwiftUI #MobilePerformance #AppArchitecture #iOSDev #Swift
