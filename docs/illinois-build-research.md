# Current zoning and Illinois BUILD: map research

Research date: **2026-08-31**. This is an analytical policy comparison, not legal advice,
an entitlement opinion, or a forecast of development. The proposed BUILD provisions are
versioned assumptions, not a substitute for checking enacted law before publication.

## What the reference map actually selects

[Strong Towns Chicago](https://www.strongtownschicago.org/single-stair) describes its map
as a selection of districts where density makes single-stair reform particularly relevant.
Its [embedded map](https://pgwhalen.github.io/single-stair/) supplies the following exact
selection. These are facts about that publisher's methodology, not a comprehensive legal
definition of single-stair eligibility.

| Reference group | Units per 3,125-square-foot standard lot | Districts |
| --- | ---: | --- |
| Housing on the ground floor | 7 | RM-5, RM-5.5, B2-3 |
| Housing on the ground floor | 10 | RM-6, RM-6.5 |
| Housing on the ground floor | 15 | B2-5 |
| Generally commercial ground floor | 7 | B1-3, B3-3, C1-3, C2-3 |
| Generally commercial ground floor | 15 | B1-5, B3-5, C1-5, C2-5 |

The implementation reproduces this **14-class selection**, using the project's dated
Chicago zoning snapshot, not copied map geometry. Different snapshot dates can therefore
produce different boundaries. It does not import the publisher's tooltip FAR or height
values into the capacity model. The floor-use groups also simplify special-use exceptions.

The authoritative numerical references are Chicago's
[residential bulk/density table](https://codelibrary.amlegal.com/codes/chicago/latest/chicago_il/0-0-0-2681113)
and [business/commercial bulk table](https://codelibrary.amlegal.com/codes/chicago/latest/chicago_il/0-0-0-2681378).
The residential code itself cautions that minimum lot area per unit does not guarantee
achievable density: height, parking, unit size and lot configuration also matter.

## BUILD is a package, not a blanket rezoning

The [Governor's official package description](https://gov-pritzker-newsroom.prezly.com/gov-pritzker-convenes-roundtable-on-build-initiative-to-lower-housing-costs)
identifies HB5626 as the House omnibus and six Senate components: SB4060 (middle housing),
SB4061 (single stairs), SB4071 (ADUs), SB4064 (parking), SB4063 (permit review), and SB4062
(impact fees). Funding proposals are separate from parcel-level permissions. Neither an
announced investment nor faster permitting is evidence that a particular parcel can add units.

The [HB5626 status page](https://www.ilga.gov/Legislation/BillStatus?DocNum=5626&DocTypeID=HB&GAID=18&LegID=167737&SessionID=114)
reports referral to Rules, the
[SB4060 status page](https://ilga.gov/legislation/BillStatus?DocNum=4060&DocTypeID=SB&GAID=18&LegID=167733&SessionID=114)
reports referral to Assignments, and the
[SB4061 status record](https://www.ilga.gov/ftp/legislation/104/BillStatus/HTML/10400SB4061.html)
reports referral to Assignments. These records do not establish enactment. The scenario is
explicitly **“With IL BUILD (proposed)”**, pinned to the introduced bill text; rerun a
legislative-status and amendment review before interpreting it as current law.

### Middle housing: the spatial expansion rule

[SB4060, proposed Sections 11-13.1-5 and 11-13.1-10](https://ilga.gov/Legislation/BillStatus/FullText?DocNum=4060&DocTypeID=SB&GAID=18&LegId=167733&Print=1&SessionID=114)
defines eligible districts by permission for detached single-family dwellings, not by the
district's name. Its lot-size allowances are:

| Lot area, square feet | Proposed allowance |
| --- | ---: |
| At most 2,500 | At least one detached dwelling |
| More than 2,500 through 5,000 | 4 dwellings |
| More than 5,000 through 7,500 | 6 dwellings |
| More than 7,500 | 8 dwellings |

These are minimum allowances municipalities must accommodate, **not a cap reducing more
permissive existing zoning**. The bill provides an eight-month local implementation period.
Its fallback bulk standards apply to nonconforming municipalities, rather than automatically
granting every parcel a new FAR or height. The draft's internal section references are not
fully consistent; the implementation does not resolve those drafting questions as settled law.

For example, a 25-by-125-foot lot falls in the four-unit tier. This is below the reference
map's seven-unit starting tier. Expanding the map therefore means showing an additional
middle-housing opportunity category, not claiming equivalent density or stair-related savings.

### Single stairs: a separate building-code test

[SB4061, proposed municipal Section 1-2-3.2](https://witnessslips.ilga.gov/Legislation/BillStatus/FullText?DocNum=4061&DocTypeID=SB&GAID=18&LegId=167735&SessionID=114)
would protect qualifying single-stair residential buildings with at most six above-grade
stories and four dwellings on each floor. Conditions include stairway sprinklers,
self-closing dwelling exit doors, smoke detection in common areas and dwellings, and an
emergency escape/rescue opening for each dwelling. The text specifies January 1, 2027.

That stair provision does **not** itself grant six-story zoning. A four-unit middle-housing
allowance is also not proof that an additional stair was previously required. Consequently,
the new map compares coverage and unit allowances, not estimated units “caused by” stair
reform. Safety compliance and architectural feasibility remain unverified.

### Other BUILD measures and what this map does not infer

The [HB5626 introduced omnibus](https://www.ilga.gov/Legislation/BillStatus/FullText?DocName=10400HB5626&DocNum=5626&DocTypeID=HB&GAID=18&LegDocId=209117&LegID=167737&Print=1&SessionID=114)
also addresses ADUs, parking, review deadlines, third-party reviews/inspections and fees.
Its existing-building conversion provision depends on expansion limits and applicable
building and preservation requirements. An existing building's footprint or unit count alone
cannot demonstrate compliant conversion.

This first implementation does not add an ADU to every lot, turn fee reductions into unit
counts, infer permit completion, or apply automatic demolition/landmark waivers. It also does
not treat our CTA/Metra station dataset as complete public-transit coverage: BUILD's transit
definition includes buses. Those omissions are model boundaries, not claims of no policy effect.

## Translating the bills to Chicago without overstating coverage

The [residential use table](https://codelibrary.amlegal.com/codes/chicago/latest/chicago_il/0-0-0-2681101)
permits detached houses in RS districts. RT/RM permissions have exceptions under
[17-2-0303-B](https://codelibrary.amlegal.com/codes/chicago/latest/chicago_il/0-0-0-2681113),
including community-preservation/transit restrictions. The
[B/C use table](https://codelibrary.amlegal.com/codes/chicago/latest/chicago_il/0-0-0-2681344)
permits detached houses in B2, treats them as special uses in B1/B3/C1/C2, and prohibits
them in C3. Additional restrictions appear in
[17-3-0307](https://codelibrary.amlegal.com/codes/chicago/latest/chicago_il/0-0-0-2681357).

Implementation decisions, rather than legislative claims:

- Preserve all baseline-map areas in the alternative; never relabel them as BUILD additions.
- Identify screened additions only when the supported district and lot data show a higher
  proposed allowance. Keep additional potential sites with unresolved use restrictions as
  review cases, separately from the screened count.
- Preserve raw `zone_class`: a generalized `B-3` value loses the legally important distinction
  between B1, B2 and B3.
- Do not assign “RM-5” or another new Chicago district code to a BUILD-affected parcel.
- Do not color an entire zoning polygon as newly eligible merely because one parcel qualifies.
- Retain missing/invalid data and review reasons; do not turn them into confirmed zero effects.

Cook County tax parcels are not necessarily Chicago zoning lots. Geometry area is a proxy,
not a surveyed legal lot area; condominium/unitized records, nonstandard parcel types, and
conflicting areas require review. The screening count is therefore not a count of legally
approved development sites. Eligibility does not depend on missing Assessor building records
being interpreted as vacancy.

The earlier capacity pipeline can report a zero district unit limit on an undersized RS
parcel because it floors lot area divided by minimum lot area. Zero does not establish
that an existing house is unlawful. For RS comparisons the new screen uses at least one
unit as the detached-district comparator and records that basis; this does not certify a
particular nonconforming lot. The allowance difference is not a net gain over actual
occupied dwellings.

## Data contract and interpretation

Inputs are the existing dated zoning and parcel-context/combined-analysis snapshots, with
source ownership retained in their manifests. The project owns the derived comparison.
Parcel grain is one source `objectid`, not one dwelling or one Census household. Zoning
coverage retains the source zoning identifier; ward and community summaries aggregate the
same selected parcel universe used by parcel detail. Parcel counts and land-area proxies
must not be described as additional housing units or net developable acreage.

The new map filters replace the old map scenario selector. Historical capacity charts and
the lot simulator remain separate analyses with their own assumptions; they are not BUILD
estimates. A future capacity study would need the final enacted text, legal zoning-lot
assembly, preservation and bus-corridor overlays, site envelopes, and explicit building-code
comparisons before attributing additional family-sized homes to BUILD or single stairs.

## Validation against the existing snapshots

The 2026-08-31 export used Chicago zoning captured on **2026-08-25** and the combined
parcel analysis dated **2026-08-27**. These are snapshot results, not a live inventory.

| Check | Parcel records |
| --- | ---: |
| Complete classification audit | 613,048 |
| Reference-map baseline | 30,555 |
| Screened BUILD additions outside baseline | 371,151 |
| Combined map selection | 401,706 |
| Potential additions excluded pending review | 90,056 |
| Non-baseline records with unassessed district permission | 61,643 |

The last two rows are separate audit categories, not part of the combined selection.
Other records are excluded for reasons recorded in the audit; the table is not an
exhaustive partition of the whole inventory. The baseline contains 2,947 zoning polygons.
BUILD unions the 371,151 screened tax-parcel footprints by district and ward for overview
display, rather than recoloring their zoning districts. Exact unions remove internal edges
without buffering or filling unscreened gaps. Group popups report contributing record counts;
individual lot assumptions remain in Parcel detail and the audit.
All selected records have centroids and community assignments; three BUILD-added records
lack ward assignments, giving a ward total of 401,703. The interface reports that exclusion.

Checks verified unique audit keys, retention of every baseline record, and reconciliation
of both geography summaries with these exceptions. No new family-sized housing production
estimate is inferred from these counts.
