import SwiftUI
import WidgetKit

private struct DigestEntry: TimelineEntry {
    let date: Date
    let feed: DigestFeed?
    var isPreview = false
}

private struct DigestTimeline: TimelineProvider {
    private static let cacheKey = "personal-delivered-digest-v2"

    private struct CachedFeed: Codable {
        let origin: URL
        let accountID: String
        let data: Data
    }

    func placeholder(in context: Context) -> DigestEntry {
        DigestEntry(date: .now, feed: example, isPreview: true)
    }

    func getSnapshot(in context: Context, completion: @escaping (DigestEntry) -> Void) {
        let current = context.isPreview ? example : cached(for: try? DigestKeychain.read())
        completion(DigestEntry(date: .now, feed: current, isPreview: context.isPreview))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<DigestEntry>) -> Void) {
        Task {
            let credentials = try? DigestKeychain.read()
            var latest = cached(for: credentials)
            if let credentials {
                do {
                    latest = try await DigestFeedClient.fetch(using: credentials)
                    if let data = try latest?.encoded() {
                        let record = CachedFeed(origin: credentials.origin, accountID: credentials.accountID, data: data)
                        UserDefaults.standard.set(try JSONEncoder().encode(record), forKey: Self.cacheKey)
                    } else {
                        UserDefaults.standard.removeObject(forKey: Self.cacheKey)
                    }
                } catch DigestFeedError.invalidCredentials {
                    latest = nil
                    UserDefaults.standard.removeObject(forKey: Self.cacheKey)
                } catch {
                    // A transient outage can use this account's last successful response.
                }
            }
            let now = Date()
            let entry = DigestEntry(date: now, feed: latest)
            completion(Timeline(entries: [entry], policy: .after(BeijingSchedule.refresh(after: now, latestSlot: latest?.slot))))
        }
    }

    private func cached(for credentials: SiteConnection?) -> DigestFeed? {
        UserDefaults.standard.removeObject(forKey: "latest-delivered-digest-v1")
        guard let credentials else {
            UserDefaults.standard.removeObject(forKey: Self.cacheKey)
            return nil
        }
        guard let data = UserDefaults.standard.data(forKey: Self.cacheKey),
              let record = try? JSONDecoder().decode(CachedFeed.self, from: data),
              credentials.origin == record.origin, credentials.accountID == record.accountID
        else { return nil }
        return try? DigestFeed.decode(record.data)
    }

    private var example: DigestFeed {
        let calendar = BeijingSchedule.calendar
        let date = calendar.startOfDay(for: .now).addingTimeInterval(5 * 3_600)
        return DigestFeed(
            issueID: 1, issueNumber: 1,
            slot: date,
            edition: .morning,
            mode: .cross,
            language: .zh,
            titles: [
                "城市轨道交通增加周末班次，热门线路夜间运营时间延长",
                "研究团队公开新的观测数据，帮助追踪近海水温变化",
                "行业协会发布年度报告，梳理中小企业数字化进展和挑战",
                "社区公共图书馆增设预约座位和非工作时间自助归还服务",
                "新一批低功耗计算芯片进入量产阶段，面向智能终端应用",
                "多地开展候鸟栖息地监测，调查结果将用于恢复湿地植被",
            ]
        )
    }
}

private struct DigestView: View {
    let entry: DigestEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(verbatim: entry.feed?.displayDate ?? "–/–")
                    .font(.system(size: 28, weight: .semibold))
                    .monospacedDigit()
                if let feed = entry.feed {
                    Text(verbatim: feed.edition.label)
                        .font(.system(size: 14, weight: .medium))
                        .foregroundStyle(.secondary)
                }
            }
            .accessibilityElement(children: .combine)

            if let feed = entry.feed {
                ViewThatFits(in: .vertical) {
                    titles(feed.titles, fontSize: 15, spacing: 9)
                    titles(feed.titles, fontSize: 14, spacing: 7)
                    titles(feed.titles, fontSize: 12, spacing: 6)
                    titles(feed.titles, fontSize: 12, spacing: 6, lineLimit: 2)
                    titles(feed.titles, fontSize: 12, spacing: 6, lineLimit: 1)
                }
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .containerBackground(for: .widget) {
            Rectangle().fill(.background)
        }
        .widgetURL(entry.isPreview ? nil : entry.feed?.link.widgetURL)
    }

    private func titles(_ titles: [String], fontSize: CGFloat, spacing: CGFloat, lineLimit: Int? = nil) -> some View {
        VStack(alignment: .leading, spacing: spacing) {
            ForEach(titles.indices, id: \.self) { index in
                Text(verbatim: titles[index])
                    .font(.system(size: fontSize))
                    .lineLimit(lineLimit)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityLabel(Text(verbatim: titles[index]))
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}

@main
struct HeadlinesWidget: Widget {
    private let kind = "local.commutedigest.reader.headlines"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: DigestTimeline()) { entry in
            DigestView(entry: entry)
        }
        .configurationDisplayName("通勤晨报")
        .description("打开最近投送的晨报或晚报。")
        .supportedFamilies([.systemLarge])
    }
}
