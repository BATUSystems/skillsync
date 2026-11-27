<!-- defaults -->
# Accessibility audit

Result: **FAIL** (exit 1). Mode: audit. Config: 000000000000.

Text in selectors, messages and notes is page data (untrusted), cleaned and shortened.

## Summary by rule

| rule | PASS | FAIL | EXCEPTED | CONFIRMED | UNVERIFIED | EXEMPT |
| --- | --- | --- | --- | --- | --- | --- |
| contrast | 4 | 2 | 0 | 0 | 9 | 3 |
| target-size | 4 | 2 | 0 | 0 | 0 | 1 |
| focus-keyboard-indicator | 2 | 1 | 0 | 0 | 1 | 0 |
| tab-positive-tabindex | 0 | 1 | 0 | 0 | 0 | 0 |
| tab-unreachable | 0 | 3 | 0 | 0 | 0 | 0 |
| console | 0 | 1 | 0 | 0 | 0 | 0 |

## Failures

- [contrast] page p: `#t-fail` measured ratio=2.169, fontSizePx=16, bold=false, large=false, backgroundKind=solid, required 4.5
- [contrast] page p: `#t-varying` measured ratio=2.715, fontSizePx=16, bold=false, large=false, backgroundKind=varying, required 4.5
- [target-size] page p: `#tiny` measured widthPx=16, heightPx=16, required 24
- [target-size] page p: `#tiny2` measured widthPx=16, heightPx=16, required 24
- [focus-keyboard-indicator] page p: `#noring` measured diffPx=0, focusVisible=false, required 4
- [tab-positive-tabindex] page p: `#edge` measured tabindex=2: tabindex="2" overrides the natural order
- [tab-unreachable] page p: `#tiny` measured : target is not reached by the Tab key
- [tab-unreachable] page p: `#tiny2` measured : target is not reached by the Tab key
- [tab-unreachable] page p: `#native` measured : target is not reached by the Tab key
- [console] page p: `console\|p\|b2a0d8a8101d` measured count=2: console.error: boom

## Unverified

- [contrast] page p: `#t-filter` (filter-or-blend)
- [contrast] page p: `#t-clipText` (background-clip-text)
- [contrast] page p: `#t-group` (opacity-group-with-paint)
- [contrast] page p: `#t-media` (media-background)
- [contrast] page p: `#t-important` (inline-important-color)
- [contrast] page p: `#t-autofill` (autofill)
- [contrast] page p: `#t-color` (color-unconvertible)
- [contrast] page p: `#t-occluded` (occluded)
- [contrast] page p: `#t-clipped` (clipped)
- [focus-keyboard-indicator] page p: `#offscreen` (clipped)

## Warnings

- warning frames-not-audited: page p: 1 frame(s): their content is not measured for contrast and target size (Tab does go through same-origin frames)
- warning shadow-not-audited: page p: 1 custom element(s) without readable content (closed shadow DOM is not audited; 0 open shadow root(s) were)
- warning tab-order-visual: page p: focus moves visually backwards (ltr) from #ring to #noring
- info tab-end: page p: Tab sequence ended by wrapped after 4 stop(s)


<!-- mouseAndOptions -->
# Accessibility audit

Result: **FAIL** (exit 1). Mode: audit. Config: 000000000000.

Text in selectors, messages and notes is page data (untrusted), cleaned and shortened.

## Summary by rule

| rule | PASS | FAIL | EXCEPTED | CONFIRMED | UNVERIFIED | EXEMPT |
| --- | --- | --- | --- | --- | --- | --- |
| contrast | 4 | 2 | 0 | 0 | 9 | 3 |
| target-size | 4 | 2 | 0 | 0 | 0 | 1 |
| focus-keyboard-indicator | 0 | 3 | 0 | 0 | 1 | 0 |
| tab-unreachable | 0 | 3 | 0 | 0 | 0 | 0 |
| tab-order-visual | 0 | 1 | 0 | 0 | 0 | 0 |
| focus-mouse-ring | 0 | 1 | 0 | 0 | 2 | 0 |
| console | 0 | 1 | 0 | 0 | 0 | 0 |

## Failures

- [contrast] page p: `#t-fail` measured ratio=2.169, fontSizePx=16, bold=false, large=false, backgroundKind=solid, required 3
- [contrast] page p: `#t-varying` measured ratio=2.715, fontSizePx=16, bold=false, large=false, backgroundKind=varying, required 3
- [target-size] page p: `#tiny` measured widthPx=16, heightPx=16, required 24
- [target-size] page p: `#tiny2` measured widthPx=16, heightPx=16, required 24
- [focus-keyboard-indicator] page p: `#ring` measured diffPx=2116, focusVisible=true, required 100000
- [focus-keyboard-indicator] page p: `#noring` measured diffPx=0, focusVisible=false, required 100000
- [focus-keyboard-indicator] page p: `#edge` measured diffPx=324, focusVisible=true, required 100000
- [tab-unreachable] page p: `#tiny` measured : target is not reached by the Tab key
- [tab-unreachable] page p: `#tiny2` measured : target is not reached by the Tab key
- [tab-unreachable] page p: `#native` measured : target is not reached by the Tab key
- [tab-order-visual] page p: `tab-order-visual\|p\|2df511bbcfd0` measured from=#ring, to=#noring: focus moves visually backwards (ltr) from #ring to #noring
- [focus-mouse-ring] page p: `#ring` measured diffPx=0, visible=false, required 100000
- [console] page p: `console\|p\|b2a0d8a8101d` measured count=2: console.error: boom

