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
