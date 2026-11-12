// deskbuddy-appwatch: reports Chat and Cowork activity in the Claude desktop app to the Desk Buddy bridge.
//
// Chat and Cowork have no hooks, so every 2 s this reads the Claude app's accessibility tree and
// posts raw signals to the bridge (http://127.0.0.1:47823/app). All the rules for what counts as
// "working" or "needs you" live in the bridge (tools/buddy_bridge.py), so they can change without
// rebuilding this binary (a rebuild changes its signature and macOS asks for permission again).
//
// What it sends, per conversation page on screen (for Claude Code pages only the tabs, since hooks
// cover Code and its pages are large):
//   buttons  - button labels inside the page (e.g. "Stop response", "Skip", "Allow once")
//   statuses - text of the page's live status regions (e.g. "Claude is responding")
//   markers  - short static texts that look like status words (e.g. "Asking a question")
// and app-wide:
//   sidebar  - each sidebar row with a status badge: {row, status} (e.g. "Work priorities", "Awaiting answer")
//   tabs     - the mode tab labels (e.g. "Code, awaiting your input")
//   labels   - any short control label anywhere in the app (Code included) that looks like status
//              or usage info, e.g. "Usage: Weekly · all models: 25%, Resets Mon 8:00 AM". This is
//              deliberately broad so new signals only need changes in the bridge, not a rebuild.
// No message text is sent: static text is only included when it is short and matches status words.
//
// It runs as its own login item so only this small binary holds the Accessibility permission.
// If permission is missing it exits after a while so launchd restarts it and picks up a new grant.
// Build: swiftc -O -swift-version 5 main.swift -o deskbuddy-appwatch

import Cocoa
import ApplicationServices

let bundleID = "com.anthropic.claudefordesktop"
let endpoint = URL(string: "http://127.0.0.1:47823/app")!
let markerWords = try! NSRegularExpression(
    pattern: "\\b(asking|question|awaiting|waiting|approv|allow|permission|working|running|thinking|responding|needs|input)",
    options: .caseInsensitive)

func attr(_ e: AXUIElement, _ n: String) -> AnyObject? {
    var v: AnyObject?
    return AXUIElementCopyAttributeValue(e, n as CFString, &v) == .success ? v : nil
}
func text(_ e: AXUIElement, _ n: String) -> String { ((attr(e, n) as? String) ?? "").trimmingCharacters(in: .whitespacesAndNewlines) }
func label(_ e: AXUIElement) -> String { let t = text(e, kAXTitleAttribute as String); return t.isEmpty ? text(e, kAXDescriptionAttribute as String) : t }
func kids(_ e: AXUIElement) -> [AXUIElement] { (attr(e, kAXChildrenAttribute as String) as? [AXUIElement]) ?? [] }
func clip(_ s: String, _ n: Int = 80) -> String { s.count > n ? String(s.prefix(n)) : s }

struct Page: Codable { var buttons: [String] = []; var statuses: [String] = []; var markers: [String] = [] }
struct Row: Codable { var row: String; var status: String }
struct Snapshot: Codable { var trusted: Bool; var pages: [String: Page] = [:]; var sidebar: [Row] = []; var tabs: [String] = []; var labels: [String] = [] }
let labelWords = try! NSRegularExpression(
    pattern: "usage|limit|resets|% used|awaiting|needs (you|your)|approval|permission|working|running|responding|thinking|asking",
    options: .caseInsensitive)

func statusText(_ e: AXUIElement) -> String {
    var s = label(e)
    if s.isEmpty { s = kids(e).map { text($0, kAXValueAttribute as String) }.filter { !$0.isEmpty }.joined(separator: " ") }
    return clip(s)
}

