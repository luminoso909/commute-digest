import Foundation

enum DigestEdition: String, Codable {
    case morning, evening

    var label: String {
        self == .morning ? "晨报" : "晚报"
    }
}

enum DigestMode: String, Codable {
    case cross, focus
}

enum DigestLanguage: String, Codable {
    case zh, en
}

enum BeijingSchedule {
    static let calendar: Calendar = {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Asia/Shanghai") ?? .gmt
        return calendar
    }()

    static func edition(for slot: Date) -> DigestEdition? {
        let time = calendar.dateComponents([.hour, .minute, .second, .nanosecond], from: slot)
        guard time.minute == 0, time.second == 0, time.nanosecond == 0 else { return nil }
        switch time.hour {
        case 5: return .morning
        case 17: return .evening
        default: return nil
        }
    }

    static func refresh(after now: Date, latestSlot: Date?) -> Date {
        let start = calendar.startOfDay(for: now)
        let morning = start.addingTimeInterval(5 * 3_600)
        let evening = start.addingTimeInterval(17 * 3_600)
        let scheduled = [morning, evening, morning.addingTimeInterval(86_400)]
            .map { $0.addingTimeInterval(600) }
            .first { $0 > now } ?? now.addingTimeInterval(1_800)
        let latestExpected: Date
        if now >= evening.addingTimeInterval(600) {
            latestExpected = evening
        } else if now >= morning.addingTimeInterval(600) {
            latestExpected = morning
        } else {
            latestExpected = evening.addingTimeInterval(-86_400)
        }
        if let latestSlot, latestSlot >= latestExpected {
            return scheduled
        }
        return min(scheduled, now.addingTimeInterval(1_800))
    }
}

struct DigestFeed: Codable {
    let issueID: Int64
    let slot: Date
    let edition: DigestEdition
    let mode: DigestMode
    let language: DigestLanguage
    let titles: [String]

    enum CodingKeys: String, CodingKey {
        case issueID = "issue_id"
        case slot, edition, mode, language, titles
    }

    static func decode(_ data: Data) throws -> Self? {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        guard let feed = try decoder.decode(Self?.self, from: data) else { return nil }
        guard feed.issueID > 0, BeijingSchedule.edition(for: feed.slot) == feed.edition,
              (3...6).contains(feed.titles.count),
              feed.titles.allSatisfy({ !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty })
        else { throw DigestFeedError.invalidFeed }
        return feed
    }

    func encoded() throws -> Data {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        return try encoder.encode(self)
    }

    var displayDate: String {
        let date = BeijingSchedule.calendar.dateComponents([.month, .day], from: slot)
        return "\(date.month ?? 0)/\(date.day ?? 0)"
    }

    var link: DigestLink {
        DigestLink(issueID: issueID, mode: mode, language: language)
    }
}

struct DigestLink {
    static let scheme = "commute-digest-widget"

    let issueID: Int64
    let mode: DigestMode
    let language: DigestLanguage

    private var query: [URLQueryItem] {
        [
            URLQueryItem(name: "issue", value: String(issueID)),
            URLQueryItem(name: "mode", value: mode.rawValue),
            URLQueryItem(name: "lang", value: language.rawValue),
        ]
    }

    init(issueID: Int64, mode: DigestMode, language: DigestLanguage) {
        self.issueID = issueID
        self.mode = mode
        self.language = language
    }

    init?(widgetURL: URL) {
        guard let components = URLComponents(url: widgetURL, resolvingAgainstBaseURL: false),
              components.scheme == Self.scheme, components.host == "issue",
              components.path.isEmpty, components.user == nil, components.password == nil,
              components.port == nil, components.fragment == nil,
              let items = components.queryItems, items.count == 3
        else { return nil }
        var values: [String: String] = [:]
        for item in items {
            guard let value = item.value, values.updateValue(value, forKey: item.name) == nil else {
                return nil
            }
        }
        guard let issue = values["issue"], let issueID = Int64(issue), issueID > 0,
              issue == String(issueID),
              let mode = values["mode"].flatMap(DigestMode.init(rawValue:)),
              let language = values["lang"].flatMap(DigestLanguage.init(rawValue:))
        else { return nil }
        self.init(issueID: issueID, mode: mode, language: language)
    }

    var widgetURL: URL? {
        var url = URLComponents()
        url.scheme = Self.scheme
        url.host = "issue"
        url.queryItems = query
        return url.url
    }

    func browserURL(origin: URL) -> URL? {
        guard var url = URLComponents(url: origin, resolvingAgainstBaseURL: false) else { return nil }
        url.path = "/"
        url.queryItems = query
        return url.url
    }
}

enum DigestFeedError: LocalizedError {
    case invalidFeed, invalidCredentials, unavailable

    var errorDescription: String? {
        switch self {
        case .invalidFeed: return "服务器返回的标题数据无法读取。"
        case .invalidCredentials: return "阅读账号或密码有误。"
        case .unavailable: return "暂时无法连接日报网页。"
        }
    }
}

private final class StayOnEndpoint: NSObject, URLSessionTaskDelegate {
    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }
}

enum DigestFeedClient {
    static func fetch(using credentials: ReadingCredentials) async throws -> DigestFeed? {
        var request = URLRequest(
            url: credentials.origin.appending(path: "app/static/widget-latest.json"),
            cachePolicy: .reloadIgnoringLocalCacheData
        )
        let pair = Data("\(credentials.username):\(credentials.password)".utf8)
        request.setValue("Basic " + pair.base64EncodedString(), forHTTPHeaderField: "Authorization")
        request.setValue("true", forHTTPHeaderField: "ngrok-skip-browser-warning")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let configuration = URLSessionConfiguration.ephemeral
        configuration.urlCredentialStorage = nil
        configuration.timeoutIntervalForRequest = 8
        configuration.timeoutIntervalForResource = 12
        let session = URLSession(configuration: configuration, delegate: StayOnEndpoint(), delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        let (data, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw DigestFeedError.unavailable }
        switch response.statusCode {
        case 200:
            guard data.count <= 65_536 else { throw DigestFeedError.invalidFeed }
            return try DigestFeed.decode(data)
        case 401, 403:
            throw DigestFeedError.invalidCredentials
        default:
            throw DigestFeedError.unavailable
        }
    }
}
