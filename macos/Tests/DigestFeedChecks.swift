import Foundation

@main
struct DigestFeedChecks {
    static func main() throws {
        let threeTitles = ["第一条新闻", "第二条新闻", "第三条新闻"]
        let morning = try feed(edition: "morning", slot: "2026-11-24T05:00:00+08:00", titles: threeTitles)
        precondition(morning.displayDate == "11/24" && morning.edition.label == "晨报")
        precondition(morning.titles == threeTitles)

        let sixTitles = threeTitles + ["第四条新闻", "第五条新闻", "第六条新闻"]
        let evening = try feed(edition: "evening", slot: "2026-11-24T17:00:00+08:00", titles: sixTitles)
        precondition(evening.displayDate == "11/24" && evening.edition.label == "晚报")
        let cached = try DigestFeed.decode(evening.encoded())
        precondition(cached?.slot == evening.slot && cached?.titles == sixTitles)
        let empty = try DigestFeed.decode(Data("null".utf8))
        precondition(empty == nil)
        let inconsistent = try? feed(edition: "morning", slot: "2026-11-24T17:00:00+08:00", titles: threeTitles)
        let tooFew = try? feed(edition: "morning", slot: "2026-11-24T05:00:00+08:00", titles: ["标题"])
        precondition(inconsistent == nil && tooFew == nil)

        let link = try require(DigestLink(widgetURL: require(evening.link.widgetURL)))
        let target = try require(link.browserURL(origin: require(URL(string: "https://news.example"))))
        precondition(target.absoluteString == "https://news.example/?issue=42&mode=focus&lang=en")
        for route in [
            "https://issue?issue=42&mode=focus&lang=en",
            "commute-digest-widget://remote?issue=42&mode=focus&lang=en",
            "commute-digest-widget://issue?issue=42&mode=cross&lang=en&mode=focus",
            "commute-digest-widget://issue?issue=-42&mode=focus&lang=en",
        ] {
            let url = try require(URL(string: route))
            precondition(DigestLink(widgetURL: url) == nil)
        }

        let nextEvening = try date("2026-11-24T17:10:00+08:00")
        let justBeforeEvening = try date("2026-11-24T17:04:00+08:00")
        let staleRetry = try date("2026-11-24T17:40:00+08:00")
        let tomorrowMorning = try date("2026-11-25T05:10:00+08:00")
        precondition(BeijingSchedule.refresh(after: justBeforeEvening, latestSlot: morning.slot) == nextEvening)
        precondition(BeijingSchedule.refresh(after: nextEvening, latestSlot: morning.slot) == staleRetry)
        precondition(BeijingSchedule.refresh(after: nextEvening, latestSlot: evening.slot) == tomorrowMorning)

        let credentials = try ReadingCredentials(baseURL: "https://news.example:443/", username: "reader", password: "example")
        let restored = try JSONDecoder().decode(ReadingCredentials.self, from: JSONEncoder().encode(credentials))
        precondition(restored == credentials && restored.origin.absoluteString == "https://news.example")
        precondition(ReadingCredentials.approvedOrigin("https://reader:secret@news.example") == nil)
        precondition(ReadingCredentials.approvedOrigin("https://news.example/unsafe") == nil)
        precondition(ReadingCredentials.approvedOrigin("http://news.example") == nil)
        print("Digest feed, deep links and Beijing refresh checks passed")
    }

    private static func date(_ value: String) throws -> Date {
        try require(ISO8601DateFormatter().date(from: value))
    }

    private static func feed(edition: String, slot: String, titles: [String]) throws -> DigestFeed {
        let fields: [String: Any] = [
            "issue_id": 42, "edition": edition, "slot": slot,
            "mode": "focus", "language": "en", "titles": titles,
        ]
        return try require(DigestFeed.decode(JSONSerialization.data(withJSONObject: fields)))
    }

    private static func require<T>(_ value: T?) throws -> T {
        guard let value else { throw CheckError.missingValue }
        return value
    }

    private enum CheckError: Error {
        case missingValue
    }
}
