"""Generate the RxScan 1.1 mockup screens (static HTML) from shared pieces.

    python3 docs/mockups/build.py        # writes docs/mockups/screens/*.html
    python3 docs/mockups/render.py       # renders PNGs (needs Playwright + Chrome)
"""
from pathlib import Path

OUT = Path(__file__).parent / "screens"
OUT.mkdir(exist_ok=True)

ICON = {
    "mark": '<svg viewBox="0 0 24 24" fill="none"><path d="M9 3h6v6h6v6h-6v6H9v-6H3V9h6z" fill="#fff"/></svg>',
    "camera": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/></svg>',
    "photos": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 16 5-5 4 4 3-3 6 6"/><circle cx="16" cy="9" r="1.5"/></svg>',
    "sheet": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M4 9h16M4 15h16M10 9v12"/></svg>',
    "folder": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M3 6h6l2 2h10v11H3z"/></svg>',
    "gear": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/></svg>',
    "back": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg>',
    "check": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10" stroke-width="2"/><path d="m7.5 12.5 3 3 6-6.5"/></svg>',
    "clock": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
    "google": '<svg class="g" viewBox="0 0 48 48"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C36.9 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/></svg>',
}


def page(title, body, bar="", dark=False):
    cls = ' class="dark"' if dark else ' class="light"'
    return f"""<!DOCTYPE html>
<html lang="en"{cls}><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{title} · RxScan 1.1 mockup</title>
<link rel="stylesheet" href="../rx.css">
</head><body>
{body}
{bar}
</body></html>
"""


def topbar(initial="M", title=None, back=False):
    left = (f'<button class="iconbtn" aria-label="Back">{ICON["back"]}</button><span class="brand">{title}</span>'
            if back else f'<div class="mark">{ICON["mark"]}</div><span class="brand">RxScan</span>')
    return f"""<header class="topbar">{left}<span class="spacer"></span>
  <button class="iconbtn" aria-label="Settings">{ICON["gear"]}</button>
  <button class="avatar" aria-label="Your account">{initial}</button></header>"""


DEST = f"""<div class="dest"><div class="ic">{ICON["sheet"]}</div><span class="k">Rows go to</span><button class="chg">Change</button>
  <span class="v">Prescriptions 2026 › Prescriptions</span></div>
<div class="dest"><div class="ic">{ICON["folder"]}</div><span class="k">Receipt photos go to</span><button class="chg">Change</button>
  <span class="v">My Drive › RxScan Receipts › 2026</span></div>"""


def shot(state, label, x=True):
    xb = '<button class="x" aria-label="Remove">✕</button>' if x else ""
    return f'<div class="shot"><div class="paper"><i></i><i></i><i></i><i></i><i></i></div>{xb}<span class="state {state}">{label}</span></div>'


def rx(no, date, drug, strength, qty, days, refills, pharmacy, prescriber, paid, retail, flag=None, edit=False):
    flag_cls = " flag" if flag else ""
    note = f'<div class="flagnote"><b>Check this</b><span>{flag}</span></div>' if flag else ""
    if edit:
        body = f"""<div class="fields">
      <div class="f full"><label>Medication</label><input value="{drug}"></div>
      <div class="f"><label>Strength</label><input value="{strength}"></div>
      <div class="f"><label>Form</label><input value="Tablet"></div>
      <div class="f"><label>Quantity</label><input value="{qty}"></div>
      <div class="f"><label>Days supply</label><input value="{days}"></div>
      <div class="f"><label>Refills left</label><input value="{refills}"></div>
      <div class="f"><label>Date filled</label><input value="2026-11-18"></div>
      <div class="f full"><label>Prescriber</label><input value="{prescriber}"></div>
      <div class="f"><label>You paid</label><input value="{paid}"></div>
      <div class="f"><label>Insurance paid</label><input class="blank" value="" placeholder="Not on receipt"></div>
      <div class="f full"><label>Notes</label><input class="blank" value="" placeholder="Not on receipt"></div>
    </div>
    <div class="rx-tools"><button>Done editing</button><button class="del">Remove</button></div>"""
    else:
        body = f"""<div class="rx-drug">{drug}</div><div class="rx-strength">{strength} tablet</div>
    <div class="rx-grid"><div><div class="k">Quantity</div><div class="v">{qty}</div></div>
      <div><div class="k">Days</div><div class="v">{days}</div></div>
      <div><div class="k">Refills</div><div class="v">{refills}</div></div></div>
    <div class="rx-who"><span><strong>{pharmacy}</strong></span><span>Prescribed by {prescriber}</span></div>
    <div class="rx-pay"><span class="k">You paid</span><span><div class="v">${paid}</div><div class="sub">Retail ${retail}</div></span></div>
    {note}
    <div class="rx-tools"><button>Edit</button><button>All 22 fields</button><button class="del">Remove</button></div>"""
    thumb = "" if edit else '<div class="rx-thumb" aria-hidden="true"><i></i><i></i><i></i><i></i></div>'
    return f"""<article class="rx{flag_cls}">
  <div class="rx-band"><b>Rx {no}</b><span>Filled</span><span class="date">{date}</span></div>
  {thumb}<div class="rx-body">{body}</div></article>"""


