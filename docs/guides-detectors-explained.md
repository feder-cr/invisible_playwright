---
title: "Detectors, Explained"
description: "How specific, well-known detectors actually work - sannysoft, CreepJS, BotD, FingerprintJS, reCAPTCHA v3 - read from their own source rather than reverse-engineered from behaviour."
parent: "Guides"
has_children: true
nav_order: 6
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
      "name": "Detectors, Explained"
    }
  ]
}
</script>

# Detectors, Explained

Not "how to beat" any of these - how they actually work, read from the tool's own
source rather than guessed at from its output. Understanding what a detector is
really checking, row by row or module by module, generalises further than any single
workaround does: most of what these tools check is not automation at all, it is
whether a browser is telling the truth about what it claims to be.
