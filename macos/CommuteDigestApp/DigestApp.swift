import AppKit
import SwiftUI
import WidgetKit

private enum SettingsStore {
    static let widgetKind = "local.commutedigest.reader.headlines"
    private static let originKey = "reading-site-origin"

    static var origin: URL? {
        UserDefaults.standard.string(forKey: originKey)
            .flatMap(ReadingCredentials.approvedOrigin)
    }

    static func save(_ credentials: ReadingCredentials) throws {
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
            let credentials = try JSONDecoder().decode(ReadingCredentials.self, from: data)
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
        didHandleURL = true
        guard let origin = SettingsStore.origin ?? (try? DigestKeychain.read())?.origin else { return }
        for url in urls {
            if let destination = DigestLink(widgetURL: url)?.browserURL(origin: origin) {
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
                contentRect: NSRect(x: 0, y: 0, width: 420, height: 280),
                styleMask: [.titled, .closable],
                backing: .buffered,
                defer: false
            )
            window.title = "通勤晨报"
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
    @State private var address = SettingsStore.origin?.absoluteString ?? ""
    @State private var username = ""
    @State private var password = ""
    @State private var savedOrigin: URL?
    @State private var savedUsername: String?
    @State private var status = ""
    @State private var busy = false

    private var canUseSavedPassword: Bool {
        guard let savedOrigin, let savedUsername else { return false }
        return ReadingCredentials.approvedOrigin(address.trimmingCharacters(in: .whitespacesAndNewlines))
            == savedOrigin && username.trimmingCharacters(in: .whitespacesAndNewlines) == savedUsername
    }

    private var actionTitle: String {
        if busy { return "正在连接…" }
        return password.isEmpty && canUseSavedPassword ? "验证连接" : "保存"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("连接日报网页")
                .font(.title2.weight(.semibold))
            TextField("日报网址", text: $address, prompt: Text("https://…"))
                .disabled(busy)
            TextField("阅读账号", text: $username)
                .disabled(busy)
            SecureField("阅读密码", text: $password)
                .disabled(busy)
            HStack {
                Text(status)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                Spacer()
                Button(actionTitle) {
                    Task { await connect() }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(busy || address.isEmpty || username.isEmpty || (password.isEmpty && !canUseSavedPassword))
            }
        }
        .padding(24)
        .frame(width: 420)
        .task {
            if let existing = try? DigestKeychain.read() {
                address = existing.origin.absoluteString
                username = existing.username
                savedOrigin = existing.origin
                savedUsername = existing.username
                await connect()
            }
        }
    }

    @MainActor
    private func connect() async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        let usingSavedPassword = password.isEmpty
        do {
            let credentials: ReadingCredentials
            if usingSavedPassword {
                guard canUseSavedPassword, let existing = try DigestKeychain.read(),
                      existing.origin == savedOrigin, existing.username == savedUsername
                else { throw CredentialError.invalidInput }
                credentials = existing
            } else {
                credentials = try ReadingCredentials(baseURL: address, username: username, password: password)
            }
            let feed = try await DigestFeedClient.fetch(using: credentials)
            if usingSavedPassword {
                WidgetCenter.shared.reloadTimelines(ofKind: SettingsStore.widgetKind)
            } else {
                try SettingsStore.save(credentials)
                savedOrigin = credentials.origin
                savedUsername = credentials.username
            }
            password = ""
            status = feed.map { "已连接，已读取第 \($0.issueID) 期。" } ?? "已连接，等待首次投送。"
        } catch let error as DigestFeedError {
            status = error.localizedDescription
        } catch let error as CredentialError {
            status = error.localizedDescription
        } catch {
            status = DigestFeedError.unavailable.localizedDescription
        }
    }
}