def bar(primary, secondary=None, count=None, disabled=False):
    c = f' <span class="count">{count}</span>' if count else ""
    s = f'<button class="btn btn-line">{secondary}</button>' if secondary else ""
    d = " disabled" if disabled else ""
    return f'<nav class="bar"><div class="in">{s}<button class="btn btn-primary"{d}>{primary}{c}</button></div></nav>'


SCREENS = {}

# 1. sign in
SCREENS["01-sign-in"] = page("Sign in", f"""
<main class="signin">
  <div class="hero">
    <div class="bigmark">{ICON["mark"]}</div>
    <h1>Snap the receipt.<br>Skip the typing.</h1>
    <p class="lede">RxScan reads your pharmacy receipts and adds each prescription to your own Google Sheet, with the photo saved alongside.</p>
    <div class="sample">{rx("0123456", "Nov 18", "Atorvastatin", "20 mg", "90", "90", "3", "CVS Pharmacy #1234", "Dr. Patel", "10.00", "64.99")}</div>
  </div>
  <div class="stack">
    <button class="btn btn-google">{ICON["google"]}Sign in with Google</button>
  </div>
  <p class="fine">Only people the admin has added can sign in. RxScan can see only the sheet and folder it creates or that you pick.</p>
</main>""")

# 2. first run
SCREENS["02-first-run"] = page("Home, first sign-in", f"""
<div class="app">{topbar("J")}
  <div class="notice ok">{ICON["check"]}<div><strong>You're set up, Jenna.</strong>
    <p>We made a sheet called “RxScan – Jenna” and a “RxScan Receipts” folder in your Drive. Change either below.</p></div></div>
  <section class="capture"><div class="scanline"></div>
    <h1>Scan a receipt</h1><p>Photograph the receipt or the label on the pharmacy bag. Several at once is fine.</p>
    <div class="actions"><button class="btn btn-primary">{ICON["camera"]}Take photo</button><button class="btn btn-quiet">{ICON["photos"]}Choose photos</button></div>
  </section>
  <h2>Where things go</h2>{DEST.replace("Prescriptions 2026 › Prescriptions", "RxScan – Jenna › Prescriptions")}
</div>""", bar("Nothing to review yet", None, disabled=True))

# 3. home with queue
SCREENS["03-capture-queue"] = page("Photos waiting", f"""
<div class="app">{topbar()}
  <section class="capture"><div class="scanline"></div>
    <h1>Scan a receipt</h1><p>Photograph the receipt or the label on the pharmacy bag. Several at once is fine.</p>
    <div class="actions"><button class="btn btn-primary">{ICON["camera"]}Take photo</button><button class="btn btn-quiet">{ICON["photos"]}Choose photos</button></div>
  </section>
  <h2>This batch</h2>
  <div class="queue">{shot("ok", "2 found")}{shot("busy", "Reading…")}{shot("wait", "Offline")}</div>
  <div class="notice wait">{ICON["clock"]}<div><strong>You're offline.</strong><p>The third photo is saved on this phone and will be read as soon as you're back online.</p></div></div>
  <h2>Where things go</h2>{DEST}
</div>""", bar("Review 2 prescriptions"))

