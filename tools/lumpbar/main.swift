// Lump on the Mac: a small visual reminder that mirrors the desk Lump's mood.
//
// By default he only shows while you're away from the desk: he hides while your desk monitor is
// plugged in and the desk Lump is online (the Mac can't sense distance, but the desk Lump sits by a
// monitor, so "laptop on that monitor" is a good stand-in for "you're at your desk").
// Right-click him to pick your desk monitor (default: any external display) or to show him all the time.
// While he's hidden, open Lump again and he shows for a minute so you can reach that menu.
// On a MacBook with a notch he sits in a little black wing just right of the camera.
// With no notch screen he's a menu bar item instead.
// No pop-outs, no text: notifications already do that. Right-click him for options.
//
// Mood and colour come from the bridge (GET 127.0.0.1:47823/status, once a second).
// Frames come from tools/lumpbar/bake.py (copied into the app's Resources/frames).
// Build and launch: zsh tools/lumpbar/build.sh

import Cocoa

let STATUS_URL = URL(string: "http://127.0.0.1:47823/status")!
let DEFAULT_COLOUR = "FF7A45"
let PEEK: TimeInterval = 60             // opening the app again shows him this long, even at the desk
let DEVICE_GRACE: TimeInterval = 60     // the desk Lump has to be gone this long before he counts as away (OTA, Wi-Fi blips)

// MARK: - frames

/// The baked strips for one mood and ink, as premultiplied RGBA, painted any body colour on demand.
/// Every pixel is linear in the body colour: out = k + (w - k) * colour / 255 (k: body black, w: body white).
final class Strip {
    let k: [UInt8], w: [UInt8], width: Int, height: Int
    init?(k kURL: URL, w wURL: URL) {
        guard let a = Strip.pixels(kURL), let b = Strip.pixels(wURL), a.w == b.w, a.h == b.h else { return nil }
        k = a.px; w = b.px; width = a.w; height = a.h
    }
    static func pixels(_ url: URL) -> (px: [UInt8], w: Int, h: Int)? {
        guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
              let img = CGImageSourceCreateImageAtIndex(src, 0, nil) else { return nil }
        let w = img.width, h = img.height
        var px = [UInt8](repeating: 0, count: w * h * 4)
        let ok = px.withUnsafeMutableBytes { buf -> Bool in
            guard let ctx = CGContext(data: buf.baseAddress, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                                      space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return false }
            ctx.draw(img, in: CGRect(x: 0, y: 0, width: w, height: h)); return true
        }
        return ok ? (px, w, h) : nil
    }
    /// Every frame of the strip with the body painted `rgb`.
    func painted(_ rgb: (Int, Int, Int), size: Int) -> [CGImage] {
        var out = [UInt8](repeating: 0, count: k.count)
        let t = [rgb.0, rgb.1, rgb.2]
        for i in stride(from: 0, to: k.count, by: 4) {
            for c in 0..<3 { out[i + c] = UInt8(max(0, min(255, Int(k[i + c]) + (Int(w[i + c]) - Int(k[i + c])) * t[c] / 255))) }
            out[i + 3] = k[i + 3]
        }
        guard let provider = CGDataProvider(data: Data(out) as CFData),
              let full = CGImage(width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: width * 4,
                                 space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                 bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue),
                                 provider: provider, decode: nil, shouldInterpolate: true, intent: .defaultIntent) else { return [] }
        return (0..<(width / size)).compactMap { full.cropping(to: CGRect(x: $0 * size, y: 0, width: size, height: size)) }
    }
}

final class Sprites {
    struct Mood: Decodable { let frames: Int; let ticks: [Int] }
    struct Manifest: Decodable { let size: Int; let fps: Int; let moods: [String: Mood] }
    let manifest: Manifest, dir: URL
    private var strips: [String: Strip] = [:]
    private var painted: [String: [CGImage]] = [:]

