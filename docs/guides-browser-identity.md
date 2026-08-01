---
title: "Browser Identity"
description: "Navigator, screen, headers and permissions - the properties a site reads before anything is drawn, and the ones that are checked against each other rather than on their own."
parent: "Guides"
has_children: true
nav_order: 1
---


# Browser Identity

The largest group here, because it is where most detection actually starts: the
plain-JavaScript properties a page reads in the first few milliseconds, before it
draws anything. Individually most of these are one line each. What makes them a
fingerprint is that a detector reads several and checks whether they agree - a
`navigator.webdriver` of `false` next to a font set that belongs to a different
operating system is caught by the comparison, not by either value alone.