# 4. review
SCREENS["04-review"] = page("Review", f"""
<div class="app">{topbar(back=True, title="Review")}
  <p class="lede">Check each label against the receipt. Tap Edit to fix anything before it goes to your sheet.</p>
  {rx("0123456", "Nov 18, 2026", "Atorvastatin", "20 mg", "90", "90", "3", "CVS Pharmacy #1234", "Dr. Anika Patel", "10.00", "64.99")}
  {rx("0098817", "Nov 18, 2026", "Lisinopril", "10 mg", "30", "30", "—", "CVS Pharmacy #1234", "Dr. Anika Patel", "4.00", "18.49", flag="Refills was hard to read on the photo. Tap Edit to fill it in, or leave it blank.")}
</div>""", bar("Add 2 to sheet", "Add photos"))

# 5. edit
SCREENS["05-edit"] = page("Editing a prescription", f"""
<div class="app">{topbar(back=True, title="Review")}
  {rx("0098817", "Nov 18, 2026", "Lisinopril", "10 mg", "30", "30", "", "CVS Pharmacy #1234", "Dr. Anika Patel", "4.00", "18.49", edit=True)}
</div>""", bar("Add 2 to sheet", "Add photos"))

# 6. done
SCREENS["06-added"] = page("Added", f"""
<div class="app">{topbar()}
  <div class="notice ok">{ICON["check"]}<div><strong>Added 2 prescriptions to Prescriptions 2026.</strong>
    <p>The receipt photo is in RxScan Receipts › 2026 and linked from each row. <a href="#">Open sheet</a></p></div></div>
  <section class="capture"><div class="scanline"></div>
    <h1>Scan a receipt</h1><p>Photograph the receipt or the label on the pharmacy bag. Several at once is fine.</p>
    <div class="actions"><button class="btn btn-primary">{ICON["camera"]}Take photo</button><button class="btn btn-quiet">{ICON["photos"]}Choose photos</button></div>
  </section>
  <h2>Where things go</h2>{DEST}
</div>""", bar("Nothing to review yet", disabled=True))

# 7. my settings
SCREENS["07-my-settings"] = page("Your settings", f"""
<div class="app">{topbar(back=True, title="Settings")}
  <h2>Your sheet</h2>
  <div class="group">
    <div class="row"><div class="main"><div class="t">Prescriptions 2026</div><div class="s">Tab: Prescriptions</div></div><button class="act">Change</button></div>
    <div class="row"><div class="main"><div class="t">Make a new sheet</div><div class="s">Starts with the column headers already in place</div></div><button class="act">Create</button></div>
  </div>
  <h2>Receipt photos</h2>
  <div class="group">
    <div class="row"><div class="main"><div class="t">RxScan Receipts</div><div class="s">My Drive › RxScan Receipts</div></div><button class="act">Change</button></div>
    <div class="row"><div class="main"><div class="t">A folder for each year</div><div class="s">RxScan Receipts › 2026</div></div><span class="toggle"></span></div>
  </div>
  <h2>Account</h2>
  <div class="group">
    <div class="row"><span class="ini">M</span><div class="main"><div class="t">Michael Strasser</div><div class="s">mstras@gmail.com</div></div><span class="pill green">Admin</span></div>
    <div class="row"><div class="main"><div class="t">Sign out</div></div><button class="act red">Sign out</button></div>
  </div>
  <h2>Admin</h2>
  <div class="group">
    <div class="row"><div class="main"><div class="t">People</div><div class="s">4 people can sign in</div></div><button class="act">Manage</button></div>
    <div class="row"><div class="main"><div class="t">Reading receipts</div><div class="s">Gemini 3.8 Flash</div></div><button class="act">Change</button></div>
    <div class="row"><div class="main"><div class="t">Google connection</div><div class="s">Signed-in users can reach Sheets and Drive</div></div><span class="pill green">Working</span></div>
  </div>
</div>""")

# 8. admin people
SCREENS["08-admin-people"] = page("People", f"""
<div class="app">{topbar(back=True, title="People")}
  <p class="lede">Only these Google accounts can sign in. Each person gets their own sheet and photo folder.</p>
  <div class="adduser"><input placeholder="name@gmail.com" aria-label="Email to add"><button class="btn btn-primary">Add</button></div>
  <h2>4 people</h2>
  <div class="group">
    <div class="row"><span class="ini">M</span><div class="main"><div class="t">Michael Strasser</div><div class="s">mstras@gmail.com</div></div><span class="pill green">Admin</span></div>
    <div class="row"><span class="ini">J</span><div class="main"><div class="t">Jenna Strasser</div><div class="s">Last used today</div></div><button class="act">Options</button></div>
    <div class="row"><span class="ini">R</span><div class="main"><div class="t">Robert Strasser</div><div class="s">Last used Nov 2</div></div><button class="act">Options</button></div>
    <div class="row"><span class="ini">?</span><div class="main"><div class="t">grandma.ruth@gmail.com</div><div class="s">Hasn't signed in yet</div></div><span class="pill amber">Invited</span></div>
  </div>
  <p class="muted" style="margin-top:12px">Removing someone signs them out and stops RxScan from using their Google access. Their sheet and photos stay in their Drive.</p>
</div>""")

