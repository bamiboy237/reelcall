# Design references and screenshots

Captured on 2026-10-22 in local headless Google Chrome using Playwright.
All images use device scale factor 1. No private footage was uploaded.
Reference pages may change after this date.

| File | Source | Viewport |
| --- | --- | --- |
| `zed-1440.png` | https://zed.dev | 1440 × 1200, including its page-level scroll container |
| `linear-1440.png` | https://linear.app | 1440 × 1000, full document |
| `ghostty-1440.png` | https://ghostty.org | 1440 × 1000, full document |
| `before-1440.png` | Local `web/` at `2f4fa1a` | 1440 × 1000, full document |
| `before-375.png` | Local `web/` at `2f4fa1a` | 375 × 812, full document, touch emulation |
| `pass1-1440.png`, `pass1-375.png` | First composition study | Same layout widths; see the frame limitation below |
| `ghostty-pass-1440.png`, `ghostty-pass-375.png` | Ghostty terminal pass, before PR polish | Same viewports as the before shots, full document |
| `final-1440.png`, `final-375.png` | Final spacing polish after PR review | Same viewports as the before shots, full documents of 1440 × 2044 and 375 × 2013 |
| `search-1440.png`, `search-375.png` | Normal-motion search result | Terminal crop at each layout width |

The before and final page comparisons use reduced motion to hold the same
paused playback scene. The real clip is decoded and paused at 2.5 seconds.
This avoids comparing different points in the automatic demo. Separate
normal-motion captures show the search state and timeline behavior.

The local capture server supports HTTP byte ranges. Each matched capture checks
the actual layout width, decoded video frames, paused state, and playback time.
The first composition study used a server without byte-range support. Its video
seek did not hold. Keep those images as design history, not matched-frame evidence.

These are research and review artifacts. They are not served by `web/`.
Desktop browser emulation does not replace a check on a physical phone.
