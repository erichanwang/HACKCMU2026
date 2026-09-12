---
name: PackAR
description: A white, hairline-ruled measuring instrument with one orange signal — camera chrome that stays out of the bag's way.
colors:
  paper: "#FFFFFF"
  card: "#F9F9FA"
  ink: "#1A1F2B"
  hairline: "#1A1F2B1A"
  accent: "#D6541F"
  warn: "#B37108"
typography:
  title:
    fontFamily: "SF Pro (system), -apple-system"
    fontSize: "20pt"
    fontWeight: 600
    lineHeight: "default"
    letterSpacing: "normal"
  body:
    fontFamily: "SF Pro (system), -apple-system"
    fontSize: "17pt"
    fontWeight: 400
    lineHeight: "default"
    letterSpacing: "normal"
  body-emphasis:
    fontFamily: "SF Pro (system), -apple-system"
    fontSize: "17pt"
    fontWeight: 500
    lineHeight: "default"
    letterSpacing: "normal"
  subheadline:
    fontFamily: "SF Pro (system), -apple-system"
    fontSize: "15pt"
    fontWeight: 500
    lineHeight: "default"
    letterSpacing: "normal"
  footnote:
    fontFamily: "SF Pro (system), -apple-system"
    fontSize: "13pt"
    fontWeight: 400
    lineHeight: "default"
    letterSpacing: "normal"
  caption:
    fontFamily: "SF Pro (system), -apple-system"
    fontSize: "12pt"
    fontWeight: 400
    lineHeight: "default"
    letterSpacing: "normal"
  measurement:
    fontFamily: "SF Mono (system monospaced), ui-monospace"
    fontSize: "12pt"
    fontWeight: 400
    lineHeight: "default"
    letterSpacing: "normal"
    fontFeature: "tabular figures"
rounded:
  sm: "8pt"
  md: "15pt"
  lg: "22pt"
  xl: "24pt"
  pill: "9999pt"
spacing:
  xs: "4pt"
  sm: "8pt"
  md: "14pt"
  lg: "18pt"
  xl: "22pt"
components:
  card-bag:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "18pt"
  card-inventory:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "9pt 11pt"
    width: "104pt"
    height: "88pt"
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.paper}"
    rounded: "{rounded.pill}"
    width: "58pt"
    height: "44pt"
  button-primary-disabled:
    backgroundColor: "{colors.hairline}"
    textColor: "{colors.paper}"
    rounded: "{rounded.pill}"
    height: "44pt"
  button-secondary:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    typography: "{typography.subheadline}"
    rounded: "{rounded.pill}"
    height: "44pt"
  icon-stamp:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    size: "30pt"
  panel-overlay:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.xl}"
    padding: "16pt"
---

# Design System: PackAR

## Overview

**Creative North Star: "The Good Tape Measure"**

PackAR is a measuring instrument, not an app about travel. Everything is on one white ground — the landing page, the lists, the plan, and the chrome floating over the live camera feed. There is no dark variant and no warm paper: `.preferredColorScheme(.light)` is set once on the TabView and every surface inherits it. Depth comes from a 10%-ink hairline and a barely-tinted card fill, never from a shadow. The interface has almost no colour of its own, so the one orange it does carry reads as a signal rather than as decoration.

Density is low and targets are large, because the user is holding a phone over an open suitcase with both hands busy. Detail lives in sheets and menus; the always-on surfaces carry one status line, one primary action, and honest figures. Every measurement and count is set in tabular or monospaced figures so numbers hold their columns as they change, and nothing is rounded to look better.

The world was chosen against specific alternatives and those refusals are load-bearing: the hero-metric dashboard, the pastel glyph chip, the drop-shadowed card on a grey ground, and the printed load-sheet vocabulary of tracked uppercase stencil labels all got built at some point and all got removed. The landing page is the clearest statement of the result — it shows a connection dot, a breathing lens, and one sentence, and it starts the work with a drag instead of reporting on it.

**Key Characteristics:**
- One white ground everywhere, camera tabs included; no dark chrome, no warm paper.
- A single accent (signal orange) plus a single warn amber; nothing else is coloured.
- Hairlines and tonal fills instead of shadows — zero drop shadows in the app.
- Sentence case, SF throughout, no tracked caps anywhere.
- Figures are always tabular; monospace is reserved for measurements.
- Motion is ambient and gestural, and every bit of it is gated on Reduce Motion.

## Colors

A near-achromatic palette of white, near-black ink and one warm signal; the whole hue budget of the app is spent on the orange.

### Primary
- **Signal Orange** (`{colors.accent}`): the app's only accent. Tab bar tint, the lens and its two haloes on the landing page, the play button that runs the solver, the load bar under capacity, the selected tick in the bag contents sheet, the inventory-count badge, the load percentage. It marks the thing you are about to act on or the number you came to read, and nothing else.

