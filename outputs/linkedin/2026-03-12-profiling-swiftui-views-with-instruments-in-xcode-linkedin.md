Your SwiftUI screen stutters — don’t guess, profile. Use Xcode Instruments and os_signpost to find the real hotspot, then make surgical fixes that preserve SwiftUI’s productivity.

Practical takeaways:
- Capture short, repeatable on-device traces with Xcode Instruments (Time Profiler + Core Animation). Add GPU/Metal captures when you suspect rendering or GPU work.
- Correlate UI events with stacks using os_signpost so you can see which state changes drive View.body churn.
- Try local fixes first: reduce observed state, scope @StateObject lifetimes, or use stable ids before considering a larger rewrite.
- Validate changes on representative devices and roll out cautiously (feature flags are useful for phased rollouts).

Tradeoff guidance: favor small, local changes when they reduce cost and risk. Consider a UIKit-backed approach only when profiling shows SwiftUI abstractions are the primary source of unacceptable runtime cost.

Example os_signpost usage:
import os
os_signpost(.begin, log: log, name: "ImageDecode", signpostID: id)

Where do you usually start when a SwiftUI screen regresses — profiling or code rollback? Share approaches that worked in your apps.

#iOSDev #SwiftUI #Performance #XcodeInstruments #Observability
