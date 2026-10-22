# Reelcall look and feel

## Scope

This is a visual redesign of `web/` for a personal project shown to startup
founders. Keep the copy, section order, install commands, footage, and demo
sequence. Keep static HTML, CSS, vanilla JavaScript, and self-hosted fonts.
Base: `2f4fa1a`, including the slower demo timing and removal of Docs links.

## Directions considered

| Direction | Treatment | Decision |
| --- | --- | --- |
| Screening room | Warm charcoal, ivory serif headline, recessed product surface, timecode rail | **Choose.** The moving search playhead makes the visual identity specific to video search. |
| Edit suite | Denser sans-serif layout, stronger grid, flatter controls | Clear, but too close to a generic developer-tool page. |
| Archive print | Sepia surfaces, editorial rules, oversized italic type | Warm, but the print metaphor competes with moving footage. |

## Screening room

- **Palette:** canvas `#191715`, surface `#211e1b`, inset `#151311`,
  ink `#f1e9dc`, muted `#b8aea1`, peach `#efa98a`.
- Derive borders and highlights from the same warm ink. Do not override the
  palette inside the demo. Peach belongs only to Install, the caret, the active
  search result, and the playhead. Focus and copy feedback stay neutral.
- **Type:** self-hosted IBM Plex Serif italic for the headline, 64px desktop,
  tight tracking and balanced lines. IBM Plex Sans for navigation, questions,
  descriptions, and search language. Iosevka for commands, paths, filenames,
  timecodes, and key hints.
- **Terminal:** follow the requested Ghostty reference with a single dark
  surface, a thin frame, a compact centered window title, and unboxed command
  text. Native red, yellow, and green window dots are the only exception to the
  single-accent rule. They are decorative, not working window controls.
- **Composition:** left-aligned headline and install block within a 960px text
  column; a 1120px demo extends beyond it. Use an 8px spacing rhythm.
- **Signature:** a timecode rail beneath the demo. It scans while the existing
  search runs and settles at the existing result's `0:24`. It is a visual
  readout, not an unimplemented draggable control.
- The second film cue is a very light, static SVG-noise grain. No sprockets,
  clapperboards, animated light leaks, or extra labels.
- Use layered, soft shadows and a thin inner top highlight. A bounded warm
  light behind the demo connects it to the page instead of framing a separate
  brown terminal inside a green site.
- Keep the FAQ open. Reduce its visual weight with sans-serif questions and
  a quiet two-column reading rhythm on desktop. Stack it on mobile.

## Motion contract

- One short entrance sequence. Only transform and opacity animate.
- UI feedback stays below 300ms, using `cubic-bezier(0.23, 1, 0.32, 1)`.
- The long progress fill and search scan explain the existing demo work.
  They are not UI response delays.
- Keep only one demo scene painted at a time.
- Keep hover changes inside `(hover: hover) and (pointer: fine)`.
- No motion for keyboard activation. Reduced motion shows the existing paused
  final scene, with the playhead already at `0:24`.
- Offscreen and hidden-tab stops also cancel the search scan.

## Reference study

See `design-refs/README.md` for sources and capture details.

- **Zed:** a real product artifact carries the page; serif type and a light
  texture can coexist with precise, useful interface detail. Borrow the clear
  composition, not its brand.
- **Linear:** large type, a calm continuous canvas, restrained interface edges,
  and strong changes in scale. Do not copy its many marketing sections.
- **Ghostty:** one terminal demonstration is the identity. The user requested
  its terminal treatment after reviewing the first pass. Use its flat window
  frame and native title-bar proportions, without copying its ASCII ghost,
  blue accent, or page layout.

## Capture and review plan

1. Save full-page before shots at real 1440px and 375px layout viewports.
2. Build the shared palette, type scale, surfaces, and timecode rail.
3. Inspect full-page and close-up renders at both widths. Exercise every demo
   scene, copy and tabs, pointer and keyboard input, and reduced motion.
4. Open a PR on `design/look-and-feel` with before and first-pass images.
5. Compare against the reference images. Record scores and a Before/After/Why
   table, apply one polish batch, and save final shots at the same widths.
6. Push the polish and final evidence to the same PR. Do not merge or deploy.

## Local design tools

Installed Impeccable into `.claude/` with hooks disabled. Installed the
`emil-design-eng`, `animate`, `review-animations`, and `mobile-native` skills
from `emilkowalski/skills`. Downloaded Anthropic's frontend-design skill from
`anthropics/claude-code`. These tools are local, ignored, and not website
dependencies. Their instructions can be read in this session; a Claude Code
restart is only needed to discover their slash commands.