# 9. admin google setup
SCREENS["09-admin-google-setup"] = page("Google setup", f"""
<div class="app">{topbar(back=True, title="Google setup")}
  <p class="lede">One-time setup in Google Cloud so people can sign in with Google. About 15 minutes.</p>
  <div class="group">
    <div class="row"><div class="main"><div class="t">Turn on Sheets, Drive and Picker</div><div class="s">APIs & Services › Library</div></div><span class="pill green">Done</span></div>
    <div class="row"><div class="main"><div class="t">Set up the consent screen</div><div class="s">External, then Publish app so sign-ins don't expire weekly</div></div><span class="pill green">Done</span></div>
    <div class="row"><div class="main"><div class="t">Create a sign-in client</div><div class="s">Paste this redirect address: https://rxscan.strasserco.com/auth/google/callback</div></div><button class="act">Copy</button></div>
    <div class="row"><div class="main"><div class="t">Paste the four values</div><div class="s">Client ID, client secret, API key, project number</div></div><span class="pill amber">2 of 4</span></div>
  </div>
  <h2>Values from Google Cloud</h2>
  <div class="fields">
    <div class="f full"><label>Client ID</label><input value="381920-abc.apps.googleusercontent.com"></div>
    <div class="f full"><label>Client secret</label><input value="••••••••••••Zq4x"></div>
    <div class="f full"><label>API key</label><input class="blank" placeholder="AIza…"></div>
    <div class="f full"><label>Project number</label><input class="blank" placeholder="12-digit number"></div>
  </div>
</div>""", bar("Save and test"))

# 10. review, dark
SCREENS["10-review-dark"] = SCREENS["04-review"].replace('<html lang="en" class="light">', '<html lang="en" class="dark">')


# ======================= Web / desktop screens =======================
ICON.update({
    "people": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.6 3.4-5.5 6.5-5.5s5.7 1.9 6.5 5.5"/><circle cx="17" cy="9" r="2.5"/><path d="M17 14.5c2.3.3 3.9 1.9 4.5 4.5"/></svg>',
    "eye": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
    "key": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M17 6l3 3M15 8l2 2"/></svg>',
})


def side(active, admin=True, name="Michael Strasser", email="mstras@gmail.com", initial="M"):
    def a(key, label, icon):
        on = ' class="on"' if key == active else ""
        return f'<a href="#"{on}>{ICON[icon]}{label}</a>'
    adm = ""
    if admin:
        adm = ('<div class="navh">Admin</div>' + a("people", "People", "people")
               + a("reading", "Reading receipts", "eye") + a("google", "Google setup", "key"))
    return f"""<aside class="side">
  <div class="brandrow"><div class="mark">{ICON["mark"]}</div><span class="brand">RxScan</span></div>
  {a("scan", "Scan receipts", "camera")}{a("settings", "Your settings", "gear")}{adm}
  <div class="me"><span class="avatar">{initial}</span><div><div class="t">{name}</div><div class="s">{email}</div></div></div>
</aside>"""


def webpage(title, inner):
    return page(title, f'<div class="web">{inner}</div>')


CAPTURE_WEB = f"""<section class="capture"><div class="scanline"></div>
    <h1>Scan a receipt</h1><p>Photograph the receipt or the label on the pharmacy bag.</p>
    <div class="actions"><button class="btn btn-primary">{ICON["photos"]}Choose photos</button><button class="btn btn-quiet">{ICON["camera"]}Use camera</button></div>
    <div class="drop"><b>Drop photos here</b><br>JPEG, PNG or HEIC, several at once</div>
  </section>"""

