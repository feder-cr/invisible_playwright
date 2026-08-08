---
title: "selenium-stealth hasn't been updated since December 2021"
description: "selenium-stealth's last commit was December 2021. What its property patches still do, what four-plus years of silence costs, and what to check today."
parent: "Comparisons"
nav_order: 9
---


# selenium-stealth hasn't been updated since December 2021

**`selenium-stealth` is effectively unmaintained: its repository's last commit is
from December 2021.** The JavaScript patches it applies still execute, but the list
of automation-visible properties it covers stopped growing in late 2021, while the
browser and the detection methods aimed at automation have moved through four-plus
years of updates since. That widening gap, not a broken install, is what its
continued presence in current tutorials obscures.

Search for how to stop Selenium from being detected and `selenium-stealth` still
shows up on the first page of results, in tutorials dated well into 2026. The last
commit being from December 2021 is not a minor detail buried in a changelog - it's
the single fact that changes how much weight the rest of the advice deserves.

## What it patches, and why that part still runs fine

`selenium-stealth` does the same class of thing `puppeteer-extra-plugin-stealth`
does on the Node side: apply a set of JavaScript overrides through Chrome DevTools
Protocol before the page's own scripts run, so a handful of automation-visible
properties answer the way a real Chrome's would. User agent, languages, platform,
vendor, renderer, a few others. Straightforward, and none of that logic has any
reason to stop functioning just because nobody's committed to the repository lately -
the patches that exist still apply the same way they did in 2021.

## What four and a half years of silence actually costs

The cost isn't in the patches that exist. It's in the ones that don't.

Chrome has shipped roughly four and a half years of version updates since this
package's last commit. Detection services have shipped that many update cycles too,
and they've spent them looking at exactly the kind of automation traffic this
package produces. A property list frozen in late 2021 was written against the
Chrome and CDP surface of that year. Neither stayed still.

Concretely, this shows up as a widening gap between "the properties this package
patches" and "the properties something checks today." The package's whole value
proposition is that list, and the list stopped growing while the thing it's
measured against kept moving. That's true regardless of whether any individual
2021-era patch technically still executes without error.

## The same ceiling as everything else in this category, just older

Separate from the staleness question, `selenium-stealth` shares the same structural
limit as [`playwright-stealth`](vs-playwright-stealth.md) and
[`puppeteer-extra-plugin-stealth`](puppeteer-extra-stealth-unmaintained.md): every
property it changes is overwritten from the page, after the browser's real value
already existed and after the automation protocol already connected. That overwrite
is its own signal - a [native-looking `Function.prototype.toString` check](tostring-native-code-detection.md)
or a descriptor-shape check catches an overwritten property regardless of what value
it now reports. And CDP's own presence, the automation protocol Selenium and CDP-based
tools rely on to run these patches in the first place, is a signal these patches
were never designed to hide.

An actively maintained version of the same approach would still have this ceiling.
The staleness makes the practical gap wider today; it isn't the whole story.

## What to check if you're relying on this today

1. **Check the repository, not the package's continued presence in tutorials.**
   Popularity in search results says nothing about whether a project is maintained -
   check the commit history directly.
2. **Test the actual properties you depend on**, against a current version of the
   browser you're driving, rather than trusting that a 2021-era patch list still
   matches a 2026 detection surface.
3. **If Chrome-based automation with active maintenance is the goal**, that's a
   separate question from whether this specific package is still receiving updates -
   the two shouldn't be conflated just because they're often mentioned together.

## Short answers to the questions that lead here

**Is selenium-stealth still maintained?** Its repository's last commit is from
December 2021. It has not received meaningful ongoing development since.

**Does selenium-stealth still work?** The specific patches it applies still execute.
Whether they're sufficient against a given detector in 2026 is a separate, unrelated
question its continued execution doesn't answer.

**Why do tutorials still recommend it?** Search content has a long tail, and a
package that worked well when a tutorial was written doesn't get automatically
flagged as outdated when the tutorial keeps ranking. The repository itself is the
only reliable source for whether something is still developed.

**Is this specific to Selenium, or does it apply elsewhere?** The staleness is
specific to this package. The architectural ceiling - page-level property patching
being detectable through the overwrite itself, and never touching anything below
the JavaScript layer - is the same one covered for the Puppeteer and Playwright
versions of this approach.

**See also:** [what the `$cdc_` variable actually is and why renaming it isn't the
whole fix](cdc-variable-explained.md), the Selenium-specific detection surface this
package's property patches sit next to, and
[puppeteer-extra-plugin-stealth hasn't shipped a real update since 2024](puppeteer-extra-stealth-unmaintained.md),
the same pattern on a different driver.

## Sources

- The package's own public repository and commit history, checked directly rather
  than inferred from its continued presence in current tutorials and answers.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level, on why a patch list's age matters independently
of whether the patches still technically run.*
