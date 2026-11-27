import AppKit
import SwiftUI
import WidgetKit

private enum SettingsStore {
    static let widgetKind = "local.commutedigest.reader.headlines"
    private static let originKey = "reading-site-origin"

    static var origin: URL? {
        UserDefaults.standard.string(forKey: originKey)
            .flatMap(SiteConnection.approvedOrigin)
    }

    static func save(_ credentials: SiteConnection) throws {
        try DigestKeychain.save(credentials)
        guard try DigestKeychain.read() == credentials else { throw CredentialError.keychainMismatch }
        UserDefaults.standard.set(credentials.origin.absoluteString, forKey: originKey)
        WidgetCenter.shared.reloadTimelines(ofKind: widgetKind)
    }
}

@main
private enum DigestApp {
    @MainActor
    static func main() {
        if CommandLine.arguments.dropFirst().elementsEqual(["--import-credentials-from-stdin"]) {
            importCredentials()
            return
        }

        let app = NSApplication.shared
        let delegate = DigestAppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.accessory)
        withExtendedLifetime(delegate) { app.run() }
    }

    private static func importCredentials() {
        do {
            guard let data = try FileHandle.standardInput.readToEnd(), data.count <= 8_192 else {
                throw CredentialError.invalidInput
            }
            let credentials = try JSONDecoder().decode(SiteConnection.self, from: data)
            try SettingsStore.save(credentials)
            print("credentials_configured=1")
        } catch CredentialError.keychain(let status) {
            FileHandle.standardError.write(Data("credentials_configured=0 keychain_status=\(status)\n".utf8))
            exit(EXIT_FAILURE)
        } catch {
            FileHandle.standardError.write(Data("credentials_configured=0\n".utf8))
            exit(EXIT_FAILURE)
        }
    }
}

@MainActor
private final class DigestAppDelegate: NSObject, NSApplicationDelegate {
    private var didHandleURL = false
    private var settingsWindow: NSWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Give Launch Services time to deliver a cold-launch widget URL before showing setup.
        DispatchQueue.main.asyncAfter(deadline: .now() + .milliseconds(250)) { [weak self] in
            guard let self, !self.didHandleURL else { return }
            self.showSettings()
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showSettings()
        return false
    }

    func application(_ application: NSApplication, open urls: [URL]) {
        let origin = SettingsStore.origin ?? (try? DigestKeychain.read())?.origin ?? SiteConnection.defaultOrigin
        for url in urls {
            if let destination = DigestLink.destination(for: url, origin: origin) {
                didHandleURL = true
                NSWorkspace.shared.open(destination)
                break
            }
        }
    }

    private func showSettings() {
        if let settingsWindow {
            settingsWindow.makeKeyAndOrderFront(nil)
        } else {
            let window = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 420, height: 270),
                styleMask: [.titled, .closable],
                backing: .buffered,
                defer: false
            )
            window.title = "通勤日报"
            window.contentViewController = NSHostingController(rootView: ConnectionSettings())
            window.isReleasedWhenClosed = false
            window.center()
            settingsWindow = window
            window.makeKeyAndOrderFront(nil)
        }
        NSApp.activate(ignoringOtherApps: true)
    }
}

private struct ConnectionSettings: View {
    @State private var address = (SettingsStore.origin ?? SiteConnection.defaultOrigin).absoluteString
    @State private var accountID = ""
    @State private var password = ""
    @State private var connected = false
    @State private var status = ""
    @State private var busy = false

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("连接日报网页")
                .font(.title2.weight(.semibold))
            TextField("日报网址", text: $address, prompt: Text("https://…"))
                .disabled(busy)
            TextField("用户 ID", text: $accountID, prompt: Text("8 位用户 ID")).disabled(busy)
            SecureField("账号密码", text: $password).disabled(busy)
            HStack {
                Text(status)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                Spacer()
                if connected {
                    Button("退出登录") {
                        do {
                            try DigestKeychain.clear()
                            password = ""
                            connected = false
                            status = "已退出小组件账号。"
                            WidgetCenter.shared.reloadTimelines(ofKind: SettingsStore.widgetKind)
                        } catch { status = error.localizedDescription }
                    }
                    .disabled(busy)
                }
                Button(busy ? "正在连接…" : "验证并保存") {
                    Task { await connect() }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(busy || address.isEmpty || accountID.isEmpty || password.isEmpty)
            }
        }
        .padding(24)
        .frame(width: 420)
        .task {
            if let existing = try? DigestKeychain.read() {
                address = existing.origin.absoluteString
                accountID = existing.accountID
                password = existing.password
                connected = true
            }
        }
    }

    @MainActor
    private func connect() async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        do {
            let connection = try SiteConnection(baseURL: address, accountID: accountID, password: password)
            let feed = try await DigestFeedClient.fetch(using: connection)
            try SettingsStore.save(connection)
            connected = true
            status = feed.map { "已连接，已读取个人第 \($0.issueNumber) 期。" } ?? "已连接，等待首次个人投送。"
        } catch let error as DigestFeedError {
            status = error.localizedDescription
        } catch let error as CredentialError {
            status = error.localizedDescription
        } catch {
            status = DigestFeedError.unavailable.localizedDescription
        }
    }
}