## Unverified

- [contrast] page p: `#t-filter` (filter-or-blend)
- [contrast] page p: `#t-clipText` (background-clip-text)
- [contrast] page p: `#t-group` (opacity-group-with-paint)
- [contrast] page p: `#t-media` (media-background)
- [contrast] page p: `#t-important` (inline-important-color)
- [contrast] page p: `#t-autofill` (autofill)
- [contrast] page p: `#t-color` (color-unconvertible)
- [contrast] page p: `#t-occluded` (occluded)
- [contrast] page p: `#t-clipped` (clipped)
- [focus-keyboard-indicator] page p: `#offscreen` (clipped)
- [focus-mouse-ring] page p: `focus-mouse-ring\|p\|8e7f7262fe76` (no-samples)
- [focus-mouse-ring] page p: `#ring` (occluded)

## Warnings

- warning threshold-lowered: threshold lowered (normal=3): fixture
- warning frames-not-audited: page p: 1 frame(s): their content is not measured for contrast and target size (Tab does go through same-origin frames)
- warning shadow-not-audited: page p: 1 custom element(s) without readable content (closed shadow DOM is not audited; 0 open shadow root(s) were)
- warning tab-positive-tabindex: page p: tabindex="2" overrides the natural order
- info tab-end: page p: Tab sequence ended by wrapped after 4 stop(s)
- warning mouse-occluded: page p: a configured mouse target is covered by another element and was not clicked


<!-- clean -->
# Accessibility audit

Result: **PASS** (exit 0). Mode: audit. Config: 000000000000.

Text in selectors, messages and notes is page data (untrusted), cleaned and shortened.

## Summary by rule

| rule | PASS | FAIL | EXCEPTED | CONFIRMED | UNVERIFIED | EXEMPT |
| --- | --- | --- | --- | --- | --- | --- |
| contrast | 1 | 0 | 0 | 0 | 0 | 0 |
| target-size | 1 | 0 | 0 | 0 | 0 | 0 |
| focus-keyboard-indicator | 1 | 0 | 0 | 0 | 0 | 0 |
| tab-unreachable | 1 | 0 | 0 | 0 | 0 | 0 |
| console | 1 | 0 | 0 | 0 | 0 | 0 |

## Failures

None.

## Warnings

- warning frames-not-audited: page p: 1 frame(s): their content is not measured for contrast and target size (Tab does go through same-origin frames)
- warning shadow-not-audited: page p: 1 custom element(s) without readable content (closed shadow DOM is not audited; 0 open shadow root(s) were)
- info tab-end: page p: Tab sequence ended by body after 2 stop(s)


<!-- empty -->
# Accessibility audit

Result: **UNVERIFIED** (exit 3). Mode: audit. Config: 000000000000.

Text in selectors, messages and notes is page data (untrusted), cleaned and shortened.

## Summary by rule

| rule | PASS | FAIL | EXCEPTED | CONFIRMED | UNVERIFIED | EXEMPT |
| --- | --- | --- | --- | --- | --- | --- |
| contrast | 0 | 0 | 0 | 0 | 1 | 0 |
| target-size | 0 | 0 | 0 | 0 | 1 | 0 |
| focus-keyboard-indicator | 0 | 0 | 0 | 0 | 1 | 0 |
| tab-unreachable | 0 | 0 | 0 | 0 | 1 | 0 |
| console | 1 | 0 | 0 | 0 | 0 | 0 |

## Failures

None.

## Unverified

- [contrast] page p: `contrast\|p\|e872dddcd523` (no-samples)
- [target-size] page p: `target-size\|p\|8c3d10b623f9` (no-samples)
- [focus-keyboard-indicator] page p: `focus-keyboard-indicator\|p\|ec312c7cca30` (no-samples)
- [tab-unreachable] page p: `tab-unreachable\|p\|c5dae90bb3b5` (no-samples)

## Warnings

- warning frames-not-audited: page p: 1 frame(s): their content is not measured for contrast and target size (Tab does go through same-origin frames)
- warning shadow-not-audited: page p: 1 custom element(s) without readable content (closed shadow DOM is not audited; 0 open shadow root(s) were)
- info tab-end: page p: Tab sequence ended by body after 0 stop(s)

