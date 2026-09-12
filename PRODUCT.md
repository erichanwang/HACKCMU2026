# Product

## Register

product

## Users

A traveler packing a carry-on or a weight-limited checked bag, standing over an open suitcase
with items laid out beside it, phone in one hand. Secondary: gear owners (camera, dive, climbing)
with fragile kit in fixed cases. Hackathon judges watching a three-minute live demo are the
audience for the first release.

Their job: scan the bag, scan each item, get a plan that fits, and follow it with both hands
busy. Every screen is a step in that one flow.

## Product Purpose

PackAR scans a suitcase and its items with LiDAR, solves the packing arrangement server-side,
validates it physically, and shows where each item goes as a layered 2D diagram and as an AR
overlay inside the real bag. Success is a plan the user can actually follow that beats what they
would do by hand. An item that does not fit is reported by name, never hidden.

## Brand Personality

Measured, plain-spoken, confident. It reads like a good tape measure: exact numbers, no
flourish, no reassurance it cannot back. Camera-app chrome over a live feed; the bag and the
items are the content, the interface stays out of their way.

## Anti-references

- Debug HUDs: monospaced status dumps, point counts as the headline, four text buttons squeezed
  onto one line.
- Gamified travel and packing-list apps: confetti, mascots, streaks, emoji as icons.
- Generic AI-tool styling: gradient text, glass cards as decoration, purple-on-dark hero pages.
- Any layout that requires reading small text while holding the phone over a suitcase.

## Design Principles

- The tool disappears into the task. Familiar iOS affordances (segmented control, prominent
  button, sheets, system font). Nothing invented where a standard control exists.
- Report, never repair. Unfit items, stability warnings, labelling failures and server errors
  are shown where they happen, in plain words, with what to do next.
- Glanceable over a bag. One status line, one primary action, large targets. Detail lives in
  sheets, not in the always-on panel.
- The layer diagram is the product, AR is the demo. Nothing is gated behind AR working.
- Honest numbers. Dimensions and counts use tabular figures and one decimal; nothing is rounded
  to look better.

## Accessibility & Inclusion

iOS defaults as the floor: Dynamic Type on all text, 44 pt touch targets, VoiceOver labels on
icon-only buttons, colour never the only signal (issues carry a symbol and a sentence). AR is
optional; the 2D layer view is the accessible path and must work without camera permission.
