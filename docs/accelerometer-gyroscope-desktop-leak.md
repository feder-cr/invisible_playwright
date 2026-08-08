---
title: "Do accelerometer and gyroscope APIs leak on desktop?"
description: "Desktop Firefox doesn't leak sensor data. Why a spoofed desktop must never invent motion events, and how to verify the sensor surface matches your platform claim."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 32
---


# Do accelerometer and gyroscope APIs leak on desktop?

Short version: on a real desktop Firefox they do not leak, because there is
nothing to leak. The generic Sensor APIs that expose an accelerometer or a
gyroscope reading are a Chromium feature, and the older DeviceOrientation and
DeviceMotion events stay silent on a machine with no motion hardware. So the
interesting question is not "how do I hide my sensors" - it is "am I sure my
automated desktop is not inventing motion data a real desktop would never
produce". A desktop that emits accelerometer or gyroscope readings is a
mobile-or-fake tell, and it contradicts the platform string sitting right next
to it.

This page walks through what a genuine desktop exposes, why faking motion is a
consistency failure rather than a clever disguise, what the patched build
reports, and how to check the surface yourself.

## What desktop Firefox actually exposes

Desktop Firefox exposes almost nothing on this surface: the generic Sensor
API is absent entirely, and the older DeviceMotion/DeviceOrientation events
exist as properties but never fire without motion hardware. Those are the two
separate families of device-motion API, and they behave very differently
across engines.

