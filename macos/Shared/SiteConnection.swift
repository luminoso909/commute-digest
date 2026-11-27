import Foundation
import Security

struct SiteConnection: Codable, Equatable {
    let origin: URL
    enum CodingKeys: String, CodingKey {
        case origin = "baseURL"
    }

    init(baseURL: String) throws {
        guard let origin = Self.approvedOrigin(baseURL.trimmingCharacters(in: .whitespacesAndNewlines))
        else { throw CredentialError.invalidInput }
        self.origin = origin
    }

    init(from decoder: Decoder) throws {
        // Earlier versions stored extra fields alongside the website URL.
        let data = try decoder.container(keyedBy: CodingKeys.self)
        try self.init(baseURL: data.decode(String.self, forKey: .origin))
    }

    static func approvedOrigin(_ baseURL: String) -> URL? {
        guard var parts = URLComponents(string: baseURL), parts.scheme == "https",
              let host = parts.host, !host.isEmpty, parts.user == nil, parts.password == nil,
              parts.port == nil || parts.port == 443, parts.query == nil, parts.fragment == nil,
              parts.path.isEmpty || parts.path == "/"
        else { return nil }
        parts.port = nil
        parts.path = ""
        return parts.url
    }
}

enum CredentialError: LocalizedError {
    case invalidInput, keychain(OSStatus), missingTargets, keychainMismatch

    var errorDescription: String? {
        switch self {
        case .invalidInput: return "请填写日报的 HTTPS 网址。"
        case .keychain, .keychainMismatch: return "无法访问 macOS 钥匙串。请解锁登录钥匙串后重试。"
        case .missingTargets: return "找不到安装包内的小组件，请重新安装宿主 App。"
        }
    }
}

enum DigestKeychain {
    private static let query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "local.commutedigest.reader.widget",
        kSecAttrAccount as String: "reading-credentials",
        kSecUseDataProtectionKeychain as String: false,
    ]

    static func read(promptIfNeeded: Bool = false) throws -> SiteConnection? {
        var lookup = query
        if !promptIfNeeded {
            lookup[kSecUseAuthenticationUI as String] = kSecUseAuthenticationUIFail
        }
        lookup[kSecReturnData as String] = true
        lookup[kSecMatchLimit as String] = kSecMatchLimitOne
        var value: CFTypeRef?
        let status = SecItemCopyMatching(lookup as CFDictionary, &value)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = value as? Data else {
            throw CredentialError.keychain(status)
        }
        return try JSONDecoder().decode(SiteConnection.self, from: data)
    }

    static func save(_ credentials: SiteConnection) throws {
        let access = try packagedAppAccess()
        let attributes: [String: Any] = [
            kSecValueData as String: try JSONEncoder().encode(credentials),
            kSecAttrAccess as String: access,
        ]
        let status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            var item = query.merging(attributes) { _, new in new }
            item[kSecAttrLabel as String] = "通勤日报网址"
            try check(SecItemAdd(item as CFDictionary, nil))
        } else {
            try check(status)
        }
    }

    private static func packagedAppAccess() throws -> SecAccess {
        guard let app = Bundle.main.executableURL,
              let plugIns = Bundle.main.builtInPlugInsURL,
              let widget = Bundle(url: plugIns.appending(path: "CommuteDigestWidget.appex"))?.executableURL
        else { throw CredentialError.missingTargets }

        let trusted = try [app, widget].map { executable -> SecTrustedApplication in
            var result: SecTrustedApplication?
            let status = executable.path.withCString { SecTrustedApplicationCreateFromPath($0, &result) }
            try check(status)
            guard let result else { throw CredentialError.missingTargets }
            return result
        }
        var access: SecAccess?
        try check(SecAccessCreate("通勤日报网址" as CFString, trusted as CFArray, &access))
        guard let access else { throw CredentialError.missingTargets }
        return access
    }

    private static func check(_ status: OSStatus) throws {
        guard status == errSecSuccess else { throw CredentialError.keychain(status) }
    }
}