The supplied brief already defines the audience, stack, content constraints,
direction, and review workflow. This file records those facts without adding
an unrelated product interview or changing persistent agent settings.

## Checks before the PR

- `uv sync --locked`, `uv run ruff format --check .`, `uv run ruff check .`,
  `uv run ty check`, and `uv build` passed.
- `node --check web/app.js` and `git diff --check` passed.
- Local Chrome checks passed at 1440px and 375px: the four-scene sequence,
  moving search scan, `0:24` result, keyboard and touch activation, decoded
  playback, offscreen pause, restart, both clipboard commands, and copy feedback.
- Reduced motion shows a paused final scene with no running animations.
- Layout widths from 320px to 1920px have no horizontal page overflow.
  Mobile install commands wrap without clipping. A 720px reflow check passed,
  but it is not a native browser zoom check.
- No browser script errors or failed HTTP responses occurred during these checks.

These are local checks. Physical-phone, Safari, assistive-technology, CI, and
production checks have not run at this stage. No merge or deployment is planned.

## Post-PR visual review

Reviewed the Ghostty pass after opening PR #14. Used the Impeccable and Emil
guidance in one context. No second agent reviewed this work. These scores are
design judgments, not user-study results.

| Area | Before | After | Why |
| --- | --- | --- | --- |
| Type and scale | 8/10 | 8/10 | Keep the balanced 64px desktop and 36px mobile headline. Preserve all wording. |
| Color | 8/10 | 8/10 | Keep shared warm neutrals and peach feedback. Native window dots remain the documented exception. |
| Depth and light | 8/10 | 8/10 | Keep soft layered shadows, bounded warm light, and static grain. |
| Rhythm | 7/10 | 8/10 | Reduce desktop hero padding from 72px/64px to 64px/48px. Reduce screen gaps from 24px to 16px and padding from 32px to 24px. |
| Signature detail | 7/10 | 8/10 | Reduce the desktop screen from 408px to 376px, video maximum width from 480px to 464px, caption gap from 16px to 8px, and rail padding from 24px/16px to 16px/8px. The complete rail now fits in the initial 1440 × 1000 view. |
| Motion | 8/10 | 8/10 | Keep the demo timing, transform and opacity feedback, instant keyboard activation, and static reduced-motion scene. |
| Mobile layout | 8/10 | 8/10 | Keep the mobile headline and 304px demo screen. Both full install commands wrap without clipping at 375px. |
| Non-template character | 8/10 | 8/10 | Keep the real clip, product-specific search rail, and Ghostty-style frame. Add no marketing sections. |

The desktop terminal bottom moved from 1058px to 986px. The timeline remains
visible without an initial scroll at 1440 × 1000. Mobile rail padding is 16px
shorter in total. The mobile headline, screen size, and video width are unchanged.

### Detector findings

Ran the Impeccable detector after the first visual review. It exited with code 2
and reported four findings:

- **Italic serif display:** intentional. The brief calls for this editorial headline.
- **Cramped install padding:** false positive on the parent wrapper. The child
  layout gives the command text 25px side insets on desktop and 17px on mobile.
- **Cramped FAQ padding:** false positive. Each bordered row has 24px bottom
  padding. The last row has no bottom border.
- **Repeating stripes:** intentional timeline measurement ticks, not a decorative
  striped background.

The review used screenshots and computed layout values, not a live overlay.

### Final local evidence

- Repeated the formatting, lint, type, package-build, JavaScript syntax, and
  whitespace checks after the spacing changes. All passed.
- Repeated desktop and touch-mobile browser checks for the search result,
  `0:24` landing, keyboard or touch activation, decoded playback, both clipboard
  commands, reduced motion, and widths from 320px to 1920px. All passed.
- All four scene layouts fit inside the final screen. Keyboard focus remains
  visible. Primary navigation, install tabs, and copy controls are 44px high.
- Checked the specified text-color pairs against their surface colors. The lowest
  checked ratio is 5.93:1. This is not a complete accessibility audit.
- Saved final full-page images and normal-motion search crops in `design-refs/`.
  The matched before and final playback captures use the same decoded frame.

The physical-phone, Safari, assistive-technology, and native browser zoom checks
remain open. CI status is reported on PR #14. No merge or deployment is included.
