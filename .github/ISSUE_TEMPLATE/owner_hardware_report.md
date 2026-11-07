---
name: Owner hardware report
about: Share tested behavior for an exact panel model, firmware and profile
title: Owner hardware report
---

Review the device facts before submitting. For a prefilled report, open the panel's **Configure → Owner hardware report** menu in Home Assistant.

Describe the panel model, vendor firmware, Android version, Panel Assistant app version and build, and integration version and build. Use `unknown` for facts you cannot confirm.

State the profile you actually selected: `unknown`, `generic`, `community` or `built-in`, with its ID and revision where known. Do not infer the profile from the panel model.

For each behavior you tested, report `works`, `fails` or `not-fitted`, and explain how and when you tested it. Leave all other behaviors as `untested`: dashboard, brightness, sleep-wake, light, proximity, buttons, leds, relays, speaker, microphone and restart. Include required permissions and remaining limitations. A connected panel does not prove its hardware controls work.

Remove private information such as addresses, serial numbers, room names and credentials. Maintainer review is required before this report can appear as owner testimony on the website; submission does not certify support.

Paste the prefilled JSON report here, or supply the facts and test results above.