SCREENS["web-01-sign-in"] = page("Sign in (web)", f"""
<div class="signin-web">
  <section class="art">
    <div class="brandrow"><div class="mark">{ICON["mark"]}</div><span class="brand">RxScan</span></div>
    <div class="stackart" aria-hidden="true">
      {rx("0098817", "Nov 18", "Lisinopril", "10 mg", "30", "30", "5", "CVS Pharmacy #1234", "Dr. Patel", "4.00", "18.49")}
      {rx("0123456", "Nov 18", "Atorvastatin", "20 mg", "90", "90", "3", "CVS Pharmacy #1234", "Dr. Patel", "10.00", "64.99")}
    </div>
    <h1>Snap the receipt. Skip the typing.</h1>
    <p>RxScan reads your pharmacy receipts and adds each prescription to your own Google Sheet, with the photo saved alongside.</p>
  </section>
  <section class="panel">
    <h2>Sign in</h2>
    <p class="lede">Use the Google account the admin added for you.</p>
    <button class="btn btn-google">{ICON["google"]}Sign in with Google</button>
    <p class="fine">RxScan can see only the sheet and folder it creates or that you pick. Nothing else in your Drive.</p>
  </section>
</div>""")

SCREENS["web-02-scan-review"] = webpage("Scan and review (web)", f"""{side("scan")}
<main class="webmain">
  <div class="pagehead"><div><h1>Scan receipts</h1><p class="sub">3 prescriptions from 2 photos are ready to check.</p></div>
    <div class="acts"><button class="btn btn-line">Clear batch</button><button class="btn btn-primary">Add 3 to sheet</button></div></div>
  <div class="scanlayout">
    <div class="left">
      {CAPTURE_WEB}
      <div>{DEST}</div>
      <div><div class="sectionhead"><h2>This batch</h2><span class="muted">2 photos</span></div>
        <div class="queue wide">{shot("ok", "2 found")}{shot("ok", "1 found")}</div></div>
    </div>
    <div>
      <div class="sectionhead"><h2>Check each label</h2><span class="muted">Edit anything before it goes to your sheet</span></div>
      <div class="labels">
        {rx("0123456", "Nov 18, 2026", "Atorvastatin", "20 mg", "90", "90", "3", "CVS Pharmacy #1234", "Dr. Anika Patel", "10.00", "64.99")}
        {rx("0098817", "Nov 18, 2026", "Lisinopril", "10 mg", "30", "30", "—", "CVS Pharmacy #1234", "Dr. Anika Patel", "4.00", "18.49", flag="Refills was hard to read on the photo. Tap Edit to fill it in, or leave it blank.")}
        {rx("5521093", "Nov 11, 2026", "Amoxicillin", "500 mg", "21", "7", "0", "Walgreens #08812", "Dr. Luis Romero", "6.20", "22.79")}
      </div>
    </div>
  </div>
</main>""")

SCREENS["web-03-settings"] = webpage("Your settings (web)", f"""{side("settings")}
<main class="webmain">
  <div class="pagehead"><div><h1>Your settings</h1><p class="sub">Where your prescriptions and receipt photos go.</p></div></div>
  <div class="settings-web">
    <div>
      <h2>Your sheet</h2>
      <div class="group">
        <div class="row"><div class="main"><div class="t">Prescriptions 2026</div><div class="s">Tab: Prescriptions</div></div><button class="act">Change</button></div>
        <div class="row"><div class="main"><div class="t">Make a new sheet</div><div class="s">Starts with the column headers already in place</div></div><button class="act">Create</button></div>
      </div>
      <h2>Account</h2>
      <div class="group">
        <div class="row"><span class="ini">M</span><div class="main"><div class="t">Michael Strasser</div><div class="s">mstras@gmail.com</div></div><span class="pill green">Admin</span></div>
        <div class="row"><div class="main"><div class="t">Sign out on this device</div></div><button class="act red">Sign out</button></div>
      </div>
    </div>
    <div>
      <h2>Receipt photos</h2>
      <div class="group">
        <div class="row"><div class="main"><div class="t">RxScan Receipts</div><div class="s">My Drive › RxScan Receipts</div></div><button class="act">Change</button></div>
        <div class="row"><div class="main"><div class="t">A folder for each year</div><div class="s">RxScan Receipts › 2026</div></div><span class="toggle"></span></div>
      </div>
      <h2>Install</h2>
      <div class="group">
        <div class="row"><div class="main"><div class="t">Add RxScan to this computer</div><div class="s">Opens in its own window, like an app</div></div><button class="act">Install</button></div>
      </div>
    </div>
  </div>
</main>""")

