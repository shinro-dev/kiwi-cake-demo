<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# The imported design, and how to sync the next one

## What was imported

| | |
| --- | --- |
| Project | `Prototype scene presets UI` |
| Project ID | `fca6be13-be8c-4976-a2d1-638fada6667f` |
| Link | https://claude.ai/design/p/fca6be13-be8c-4976-a2d1-638fada6667f |
| Owner | the operator's own claude.ai account |
| Project type | `PROJECT_TYPE_PROJECT` |
| Imported | 2026-09-22 |
| Design system | `@shinro/web@0.1.0`, bundle id `shinro-studio-2240e739-27fc-46c7-aa98-35e020eb4b81` |

The integration supplies **no revision or version number** for a project, so there is nothing to record as one.
The export digest below is what pins this import instead. It is a sha256 of the file bytes exactly as the
integration returned them.

| sha256 | file |
| --- | --- |
| `da9b19e4eada7e80f1e0fe2276c5dc71cd464817401f67152f1d6528e03aa916` | `imported/Cake Live Demo v2.dc.html` |
| `b3381471a957345fb750bc115bb61bfe170c4b8c6965f6b829db07b0f376d2e6` | `imported/_ds/design-tokens.css` (derived, see below) |

The project also holds `Cake Live Demo.dc.html` (the earlier version, not imported), `support.js` (the
generated dc-runtime that renders a `.dc.html`), `image-slot.js`, and the design system's own bundle and fonts.

## What the design actually is

796 lines. It is a `.dc.html` design document: an `<x-dc>` template plus one `<script type="text/x-dc">`
holding the prototype's state.

It is almost entirely PLAIN MARKUP styled inline: 211 inline `style=` attributes, two `class=` attributes, and
exactly one design-system component used eight times (`ShinroStudio.Button`, at template lines 44, 45, 46, 184,
253, 255, 310, 311). That is why this import is a token sheet and a template rather than a vendored React
library: the design does not depend on the 744-component bundle to look like itself.

## The one thing the integration could not give us

`_ds/…/_ds_bundle.css` is **267.7 KB** and the integration's `get_file` caps a read at **256 KiB**, so a fetched
copy comes back with `truncated: true`, cut mid-rule inside the trailing Tailwind utilities layer. A truncated
stylesheet in the tree would look complete and not be, so it is not vendored.

It is not needed. Every `var(--…)` in the design was extracted and checked against the bundle's definitions:

```
tokens referenced by the design: 5   (--color-border, --color-card, --color-muted-foreground, --font-sans, --font-mono)
defined in the bundle:           5
missing:                         0
```

All five are in the bundle's `@layer theme` block, which is at the TOP of the file and therefore intact; the cut
is in utility classes, which this design uses twice (`class="fixture"`, a local rule of its own). The five values
are copied verbatim into `imported/_ds/design-tokens.css`.

**If a future revision starts using Tailwind utility classes or more tokens, this no longer holds.** Re-run the
check below; if it reports anything missing, the bundle has to be brought in by a path that is not capped at
256 KiB, and that is a genuine blocker to report rather than work around.

## Syncing a later design change

Read-only; it never writes to the design project.

```
bash apps/console-web/design/sync-check.sh
```

It re-reads the project through the integration, restages `imported/`, prints a digest for every file, and fails
if any token the design references is not defined. Review the diff of `imported/` before touching anything else.

**What a sync may and may not touch.** `imported/` is generated and may be replaced wholesale. Nothing else may
be: the bindings live in `src/design/` as a thin adapter, and the runtime behaviour they bind to (the API client,
authentication, the event reducer, snapshot reconciliation, readiness, the command journal and the result
helpers) is ordinary application code that a design sync never reaches. A sync that wants to change behaviour is
a code change, made by hand, in those files.

Staging is local and manual on purpose: an import is never deployed automatically, and never while a robot
command is active.