func scan(_ app: AXUIElement, into snap: inout Snapshot) {
    // The section tabs ("Claude", "Code, awaiting your input") are what tell us about sections
    // that aren't showing, so they're collected everywhere, including inside Claude Code pages.
    func walk(_ e: AXUIElement, _ depth: Int, _ current: String?) {
        if depth > 45 { return }
        let role = text(e, kAXRoleAttribute as String), sub = text(e, kAXSubroleAttribute as String)
        var page = current
        if role == "AXWebArea" {
            let title = text(e, kAXTitleAttribute as String)
            page = title.hasSuffix("Claude Code") ? nil : title      // Code: hooks cover the content
            if let t = page, snap.pages[t] == nil { snap.pages[t] = Page() }
        } else if role == "AXRadioButton", !label(e).isEmpty {
            let l = label(e)
            if !snap.tabs.contains(l) { snap.tabs.append(clip(l)) }
        }
        if role == "AXButton" || role == "AXPopUpButton" || role == "AXMenuButton" || role == "AXStaticText" {
            var l = label(e)
            if l.isEmpty && role == "AXStaticText" { l = text(e, kAXValueAttribute as String) }
            if !l.isEmpty, l.count <= 100, snap.labels.count < 40, !snap.labels.contains(l),
               labelWords.firstMatch(in: l, range: NSRange(l.startIndex..., in: l)) != nil {
                snap.labels.append(l)
            }
        }
        if let p = page {
            if role == "AXButton" {
                let l = label(e)
                if !l.isEmpty { snap.pages[p]!.buttons.append(clip(l)) }
            } else if sub == "AXApplicationStatus" {
                let s = statusText(e)
                if !s.isEmpty {
                    snap.pages[p]!.statuses.append(s)
                    // a status badge next to a sidebar row button: pair them up
                    if let parent = attr(e, kAXParentAttribute as String) {
                        let rowButtons = kids(parent as! AXUIElement).filter { text($0, kAXRoleAttribute as String) == "AXButton" }.map(label)
                        if let r = rowButtons.first(where: { $0.hasSuffix(s) && $0.count > s.count }) {
                            snap.sidebar.append(Row(row: clip(String(r.dropLast(s.count)).trimmingCharacters(in: .whitespaces)), status: s))
                        }
                    }
                }
            } else if role == "AXStaticText" {
                let v = text(e, kAXValueAttribute as String)
                if !v.isEmpty, v.count <= 40, markerWords.firstMatch(in: v, range: NSRange(v.startIndex..., in: v)) != nil {
                    snap.pages[p]!.markers.append(v)
                }
            }
        }
        for c in kids(e) { walk(c, depth + 1, page) }
    }
    for w in (attr(app, kAXWindowsAttribute as String) as? [AXUIElement]) ?? [] { walk(w, 0, nil) }
}

func post(_ body: Data) {
    var req = URLRequest(url: endpoint, timeoutInterval: 1)
    req.httpMethod = "POST"; req.httpBody = body
    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
    let done = DispatchSemaphore(value: 0)
    URLSession.shared.dataTask(with: req) { _, _, _ in done.signal() }.resume()
    _ = done.wait(timeout: .now() + 2)
}

let trusted = AXIsProcessTrustedWithOptions(["AXTrustedCheckOptionPrompt": true] as CFDictionary)
let started = Date()
var lastBody = Data(), lastSent = Date.distantPast
while true {
    // macOS only applies a new Accessibility grant to a fresh process: if we don't have it yet,
    // report that, then exit after a minute so launchd restarts us and the grant takes effect.
    if !trusted && Date().timeIntervalSince(started) > 60 { exit(0) }
    autoreleasepool {
        var snap = Snapshot(trusted: trusted)
        if trusted, let app = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).first {
            let ax = AXUIElementCreateApplication(app.processIdentifier)
            AXUIElementSetAttributeValue(ax, "AXManualAccessibility" as CFString, kCFBooleanTrue)  // Electron: expose web content
            scan(ax, into: &snap)
        }
        let body = (try? JSONEncoder().encode(snap)) ?? Data()
        if body != lastBody || Date().timeIntervalSince(lastSent) > 30 {
            post(body); lastBody = body; lastSent = Date()
        }
    }
    Thread.sleep(forTimeInterval: 2)
}
