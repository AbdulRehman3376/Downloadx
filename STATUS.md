# DownloadX — Status (kya hua / kya baqi hai)

## Step 1 — DONE (v0.2.0): asli download engine + desktop window
- [x] Multi-connection download (8 connections, 1-32 settings mein) — HTTP Range
- [x] IDM jaisi dynamic splitting: jo connection pehle khatam ho wo doosray ka aadha hissa utha leta hai
- [x] Pause / Resume (file ka jitna hissa aa chuka wahin se dobara shuru)
- [x] App band karke dobara kholne par bhi resume (queue `data/queue.json` mein save hoti hai)
- [x] Queue: max simultaneous downloads (default 3), baqi "Queued"
- [x] Progress bar, speed, ETA, status har download ka
- [x] Retry (8 baar, backoff ke sath), 404/403 par saaf error
- [x] Server jo Range support nahi karta: single stream mein download (pause par restart hota hai)
- [x] Add dialog: kai URLs ek sath, save folder, connections; clipboard se URL khud bhar jata hai
- [x] Right-click menu, double-click se file open / pause / resume, Remove (+ file delete)
- [x] Settings: default folder, connections, max simultaneous
- [x] Automatic tests (`run_tests.bat`): multi-conn, pause/resume, restart-resume, no-Range server, 404, queue — sab pass
- [x] GUI smoke test (virtual display) pass

## Step 2 — BAQI: Chrome se automatic capture (IDM jaisa)
- [ ] Extension `chrome.downloads` se Chrome ke har download ko pakde, cancel kare aur DownloadX ko bheje
- [ ] Native host ab sirf "helper" nahi, balkay chalti hui DownloadX app ko URL + referer + cookies forward kare (app band ho to khud khole)
- [ ] Popup mein on/off switch, minimum size filter, excluded sites
- [ ] Right-click "Download with DownloadX" (link / video / image)
- [ ] Video par floating "Download" button (purana wala, naye engine se jura hua)
- [ ] install.bat naye flow ke liye update

## Step 3 — BAQI: polish
- [ ] Categories (Video / Music / Documents / Programs / Other) aur har category ka alag folder
- [ ] Speed limiter, scheduler (raat ko download, shutdown after finish)
- [ ] Completed download par notification / sound; system tray icon
- [ ] Checksum (MD5/SHA256) verify option, proxy settings
- [ ] Segmented streams (HLS .m3u8) FFmpeg se; sirf apna / allowed content
- [ ] Single .exe + installer (PyInstaller / Inno Setup), clean Windows par test

## Maloom hadood
- Cookies sirf memory mein rakhi jati hain (security), isliye login-wale link app restart ke baad resume nahi honge.
- DRM / protected streams support nahi.
- Abhi Chrome ka automatic capture nahi hai (Step 2); abhi links app mein paste karne hain.

## Chalane ka tareeqa
1. Python 3 install (python.org, "Add to PATH" tick). Koi pip package nahi chahiye.
2. `DownloadX.bat` double-click (ya `py -3 app.py`).
3. **➕ Add URL** dabayen, link paste karein, **Download now**.
4. Files default mein `DownloadX\downloads\` mein jati hain (Settings se badal sakte hain, ya `DOWNLOADX_DIR`).