### Secondary
- **Warn Amber** (`{colors.warn}`): the over-capacity and failed-request signal only. It replaces the orange in the load bar and the percentage when a bag holds more than it can close on, and it colours the status row's warning triangle on the Pack tab. It is never used for emphasis.

### Neutral
- **Paper** (`{colors.paper}`): the ground of every screen, and — at 92–94% opacity — the ground of the chrome panels floating over the live camera feed.
- **Card** (`{colors.card}`): the barely-there fill that separates a card from the paper behind it. Used at full strength on inventory cards and secondary buttons, and at 55% on the Pack tab's bag cards.
- **Ink** (`{colors.ink}`): all text, all glyphs. Secondary and tertiary text are the same ink at reduced opacity — 0.7 for supporting sentences, 0.5–0.6 for metadata, 0.4 for the quietest hint, 0.25 for a disabled dot.
- **Hairline** (`{colors.hairline}`): ink at 10%, drawn at 0.5–0.75pt. This is the app's only edge. Structural rules inside components (the load bar's track at 7% ink, its limit tick at 30%) are the same ink, different opacity.

### Named Rules
**The One Signal Rule.** Orange means "act here" or "this is the number". If two orange things are visible at once on a surface, one of them is decoration and should be ink.

**The Opacity-Not-Grey Rule.** There is no grey token. Every quieter tone is `ink` at a lower opacity, so a single hue holds the whole neutral ramp and nothing drifts blue or warm against the white.

**The Colour-Is-Never-Alone Rule.** Over capacity is amber *and* says so in words ("over what the bag holds" in the VoiceOver label, "Didn't fit: …" in the status line). A failed request is amber *and* a triangle *and* a sentence.

## Typography

**Display Font:** none — the app has no display face. The largest type in the product is a 20pt semibold title.
**Body Font:** SF Pro, the system face, at Dynamic Type sizes throughout.
**Label/Mono Font:** SF Mono, the system monospaced face, for measurements only.

**Character:** Plain, sentence case, and unstyled on purpose. The type does no work the numbers cannot do; its only expressive move is the switch to monospace when a value is a measurement.

### Hierarchy
- **Title** (semibold, `.title3` 20pt): a bag's name on its card, the landing page's "Swipe up to scan" (medium weight), the empty-state headline. The ceiling of the ramp.
- **Body** (regular/medium 17pt): item names in lists, the label field, list rows.
- **Subheadline** (medium/semibold 15pt): the scan panel's status line, the secondary button label, the load percentage (with tabular figures).
- **Footnote** (regular 13pt): status lines, one-line explanations, the landing page's connection state, inventory heading.
- **Caption / Caption2** (regular 12pt / 11pt): metadata under a row, size and source lines, the quietest provenance text.
- **Measurement** (monospaced, caption or footnote size): `74×41×24` in list rows and inventory cards, layer captions in the 3D scene, the server address in the mock-plan banner.

### Named Rules
**The Tabular Figures Rule.** Any run of text containing a number uses `.monospacedDigit()` at minimum — counts, percentages, kilograms, compressibility. Numbers must not reflow their neighbours when they change.

**The Mono-Means-Measured Rule.** Full monospace is reserved for dimensions, addresses and layer indices. It is never applied for texture; a monospaced sentence that is not a measurement is a debug HUD.

**The Sentence Case Rule.** No tracked caps and no uppercase labels anywhere in the app. Titles, buttons, section headers and captions are all sentence case.

## Layout

Four tabs (Home, Scan, Items, Pack) with the standard iOS tab bar, tinted orange. Each tab owns its own NavigationStack; nothing is shared between them except the AR camera lock.

Screen gutters are 18pt on the Pack tab, 22pt on the landing page, and 12–16pt for chrome floating over the camera, where the panel's own rounded corner supplies the inset. Cards are padded 18pt; the camera panel 16pt; the inventory strip 13pt. Vertical rhythm inside a card runs on 2 / 8 / 14 / 16pt steps — a heading and its metadata sit 2pt apart, a bar is 14pt below the text it describes, and the action row is 16pt below the bar.

Lists are plain system `List`s with `.scrollContentBackground(.hidden)` over a paper background, so the list inherits the app's white rather than the system's grouped grey. Sheets use `.presentationDetents([.medium, .large])` throughout. Touch targets are 44pt minimum (the gear button, the play button, the secondary button, the close buttons), and negative padding is used to pull a 44pt target's optical edge back to the gutter rather than shrinking the target.