SCREENS["web-04-admin-people"] = webpage("People (web)", f"""{side("people")}
<main class="webmain">
  <div class="pagehead"><div><h1>People</h1><p class="sub">Only these Google accounts can sign in. Each person gets their own sheet and photo folder.</p></div></div>
  <div class="addbar"><input placeholder="name@gmail.com" aria-label="Email to add"><button class="btn btn-primary">Add person</button></div>
  <div class="tablewrap"><table class="people">
    <thead><tr><th>Person</th><th>Role</th><th>Sheet</th><th>Last used</th><th></th></tr></thead>
    <tbody>
      <tr><td><div class="who"><span class="ini">M</span><div><div class="t">Michael Strasser</div><div class="muted">mstras@gmail.com</div></div></div></td><td><span class="pill green">Admin</span></td><td>Prescriptions 2026</td><td>Today</td><td></td></tr>
      <tr><td><div class="who"><span class="ini">J</span><div><div class="t">Jenna Strasser</div><div class="muted">jenna.s@gmail.com</div></div></div></td><td>Member</td><td>RxScan – Jenna</td><td>Today</td><td><button class="act">Make admin</button> <button class="act red">Remove</button></td></tr>
      <tr><td><div class="who"><span class="ini">R</span><div><div class="t">Robert Strasser</div><div class="muted">rob.strasser@gmail.com</div></div></div></td><td>Member</td><td>Family meds</td><td>Nov 2</td><td><button class="act">Make admin</button> <button class="act red">Remove</button></td></tr>
      <tr><td><div class="who"><span class="ini">?</span><div><div class="t">grandma.ruth@gmail.com</div><div class="muted">Hasn't signed in yet</div></div></div></td><td><span class="pill amber">Invited</span></td><td>Made on first sign-in</td><td>Never</td><td><button class="act red">Remove</button></td></tr>
    </tbody></table></div>
  <p class="muted" style="margin-top:12px">Removing someone signs them out and stops RxScan from using their Google access. Their sheet and photos stay in their Drive.</p>
</main>""")

SCREENS["web-05-admin-google-setup"] = webpage("Google setup (web)", f"""{side("google")}
<main class="webmain">
  <div class="pagehead"><div><h1>Google setup</h1><p class="sub">One-time setup in Google Cloud so people can sign in with Google. About 15 minutes.</p></div>
    <div class="acts"><button class="btn btn-primary">Save and test</button></div></div>
  <div class="steps2">
    <div>
      <h2>In Google Cloud Console</h2>
      <div class="group">
        <div class="row"><div class="main"><div class="t">Turn on Sheets, Drive and Picker</div><div class="s">APIs & Services › Library</div></div><span class="pill green">Done</span></div>
        <div class="row"><div class="main"><div class="t">Set up the consent screen</div><div class="s">External, then Publish app so sign-ins don't expire weekly</div></div><span class="pill green">Done</span></div>
        <div class="row"><div class="main"><div class="t">Create a sign-in client</div><div class="s">Web application, with this redirect address:</div>
          <div class="copyline"><code>https://rxscan.strasserco.com/auth/google/callback</code><button class="act">Copy</button></div></div></div>
        <div class="row"><div class="main"><div class="t">Create an API key</div><div class="s">Restrict it to the Picker API</div></div><span class="pill amber">To do</span></div>
      </div>
    </div>
    <div>
      <h2>Values from Google Cloud</h2>
      <div class="fields">
        <div class="f full"><label>Client ID</label><input value="381920-abc.apps.googleusercontent.com"></div>
        <div class="f full"><label>Client secret</label><input value="••••••••••••Zq4x"></div>
        <div class="f"><label>API key</label><input class="blank" placeholder="AIza…"></div>
        <div class="f"><label>Project number</label><input class="blank" placeholder="12-digit number"></div>
      </div>
      <div class="notice wait">{ICON["clock"]}<div><strong>Two values left.</strong><p>Sign-in stays on the admin password until all four are saved and tested.</p></div></div>
    </div>
  </div>
</main>""")

SCREENS["web-06-scan-review-dark"] = SCREENS["web-02-scan-review"].replace('<html lang="en" class="light">', '<html lang="en" class="dark">')

for name, html in SCREENS.items():
    (OUT / f"{name}.html").write_text(html)
print(f"wrote {len(SCREENS)} screens to {OUT}")
