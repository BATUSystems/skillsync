<!-- everything -->
# Accessibility audit

Result: **FAIL** (exit 1). Mode: audit. Config: 000000000000.

Text in selectors, messages and notes is page data (untrusted), cleaned and shortened.

PASS includes 4 excepted and 1 manually confirmed finding(s); keys below.

- excepted: `contrast\|main\|#brand`
- excepted: `target-size\|main\|#tiny`
- excepted: `focus-keyboard-indicator\|main\|#noring`
- excepted: `console\|main\|0b6909a2d135`
- confirmed: `contrast\|main\|#hero`

## Summary by rule

| rule | PASS | FAIL | EXCEPTED | CONFIRMED | UNVERIFIED | EXEMPT |
| --- | --- | --- | --- | --- | --- | --- |
| contrast | 2 | 3 | 1 | 1 | 4 | 2 |
| target-size | 2 | 0 | 1 | 0 | 0 | 1 |
| focus-keyboard-indicator | 1 | 0 | 1 | 0 | 1 | 0 |
| focus-mouse-ring | 0 | 0 | 0 | 0 | 1 | 0 |
| tab-positive-tabindex | 0 | 1 | 0 | 0 | 0 | 0 |
| tab-unreachable | 0 | 1 | 0 | 0 | 1 | 0 |
| tab-trap | 0 | 1 | 0 | 0 | 0 | 0 |
| tab-order-visual | 0 | 1 | 0 | 0 | 0 | 0 |
| console | 0 | 1 | 1 | 0 | 1 | 0 |
| assertion-failed | 0 | 1 | 0 | 0 | 1 | 0 |
| checks | 0 | 0 | 0 | 0 | 1 | 0 |

## Failures

- [contrast] page main: `#bad` measured ratio=2.17, fontSizePx=16, bold=false, large=false, backgroundKind=solid, required 4.5
- [contrast] page main: `#old` measured ratio=2.17, fontSizePx=16, bold=false, large=false, backgroundKind=solid, required 4.5
- [contrast] page main: `#a \| b` measured ratio=2.17, fontSizePx=16, bold=false, large=false, backgroundKind=solid, required 4.5
- [tab-positive-tabindex] page main: `#jump` measured tabindex=3: tabindex="3" overrides the natural order
- [tab-unreachable] page main: `#lost` measured : target is not reached by the Tab key
- [tab-trap] page main: `tab-trap\|main\|ea586da562c4` measured cycleLength=2, unreached=1: focus cycles among 2 element(s)
- [tab-order-visual] page main: `tab-order-visual\|main\|dd226d551fb7` measured from=#x, to=#y: focus moves visually backwards (ltr) from #x to #y
- [console] page main: `console\|main\|c1feb4e7a236` measured : http 404: GET http://localhost/missing-resource
- [assertion-failed] page asserted-fail: `assertion-failed\|asserted-fail\|7c5b1d49e687` measured : asserted element missing or text differs

## Unverified

- [contrast] page main: `#stale` (media-background)
- [contrast] page main: `contrast\|main\|12e0a3635359` (page-too-tall)
- [contrast] page main: `contrast\|main\|87764dc03a5a` (layout-shift)
- [contrast] page main: `contrast\|main\|4be3ec896a56` (truncated)
- [focus-keyboard-indicator] page main: `#edge` (clipped)
- [focus-mouse-ring] page main: `#go` (mouse-capture-failed)
- [tab-unreachable] page main: `tab-unreachable\|main\|af611ad6550c` (sequence-truncated)
- [console] page main: `console\|main\|934acd790319` (truncated)
- [assertion-failed] page no-assertion: `assertion-failed\|no-assertion\|d8a41edb6423` (no-assertion)
- [checks] page no-checks: `checks\|no-checks\|5805cf8bbc28` (no-checks)

## Exceptions

- [contrast] page main: `#brand`: logo type
- [target-size] page main: `#tiny`: matched by key
- [focus-keyboard-indicator] page main: `#noring`: rule and selector without page
- [console] page main: `console\|main\|0b6909a2d135`: known fixture error

## Confirmed manually

- [contrast] page main: `#hero` by tester: measured by hand

## Warnings

- warning threshold-lowered: threshold lowered (normal=3): fixture
- warning exception-expired: exception expired on 2026-01-01 and is ignored: contrast\|#old
- warning exception-expired: confirmation expired on 2026-01-01 and is ignored: contrast\|#stale
- info exception-unused: exception matched nothing: contrast\|#nothing
- info exception-unused: exception matched nothing: tab-unreachable\|#ok
- info exception-unused: confirmation matched nothing: contrast\|#bad
- info exception-unused: confirmation matched nothing: contrast\|main\|12e0a3635359
- warning now-overridden: run date overridden with --now 2026-11-26; expiry of exceptions is judged against it