**The Detail-Lives-In-Sheets Rule.** The always-on surface carries one status line and one primary action. Everything else — contents, settings, item editing, the plan — opens in a sheet or a pushed screen.

## Elevation & Depth

**There are no drop shadows in this app.** Not one `shadow` modifier ships. Depth is expressed three ways: a 0.5pt hairline stroke at 10% ink, a card fill one step off white (`{colors.card}`, sometimes at 55% opacity), and — only over the live camera feed — a translucent paper panel at 92–94% opacity so the feed reads faintly through the chrome.

The landing page's ambient wash is the one atmospheric layer: two radial fields of accent at 16% and 9% opacity, 50pt blurred, drifting on mismatched multi-second cycles. It is a wash of light, not an object; it has no edge, no geometry, and no marks.

### Named Rules
**The No-Shadow Rule.** A surface separates from its ground by a hairline and a tonal step, never by a cast shadow. If a card needs a shadow to be legible, the ground is wrong.

**The Light-Chrome-Over-Camera Rule.** Chrome above a live camera feed stays on the app's white at 92–94% opacity with `.environment(\.colorScheme, .light)` forced. The camera tab is not a dark tab.

## Shapes

Continuous (superelliptical) corners everywhere a rectangle is rounded, and capsules for anything that reads as a control. The radius ramp in use: 8pt for the 30pt stamp icon, 15pt for the small inventory card, 22pt for full-width cards and the floating inventory strip, 24pt for the camera panel. Buttons and the load bar are capsules; the lens, the haloes, the connection dot and the tick marks are true circles.

Strokes are 0.5pt on cards and 0.75pt on the stamp icon, always in hairline ink, always as an `.overlay` on top of the fill so the edge stays crisp at the corner.

**The Continuous-Corner Rule.** Every `RoundedRectangle` uses `style: .continuous`. A circular-arc corner reads harder than this world allows.

## Components

### Buttons
- **Primary (the play button):** a 58×44pt accent capsule with a white `play.fill` glyph; it becomes an inline white `ProgressView` while its own bag is solving, so only the bag being packed spins. Disabled state is the same capsule at 18% ink rather than a greyed accent.
- **Secondary ("Add items"):** a full-width 44pt `{colors.card}` capsule with ink text and a leading `plus` glyph. No border, no shadow.
- **System buttons:** the Scan tab's action row uses stock `.borderedProminent` / `.bordered` at `.controlSize(.large)`, semibold, with `.monospacedDigit()` applied to the row so the item count does not jitter.
- **Icon-only buttons** are 44pt (or 48pt for the backpack toggle), carry `.contentShape(Rectangle())` so the whole frame is tappable, and always carry an `accessibilityLabel`.

### Cards / Containers
- **Corner Style:** 22pt continuous for full-width cards, 15pt for the small inventory card.
- **Background:** `{colors.card}` at 55% for bag cards, full strength for inventory cards, paper at 94% for panels over the camera.
- **Shadow Strategy:** none. See Elevation & Depth.
- **Border:** 0.5pt hairline overlay on bag cards and inventory cards; the camera panel and inventory strip carry no stroke (the translucency is their edge).
- **Internal Padding:** 18pt (bag card), 16pt (camera panel), 13pt (inventory strip), 9/11pt (inventory card).

### List rows
A 30pt stamp icon, then a two-line stack: item name at body-medium, and a monospaced caption of size and location. Rows are padded 3pt vertically over the system row. Swipe-to-delete is always accompanied by a tap route (a context menu, an Edit button, or a detail screen) and a confirmation dialog naming the item.

### Ticked rows (BagContentsSheet)
A row is a `.plain` button whose entire frame is the target. State is a 20pt `checkmark.circle.fill` in accent when in the bag, `circle` at 25% ink when not — plus a monospaced caption that says in words where the item currently is ("in this bag", "in another bag", "not in a bag"). The tick is never the only statement of state.

### StampIcon (signature)
An item's glyph as ink on paper inside a hairline field box: SF Symbol at 14pt regular, ink at 60%, in a 30pt frame with an 8pt continuous hairline stroke. Deliberately not a tinted chip — no fill, no colour, no circle. It is `accessibilityHidden(true)`; the row's text carries the meaning. The symbol itself comes from a keyword→SF Symbol table on the item label, first match wins, falling back to `shippingbox.fill` for anything unlabelled so an unknown item never gets a confidently wrong picture.

### LoadBar (signature)
An 8pt capsule track at 7% ink with an accent (or amber, over capacity) capsule fill, and a 1pt × 14pt ink tick at 30% marking the limit at 84% of the width. The limit mark sits inboard of the end on purpose: a bag over its capacity runs visibly into the overrun strip instead of stopping at full and looking fine. The whole bar is one accessibility element labelled "N percent by volume, over what the bag holds".