    init?(dir: URL) {
        guard let data = try? Data(contentsOf: dir.appendingPathComponent("manifest.json")),
              let m = try? JSONDecoder().decode(Manifest.self, from: data) else { return nil }
        manifest = m; self.dir = dir
    }
    func ticks(_ mood: String) -> [Int] { manifest.moods[mood]?.ticks ?? [] }
    func frames(_ mood: String, ink: String, colour: String) -> [CGImage] {
        let key = "\(mood)-\(ink)-\(colour)"
        if let f = painted[key] { return f }
        let sk = "\(mood)-\(ink)"
        if strips[sk] == nil {
            strips[sk] = Strip(k: dir.appendingPathComponent("\(sk)-k.png"), w: dir.appendingPathComponent("\(sk)-w.png"))
        }
        guard let strip = strips[sk] else { return [] }
        if painted.count > 24 { painted.removeAll() }      // the meeting countdown slides the colour: don't hoard
        let f = strip.painted(rgb(colour), size: manifest.size)
        painted[key] = f
        return f
    }
    func rgb(_ hex: String) -> (Int, Int, Int) {
        let v = Int(hex, radix: 16) ?? 0xFF7A45
        return ((v >> 16) & 255, (v >> 8) & 255, v & 255)
    }
}

// MARK: - views

/// The black wing that sticks out of the right side of the notch, with Lump in it.
final class WingView: NSView {
    var image: CGImage? { didSet { needsDisplay = true } }
    var overlap: CGFloat = 12        // how far the wing reaches under the notch, to fill its rounded corner
    var dim: CGFloat = 1 { didSet { needsDisplay = true } }
    var onRightClick: ((NSEvent) -> Void)?

    override func draw(_ dirtyRect: NSRect) {
        let b = bounds, r = min(10, b.height * 0.3)
        let p = NSBezierPath()
        p.move(to: NSPoint(x: 0, y: b.maxY)); p.line(to: NSPoint(x: b.maxX, y: b.maxY))
        p.line(to: NSPoint(x: b.maxX, y: r))
        p.appendArc(withCenter: NSPoint(x: b.maxX - r, y: r), radius: r, startAngle: 0, endAngle: 270, clockwise: true)
        p.line(to: NSPoint(x: 0, y: 0)); p.close()
        NSColor.black.setFill(); p.fill()
        guard let img = image, let ctx = NSGraphicsContext.current?.cgContext else { return }
        let side = b.height - 2, x = overlap + (b.width - overlap - side) / 2
        ctx.interpolationQuality = .high
        ctx.setAlpha(dim)
        ctx.draw(img, in: CGRect(x: x, y: 1, width: side, height: side))
    }
    override func rightMouseDown(with event: NSEvent) { onRightClick?(event) }
    override func mouseDown(with event: NSEvent) { if event.modifierFlags.contains(.control) { onRightClick?(event) } }
}

// MARK: - app

final class App: NSObject, NSApplicationDelegate, NSMenuDelegate {
    var sprites: Sprites!
    var mood = "idle", colour = DEFAULT_COLOUR, online = false
    var deviceSeen = Date.distantPast, atDesk = false, placed = false
    var tick = 0, shownKey = "", peekUntil = Date.distantPast
    var wing: NSPanel?, wingView: WingView?
    var item: NSStatusItem?
    let prefs = UserDefaults.standard
    var preferMenuBar: Bool { get { prefs.bool(forKey: "menuBarOnly") } set { prefs.set(newValue, forKey: "menuBarOnly") } }
    var showAlways: Bool { get { prefs.bool(forKey: "showAlways") } set { prefs.set(newValue, forKey: "showAlways") } }
    /// The monitor the desk Lump lives by, as macOS names it; nil = any external display.
    /// LUMPBAR_DESK_DISPLAY overrides it (for testing "away" while sitting at the desk).
    var deskDisplay: String? {
        get { ProcessInfo.processInfo.environment["LUMPBAR_DESK_DISPLAY"] ?? prefs.string(forKey: "deskDisplay") }
        set { prefs.set(newValue, forKey: "deskDisplay") }
    }
    lazy var theMenu: NSMenu = { let m = NSMenu(); m.delegate = self; return m }()