<!-- unverified -->
# Accessibility audit

Result: **UNVERIFIED** (exit 3). Mode: audit. Config: 000000000000.

Text in selectors, messages and notes is page data (untrusted), cleaned and shortened.

## Summary by rule

| rule | PASS | FAIL | EXCEPTED | CONFIRMED | UNVERIFIED | EXEMPT |
| --- | --- | --- | --- | --- | --- | --- |
| contrast | 1 | 0 | 0 | 0 | 1 | 0 |

## Failures

None.

## Unverified

- [contrast] page p: `#hero` (occluded)


<!-- onlyNeutral -->
# Accessibility audit

Result: **UNVERIFIED** (exit 3). Mode: audit. Config: 000000000000.

Text in selectors, messages and notes is page data (untrusted), cleaned and shortened.

## Summary by rule

| rule | PASS | FAIL | EXCEPTED | CONFIRMED | UNVERIFIED | EXEMPT |
| --- | --- | --- | --- | --- | --- | --- |
| contrast | 0 | 0 | 0 | 0 | 0 | 1 |
| console | 0 | 0 | 0 | 0 | 0 | 0 |

## Failures

None.


<!-- passWithSettled -->
# Accessibility audit

Result: **PASS** (exit 0). Mode: audit. Config: 000000000000.

Text in selectors, messages and notes is page data (untrusted), cleaned and shortened.

PASS includes 1 excepted and 1 manually confirmed finding(s); keys below.

- excepted: `contrast\|main\|#brand`
- confirmed: `contrast\|main\|#hero`

## Summary by rule

| rule | PASS | FAIL | EXCEPTED | CONFIRMED | UNVERIFIED | EXEMPT |
| --- | --- | --- | --- | --- | --- | --- |
| contrast | 1 | 0 | 1 | 1 | 0 | 0 |
| console | 0 | 0 | 0 | 0 | 0 | 0 |

## Failures

None.

## Exceptions

- [contrast] page main: `#brand`: logo type

## Confirmed manually

- [contrast] page main: `#hero` by tester: measured by hand


<!-- broadException -->
# Accessibility audit

Result: **PASS** (exit 0). Mode: audit. Config: 000000000000.

Text in selectors, messages and notes is page data (untrusted), cleaned and shortened.

PASS includes 2 excepted and 0 manually confirmed finding(s); keys below.

- excepted: `contrast\|a\|#brand`
- excepted: `contrast\|b\|#brand`

## Summary by rule

| rule | PASS | FAIL | EXCEPTED | CONFIRMED | UNVERIFIED | EXEMPT |
| --- | --- | --- | --- | --- | --- | --- |
| contrast | 0 | 0 | 2 | 0 | 0 | 0 |

## Failures

None.

## Exceptions

- [contrast] page a: `#brand`: everywhere
- [contrast] page b: `#brand`: everywhere

## Warnings

- warning exception-broad: exception without "page" matched 2 findings: contrast\|#brand


<!-- expectMismatch -->
# Accessibility audit

Result: **FAIL** (exit 1). Mode: expect. Config: 000000000000.

Text in selectors, messages and notes is page data (untrusted), cleaned and shortened.

## Summary by rule

| rule | PASS | FAIL | EXCEPTED | CONFIRMED | UNVERIFIED | EXEMPT |
| --- | --- | --- | --- | --- | --- | --- |
| contrast | 1 | 0 | 0 | 0 | 0 | 0 |

## Failures

None.


<!-- expectAbsent -->
# Accessibility audit

Result: **UNVERIFIED** (exit 3). Mode: expect. Config: 000000000000.

Text in selectors, messages and notes is page data (untrusted), cleaned and shortened.

## Summary by rule

| rule | PASS | FAIL | EXCEPTED | CONFIRMED | UNVERIFIED | EXEMPT |
| --- | --- | --- | --- | --- | --- | --- |
| contrast | 1 | 0 | 0 | 0 | 0 | 0 |

## Failures

None.


<!-- expectMatch -->
# Accessibility audit

Result: **PASS** (exit 0). Mode: expect. Config: 000000000000.

Text in selectors, messages and notes is page data (untrusted), cleaned and shortened.

## Summary by rule

| rule | PASS | FAIL | EXCEPTED | CONFIRMED | UNVERIFIED | EXEMPT |
| --- | --- | --- | --- | --- | --- | --- |
| contrast | 0 | 1 | 0 | 0 | 0 | 0 |

## Failures

- [contrast] page p: `#bad` measured ratio=2.17, fontSizePx=16, bold=false, large=false, backgroundKind=solid, required 4.5

