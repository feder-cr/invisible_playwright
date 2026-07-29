---
title: "The Automation Layer"
description: "The driver itself as a fingerprinting surface: what patching a page cannot fix, because the tell lives in how the browser is piloted, not in what it reports."
parent: "Guides"
has_children: true
nav_order: 4
---

<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "itemListElement": [
    {
      "@type": "ListItem",
      "position": 1,
      "name": "Home",
      "item": "https://feder-cr.github.io/invisible_playwright/"
    },
    {
      "@type": "ListItem",
      "position": 2,
      "name": "Guides",
      "item": "https://feder-cr.github.io/invisible_playwright/guides.html"
    },
    {
      "@type": "ListItem",
      "position": 3,
      "name": "The Automation Layer"
    }
  ]
}
</script>

# The Automation Layer

A different category from the rest of this site: not what the browser reports, but
what the act of automating it leaves behind. A debugger attached for evaluation
changes timing. A driver's own artefacts show up in stack traces. A protocol version
mismatch breaks silently on one specific call. None of this is a value you can
override from the page, because the page is not where the tell originates.