    func applicationDidFinishLaunching(_ n: Notification) {
        guard let dir = Bundle.main.url(forResource: "frames", withExtension: nil), let s = Sprites(dir: dir) else {
            NSLog("lumpbar: no frames in the app bundle (run tools/lumpbar/bake.py, then build.sh)"); NSApp.terminate(nil); return
        }
        sprites = s                 // placed after the first poll, so he doesn't flash up at the desk
        if ProcessInfo.processInfo.environment["LUMPBAR_PRINT_MENU"] != nil {   // for checking the menu without clicking
            menuNeedsUpdate(theMenu)
            func dump(_ m: NSMenu, _ pad: String) {
                for it in m.items {
                    print(pad + (it.isSeparatorItem ? "----" : (it.state == .on ? "✓ " : "  ") + it.title + (it.isEnabled ? "" : "  (info)")))
                    if let sub = it.submenu { sub.delegate?.menuNeedsUpdate?(sub); dump(sub, pad + "    ") }
                }
            }
            dump(theMenu, ""); exit(0)
        }
        NotificationCenter.default.addObserver(self, selector: #selector(place), name: NSApplication.didChangeScreenParametersNotification, object: nil)
        let anim = Timer(timeInterval: 1.0 / Double(s.manifest.fps), repeats: true) { [weak self] _ in self?.step() }
        RunLoop.main.add(anim, forMode: .common)
        let poll = Timer(timeInterval: 1, repeats: true) { [weak self] _ in self?.poll() }
        RunLoop.main.add(poll, forMode: .common)
        if !prefs.bool(forKey: "launchedBefore") {      // first launch: let a new user see where he lives
            prefs.set(true, forKey: "launchedBefore"); peekUntil = Date().addingTimeInterval(PEEK)
        }
        poll.fire()
    }

    /// Opening the app while it's running (Finder, Spotlight, `open`): show him for a minute, even at the desk.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        peekUntil = Date().addingTimeInterval(PEEK); place(); return false
    }

    // MARK: where he lives

    /// The built-in screen with a notch, if one is on: (screen, menu bar height, global x of the notch's right edge).
    /// LUMPBAR_FAKE_NOTCH=1 pretends the main screen has a 200 pt notch, for trying the wing on any Mac.
    func notch() -> (screen: NSScreen, height: CGFloat, right: CGFloat)? {
        if ProcessInfo.processInfo.environment["LUMPBAR_FAKE_NOTCH"] != nil, let s = NSScreen.main {
            return (s, s.frame.maxY - s.visibleFrame.maxY, s.frame.midX + 100)
        }
        guard let s = NSScreen.screens.first(where: { $0.safeAreaInsets.top > 0 && $0.auxiliaryTopRightArea != nil }) else { return nil }
        let f = s.frame
        var right = s.auxiliaryTopRightArea!
        // that rect may be relative to the screen or global: it always runs to the screen's right edge
        if abs(right.maxX - f.width) < 1 && abs(right.maxX - f.maxX) >= 1 { right.origin.x += f.minX }
        return (s, s.safeAreaInsets.top, right.minX)
    }

    func externalDisplays() -> [String] {
        NSScreen.screens.filter { s in
            CGDisplayIsBuiltin(s.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? CGDirectDisplayID ?? 0) == 0
        }.map { $0.localizedName }
    }
    func deskDisplayConnected() -> Bool {
        let shown = externalDisplays()
        if let want = deskDisplay { return shown.contains(want) }
        return !shown.isEmpty
    }
    /// At the desk = desk monitor plugged in and the desk Lump heard from in the last minute.
    func computeAtDesk() -> Bool {
        !showAlways && Date() >= peekUntil && deskDisplayConnected() && Date().timeIntervalSince(deviceSeen) < DEVICE_GRACE
    }

    @objc func place() {
        atDesk = computeAtDesk()
        if atDesk { hideWing(); hideItem(); shownKey = ""; return }   // the real one is right there
        if !preferMenuBar, let n = notch() { showWing(n); hideItem() } else { hideWing(); showItem() }
        shownKey = ""
    }

    func showWing(_ n: (screen: NSScreen, height: CGFloat, right: CGFloat)) {
        let f = n.screen.frame, h = n.height
        let overlap: CGFloat = 12, w = h + 6
        let frame = NSRect(x: n.right - overlap, y: f.maxY - h, width: w + overlap, height: h)
        if wing == nil {
            let p = NSPanel(contentRect: frame, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
            p.isOpaque = false; p.backgroundColor = .clear; p.hasShadow = false
            p.level = .statusBar
            p.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle, .fullScreenAuxiliary]
            p.isReleasedWhenClosed = false
            let v = WingView(frame: NSRect(origin: .zero, size: frame.size))
            v.autoresizingMask = [.width, .height]
            v.onRightClick = { [weak self] e in self?.popMenu(e, in: v) }
            p.contentView = v
            wing = p; wingView = v
        }
        wingView!.overlap = overlap
        wing!.setFrame(frame, display: true)
        wing!.orderFrontRegardless()
    }
    func hideWing() { wing?.orderOut(nil) }

    func showItem() {
        if item == nil {
            let it = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
            it.button?.imageScaling = .scaleProportionallyUpOrDown
            it.menu = theMenu
            item = it
        }
        item?.isVisible = true
    }
    func hideItem() { if let it = item { NSStatusBar.system.removeStatusItem(it); item = nil } }