The **generic Sensor APIs** - [`Accelerometer`](https://developer.mozilla.org/en-US/docs/Web/API/Accelerometer),
[`Gyroscope`](https://developer.mozilla.org/en-US/docs/Web/API/Gyroscope),
`LinearAccelerationSensor`, `AbsoluteOrientationSensor` and the rest of that
family - are implemented in Chromium and, at time of writing, not in Firefox at
all. On a real Firefox, `typeof Accelerometer` is `"undefined"`. A page that
tries `new Gyroscope()` gets a `ReferenceError`. This is not a stealth trick;
it is simply what the engine ships.

The **[DeviceOrientation and DeviceMotion](https://developer.mozilla.org/en-US/docs/Web/API/DeviceMotionEvent) events** - `window.ondeviceorientation`,
`window.ondevicemotion` - do exist as properties in Firefox, because they are
part of an older, more widely shipped spec. But existing as a property is not
the same as firing. On a desktop with no accelerometer and no gyroscope, you can
add a `devicemotion` listener and it will never be called. The event surface is
present; the data behind it is silent.

So a truthful desktop Firefox answers the sensor question three ways at once:
the generic Sensor constructors are absent, the motion event handlers exist but
never fire, and `DeviceMotionEvent.requestPermission` (the iOS gesture gate) is
also absent because it is a mobile Safari construct. All three of those agree
with each other and with a `navigator.platform` of `Win32`.

## Why a desktop that emits motion is a tell

The failure mode here is not "a detector reads your accelerometer". It is a
detector noticing that your accelerometer exists at all when your platform says
it should not.

Detection on this surface is a cross-check, the same shape as every other
consistency check. A page reads `navigator.platform`, `navigator.userAgent` and
`navigator.oscpu`, decides you are claiming a Windows desktop, and then asks a
question a Windows desktop has a known answer to: does `window.DeviceMotionEvent`
ever produce a non-null reading? A desktop says no. If yours says yes - if some
well-meaning spoof layer decided to synthesize plausible-looking motion so the
sensor "would not look dead" - you have just told the page you are a phone
wearing a desktop user agent, which is a contradiction no real device produces.

This is why the honest move is silence, not simulation. The instinct to fill
every surface with realistic data is exactly wrong here: on a desktop the
realistic data is no data. The values that have to agree are your platform, your
oscpu, your touch surface and your motion surface, and they agree by all
pointing at the same kind of machine. If you want the platform side of that
in depth, see
[keeping navigator.platform and oscpu consistent](navigator-platform-oscpu-consistency.md);
the closely related touch side, where a desktop must report zero touch points,
is in [maxTouchPoints and pointer type](navigator-maxtouchpoints-pointer.md).

The general rule, which shows up on every fingerprint surface: a suppressed or
absent signal is not automatically a red flag. It is a red flag only when it
disagrees with what you claim to be. A desktop with no motion sensor is
correct. A mobile user agent with no motion sensor is the tell.

## What the patched build reports

invisible_playwright is a real Firefox patched at the C++ level and driven by
stock Playwright. On the sensor surface its job is the easy kind: report exactly
what a real Windows Firefox reports, which is silence. The generic Sensor
constructors stay absent, the DeviceMotion and DeviceOrientation handlers exist
but never fire, and nothing invents a reading to fill the gap. Because the whole
identity is seed-derived, the platform string, the oscpu string, the touch
surface and this motion surface all come from one coherent machine rather than
from four independent guesses, so they agree by construction.

That is the point of the demonstration below. There is nothing to configure and
no sensor flag to set - the correct desktop behaviour is the default, and the
launch is the ordinary two lines.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    report = page.evaluate("""() => {
        const motionFired = [];
        window.addEventListener('devicemotion', e => motionFired.push(e), { once: true });
        return {
            platform: navigator.platform,
            hasAccelerometerCtor: typeof Accelerometer !== 'undefined',
            hasGyroscopeCtor: typeof Gyroscope !== 'undefined',
            hasDeviceMotionEvent: 'DeviceMotionEvent' in window,
            hasRequestPermission:
                typeof (window.DeviceMotionEvent || {}).requestPermission === 'function',
            motionEventsSeen: motionFired.length,
        };
    }""")

    print(report)
    # platform: 'Win32'
    # hasAccelerometerCtor: False   <- generic Sensor API is Chromium-only
    # hasGyroscopeCtor:     False
    # hasDeviceMotionEvent: True     <- handler exists...
    # hasRequestPermission: False    <- iOS-only, correctly absent
    # motionEventsSeen:     0        <- ...but never fires: no motion hardware
```

Pass the same `seed` and the surrounding identity comes back identical every
run, so if you are bisecting a block you are changing one thing at a time rather
than chasing a fresh random machine on each attempt.

## Verify it yourself against a stock browser

Do not take the output above on faith - the useful test is a diff, not a
verdict. Open the same probe in a stock desktop Firefox on the same machine and
compare field by field. The two should agree on every line: same absent
constructors, same present-but-silent event handlers, same zero motion events.
Anything that differs between the automated browser and the stock one, other
than the exit address, is a candidate worth explaining.

Then extend the same probe to the neighbouring surfaces that have to tell the
same story - platform, oscpu, `maxTouchpoints`, screen. A detector reads them
together, so you should too. The method for turning that comparison into
something trustworthy, including why one green run is not a pass and why an empty
result is a failure rather than a clean sheet, is in
[how to test bot detection without a false pass](how-to-test-bot-detection.md).
For the wider point that a missing signal counts as a signal, the
[screen-size and headless tells](screen-size-headless-tells.md) page covers the
same idea on a different surface.

## What this fixes and what it does not

Matching the sensor surface makes invisible_playwright look like a genuine
desktop Firefox on one more axis, and that is real: the fingerprint, the TLS
handshake and the driver layer read as an ordinary Firefox, which is why a
consistent identity passes most in-page detection. But the sensor surface is a
narrow win, and it is worth being blunt about the boundary.

Reporting the correct silence does nothing for the parts of a session that are
not browser properties at all. It does not clean your **IP reputation** - a
perfectly consistent desktop on a datacenter or already-flagged address still
loses on the address. It does not manage **per-account quotas or rate limits** -
those are counted server-side regardless of how real the browser looks. And it
does not shape your **behaviour or timing** - a pointer that teleports, a form
filled in eighty milliseconds, or an agent that pauses in a shape that looks
like model latency will be flagged no matter how honest the sensor readings are.
Those are yours to supply: a clean residential exit and human pacing. If your
fingerprint is clean and you are still blocked, that separation is the whole
subject of [why you can be blocked with a clean fingerprint](why-blocked-with-a-clean-fingerprint.md).

So the honest framing: device-sensor consistency is one correct answer among
many, it costs you nothing here because the default is already right, and it is
necessary but nowhere near sufficient on its own.

## Conclusion

Accelerometer and gyroscope do not leak on desktop Firefox because desktop
Firefox has nothing to report: the generic Sensor APIs are Chromium-only, and
the motion events exist as handlers that never fire. The mistake to avoid is
over-correction - synthesizing motion data so the surface "does not look dead"
turns a correct silence into a contradiction with your platform string. The
patched build reports the same silence a real Windows Firefox does, keeps it
coherent with platform, oscpu and the touch surface because they all derive from
one seed, and leaves the parts it cannot touch - address, quotas, behaviour -
clearly labelled as yours.

## Short answers to the questions that lead here

**Does desktop Firefox expose accelerometer or gyroscope data?** No. The generic
Sensor API constructors are Chromium-only and absent in Firefox, and the older
DeviceMotion and DeviceOrientation events stay silent with no motion hardware.

**Is a silent sensor surface a red flag?** Only if it disagrees with what you
claim. A desktop with no motion sensor is correct; a mobile user agent with no
motion sensor is the tell.

**Should I fake motion events to look more real on a desktop?** No. That is the
one thing that breaks it - a desktop emitting accelerometer or gyroscope
readings contradicts its own platform string.

**Do the DeviceMotion event handlers exist at all in Firefox?** Yes, the
`ondevicemotion` and `ondeviceorientation` properties are present, but they never
fire without hardware, so a listener is simply never called.

**Does invisible_playwright need a sensor flag set for this?** No. Reporting the
correct desktop silence is the default; there is nothing to configure.

**Does getting the sensor surface right mean I will not be detected?** No. It
helps with fingerprint consistency, not with IP reputation, account quotas, rate
limits, or behaviour and timing, which you still have to supply.

## Sources

- The generic Sensor APIs (`Accelerometer`, `Gyroscope` and family) as an
  engine feature present in Chromium and not in Firefox, read from each engine's
  own behaviour rather than from a summary.
- The DeviceOrientation and DeviceMotion event model, and its behaviour on
  hardware with no motion sensor.
- This project's release gates, which compare the patched build's surfaces field
  by field against a stock desktop Firefox on the same machine.

**See also:** [navigator.platform and oscpu consistency](navigator-platform-oscpu-consistency.md),
[maxTouchpoints and pointer type](navigator-maxtouchpoints-pointer.md), and
[why you can be blocked with a clean fingerprint](why-blocked-with-a-clean-fingerprint.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The sensor surface
is the easy kind of honest: the correct answer is to report nothing, and the only
way to fail is to invent data a real desktop would never send.*