### InventoryStrip (signature)
A 22pt paper-at-94% panel floating above the scan panel, containing a heading row ("Inventory", a monospaced count, a close button) and a horizontally scrolling row of 104×88pt cards. Each card is `{colors.card}` at 15pt with a hairline stroke, carrying an accent glyph, a two-line name, and a monospaced size. It replaced a field of drifting concentric rings, which looked better standing still than they worked in the hand.

### The lens (signature, landing page)
Two accent haloes — 190pt at 6% and 138pt at 10% — around a 46pt light `viewfinder` glyph, breathing out of phase on a 2.6s autoreversing cycle. Under a drag the haloes brighten and grow, the glyph rotates up to 90°, and the supporting text fades; past a 96pt threshold the label switches to "Release". On commit the lens fills solid accent and scales 10× past the screen edges, hands over to the tab underneath at 280ms, and resets behind the swap. A tap on the lens does the same thing without the gesture.

### Navigation
Stock iOS tab bar tinted accent, inline navigation titles, and `EditButton` in the trailing toolbar slot when a list has content. Nothing about the navigation is custom.

## Do's and Don'ts

### Do:
- **Do** put every new surface on `{colors.paper}`, including anything drawn over the camera, and force `.environment(\.colorScheme, .light)` on chrome that floats over a live feed.
- **Do** separate surfaces with a 0.5pt hairline at 10% ink and a `{colors.card}` fill.
- **Do** use 22pt continuous corners for full-width cards and capsules for controls.
- **Do** spend the accent on the one thing the user is about to touch or the one number they came to read.
- **Do** set every number with `.monospacedDigit()`, and every measurement in full monospace.
- **Do** give every swipe action a tap equivalent and a named VoiceOver action, and label every icon-only control.
- **Do** hide decorative glyphs from VoiceOver (`accessibilityHidden(true)`) and let the adjacent text carry the meaning.
- **Do** gate every animation on `@Environment(\.accessibilityReduceMotion)` — the ambient wash pauses, the breathing stops, and the lens hand-off degrades to an immediate tab switch.
- **Do** say the state in words as well as in colour.

### Don't:
- **Don't** use pastel tinted glyph chips for item categories. The stamp icon is ink on paper inside a hairline box; a coloured chip per category turns an inventory into a toy and spends the accent budget on decoration.
- **Don't** build a hero-metric card — a giant percentage over a small label. It was tried on the landing page and removed: the number is already one tab away on Pack, at subheadline size next to the bag it describes, where it is actionable rather than impressive.
- **Don't** put drop-shadowed cards on a grey ground. This app has zero shadows and one white; a floating card on grey is the generic dashboard the world was chosen against.
- **Don't** use gradient text, ever. (Gradients exist in exactly one place — the ambient wash's radial light fields — and they carry no marks and no type.)
- **Don't** introduce dark chrome over the camera. The camera tab is the same white as every other tab.
- **Don't** set tracked uppercase stencil labels. The printed load-sheet vocabulary was built and removed; its restraint stayed, its print styling did not.
- **Don't** add decorative dot fields, ray bursts, or drifting radial arrangements of content. The concentric inventory rings were replaced by a scrolling row for exactly this reason: nothing about a packing inventory is radial.
- **Don't** add a second accent hue. Amber is the over-capacity signal, not a second brand colour.
- **Don't** make a drag the only route to an action.

## Known drift in the shipped build

Recorded as observed, not as rules to inherit:

- **The plan views are on a different palette.** `PlanSheet`'s mock-plan banner, `ScanScreen`'s status warning triangle, and the 3D scene's captions use the system `.orange` and `.secondary`/`.tertiary` hierarchies rather than `{colors.warn}` and ink opacities. `LayeredPlanSceneView`'s control panel uses `.regularMaterial` at a 12pt *circular* corner, and the 3D scene's background is `.secondarySystemBackground`. New surfaces should follow the `Sheet` palette, not this.
- **`.regularMaterial` on the backpack toggle** (ScanScreen) is the one other material surface; every other floating chrome uses paper at 92–94%.
- **The connection dot's "connected" state is SwiftUI's system `.green`**, the only hue in the app outside the two named signals.
- **Radii are not yet a closed set.** 8 / 12 / 15 / 16 / 22 / 24pt all ship. 22pt is the house value; 12 and 16 are older surfaces.
- **Secondary text is spelled two ways** — `Sheet.ink.opacity(0.5)` on the newer screens, `.foregroundStyle(.secondary)` on the older ones. They do not render identically.
- **`PlanSheet`'s 2D/3D flip button carries `.monospacedDigit()` on a label with no digits**, which is the one decorative use of a figure treatment in the build.