    // MARK: menu (rebuilt each time it opens, so the monitor list is current)

    func menuNeedsUpdate(_ m: NSMenu) {
        m.removeAllItems()
        func add(_ title: String, _ action: Selector?, on: Bool = false, to menu: NSMenu = m, rep: Any? = nil) {
            let it = NSMenuItem(title: title, action: action, keyEquivalent: "")
            it.target = self; it.state = on ? .on : .off; it.representedObject = rep; it.isEnabled = action != nil
            menu.addItem(it)
        }
        add("Only When I'm Away From My Desk", #selector(setShowAway), on: !showAlways)
        add("All the Time", #selector(setShowAlways), on: showAlways)
        m.addItem(.separator())

        let desk = NSMenu()
        add("Any External Display", #selector(pickDesk(_:)), on: deskDisplay == nil, to: desk)
        var names = externalDisplays()
        if let d = deskDisplay, !names.contains(d) { names.insert(d, at: 0) }   // remembered, just not plugged in now
        if !names.isEmpty { desk.addItem(.separator()) }
        for n in names { add(n, #selector(pickDesk(_:)), on: n == deskDisplay, to: desk, rep: n) }
        let deskItem = NSMenuItem(title: "My Desk Monitor", action: nil, keyEquivalent: "")
        deskItem.submenu = desk
        m.addItem(deskItem)
        add(atDeskHint(), nil)
        m.addItem(.separator())

        add("Always in the Menu Bar", #selector(toggleMenuBar), on: preferMenuBar)
        m.addItem(.separator())
        m.addItem(NSMenuItem(title: "Quit Lump", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
    }
    func atDeskHint() -> String {
        if showAlways { return "Showing all the time" }
        let mon = deskDisplayConnected(), dev = Date().timeIntervalSince(deviceSeen) < DEVICE_GRACE
        if mon && dev { return Date() < peekUntil ? "At your desk: hiding in a moment" : "At your desk" }
        return !mon ? "Away: desk monitor not plugged in" : "Away: desk Lump is offline"
    }
    func popMenu(_ e: NSEvent, in v: NSView) { NSMenu.popUpContextMenu(theMenu, with: e, for: v) }
    @objc func toggleMenuBar() { preferMenuBar.toggle(); place() }
    @objc func setShowAway() { showAlways = false; place() }
    @objc func setShowAlways() { showAlways = true; place() }
    @objc func pickDesk(_ sender: NSMenuItem) { deskDisplay = sender.representedObject as? String; place() }

    // MARK: mood

    func poll() {
        var req = URLRequest(url: STATUS_URL); req.timeoutInterval = 0.8
        URLSession.shared.dataTask(with: req) { data, _, _ in
            let j = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            DispatchQueue.main.async {
                defer { if !self.placed || self.computeAtDesk() != self.atDesk { self.placed = true; self.place() } }
                guard let j = j else { self.online = false; return }
                self.online = true
                if let m = j["mood"] as? String, self.sprites.manifest.moods[m] != nil, m != self.mood { self.mood = m; self.tick = 0 }
                if let c = j["colour"] as? String, c.count == 6 { self.colour = c.uppercased() }
                if j["device"] as? Bool == true { self.deviceSeen = Date() }
            }
        }.resume()
    }

    func step() {
        let ticks = sprites.ticks(mood)
        guard !ticks.isEmpty else { return }
        let idx = ticks[tick % ticks.count]; tick += 1
        let dark: Bool
        if let b = item?.button { dark = b.effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua } else { dark = true }
        let ink = (wing?.isVisible ?? false) || dark ? "glow" : "paper"   // the wing is always black
        let dim: CGFloat = !online ? 0.4 : mood == "night" ? 0.55 : 1     // asleep at night, faded when the bridge is down
        let key = "\(mood)-\(ink)-\(colour)-\(idx)-\(dim)"
        if key == shownKey { return }
        shownKey = key
        let frames = sprites.frames(mood, ink: ink, colour: colour)
        guard idx < frames.count else { return }
        if let v = wingView, wing?.isVisible == true { v.dim = dim; v.image = frames[idx] }
        if let b = item?.button {
            let side = NSStatusBar.system.thickness - 2
            let img = NSImage(cgImage: frames[idx], size: NSSize(width: side, height: side))
            b.image = img; b.alphaValue = dim
        }
    }
}

let app = NSApplication.shared
let delegate = App()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
