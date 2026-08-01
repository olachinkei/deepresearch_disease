# Web Accessibility Baseline Checklist

## Scope

This checklist applies to the local public/synthetic-data MVP desktop interface.
It is a release regression checklist, not a claim of full WCAG conformance.

## Automated evidence

- [x] Axe reports zero `critical` or `serious` violations on the idle and
  completed research views.
- [x] A keyboard-triggered flow covers research start, cancel, retry, source
  focus, follow-up, and positive feedback.
- [x] Research progress is announced by a small atomic `role="status"` region.
- [x] The transcript and streaming Markdown are not live regions.
- [x] Errors use alerts and saved feedback uses a status announcement without
  leaving focus on a removed control.
- [x] `prefers-reduced-motion: reduce` collapses animation and transition time.
- [x] Keyboard focus has a computed solid outline of at least 3 CSS pixels.

The executable evidence is `apps/web/tests/e2e/accessibility.spec.ts`.

## Manual release check

- [ ] At 200% browser zoom, content remains usable without overlapping controls.
- [ ] With VoiceOver, progress changes are announced once and streamed Markdown
  is not repeatedly reread.
- [ ] Visible focus can be followed through every interactive control.
- [ ] Source badges, error states, disclaimers, and controls remain legible in
  their actual display environment.

Record browser, assistive technology, operating system, date, and reviewer when
performing the manual release check. Do not use confidential data.
