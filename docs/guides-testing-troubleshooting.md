---
title: "Testing and Troubleshooting"
description: "What to check, and in what order, when automation is detected or a preference silently does nothing - before assuming a fix worked, and before buying a better proxy."
parent: "Guides"
has_children: true
nav_order: 7
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
      "name": "Testing and Troubleshooting"
    }
  ]
}
</script>

# Testing and Troubleshooting

The order you check things in matters more than most of the individual checks. A
green result from a shallow test and a green result from real usage are not the same
claim, and a check that only ever asserts the absence of a leak can stay green while
the feature it's supposed to protect is completely broken. This group is about
telling the difference before you ship a fix, not after.
