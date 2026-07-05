import WidgetKit
import SwiftUI

// MARK: - App Group ID (muss mit Haupt-App übereinstimmen)

private let appGroupID = "group.de.nimtz.mysecureprint"

// MARK: - Timeline Provider

struct PrintJobStatusEntry: TimelineEntry {
    let date: Date
    let state: WidgetJobState
}

struct PrintJobStatusProvider: TimelineProvider {
    func placeholder(in context: Context) -> PrintJobStatusEntry {
        PrintJobStatusEntry(
            date: Date(),
            state: WidgetJobState(pendingCount: 1,
                                  lastFilename: "Dokument.pdf",
                                  lastStatus: "forwarded",
                                  lastQueue: "Office",
                                  updatedAt: Date())
        )
    }

    func getSnapshot(in context: Context, completion: @escaping (PrintJobStatusEntry) -> Void) {
        completion(PrintJobStatusEntry(date: Date(),
                                       state: WidgetJobState.load(appGroupID: appGroupID)))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<PrintJobStatusEntry>) -> Void) {
        let state = WidgetJobState.load(appGroupID: appGroupID)
        let entry = PrintJobStatusEntry(date: Date(), state: state)
        // Refresh nach 15 Min (oder früher via WidgetCenter.reloadTimelines aus der App)
        let next = Calendar.current.date(byAdding: .minute, value: 15, to: Date())!
        completion(Timeline(entries: [entry], policy: .after(next)))
    }
}

// MARK: - Widget Definition

struct PrintJobStatusWidget: Widget {
    let kind = "PrintJobStatusWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: PrintJobStatusProvider()) { entry in
            PrintJobStatusWidgetView(entry: entry)
                .containerBackground(Color(.systemBackground), for: .widget)
        }
        .configurationDisplayName("Druckstatus")
        .description("Letzter Druckauftrag und ausstehende Jobs.")
        .supportedFamilies([.accessoryRectangular, .accessoryCircular, .accessoryInline])
    }
}

// MARK: - Views

struct PrintJobStatusWidgetView: View {
    let entry: PrintJobStatusEntry
    @Environment(\.widgetFamily) private var family

    var body: some View {
        switch family {
        case .accessoryRectangular: rectangularView
        case .accessoryCircular:    circularView
        case .accessoryInline:      inlineView
        default:                    rectangularView
        }
    }

    // ── Rectangular (2 Zeilen, breit) ─────────────────────────────────────

    private var rectangularView: some View {
        let state = entry.state
        return VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 4) {
                Image(systemName: "printer.fill")
                    .font(.caption2)
                if state.pendingCount > 0 {
                    Text("\(state.pendingCount) ausstehend")
                        .font(.caption2)
                        .fontWeight(.semibold)
                } else {
                    Text("Secure Print")
                        .font(.caption2)
                        .fontWeight(.semibold)
                }
                Spacer()
            }
            .widgetAccentable()

            if let filename = state.lastFilename {
                HStack(spacing: 4) {
                    Image(systemName: state.statusIcon)
                        .font(.caption2)
                    Text(filename)
                        .font(.caption)
                        .lineLimit(1)
                }
            } else {
                Text("Kein Job")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if let queue = state.lastQueue {
                Text(queue)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
    }

    // ── Circular (Icon + Zahl) ─────────────────────────────────────────────

    private var circularView: some View {
        let state = entry.state
        return ZStack {
            if state.pendingCount > 0 {
                VStack(spacing: 1) {
                    Image(systemName: "printer.fill")
                        .font(.body)
                        .widgetAccentable()
                    Text("\(state.pendingCount)")
                        .font(.caption2)
                        .fontWeight(.bold)
                }
            } else {
                VStack(spacing: 1) {
                    Image(systemName: state.statusIcon)
                        .font(.title3)
                        .widgetAccentable()
                }
            }
        }
    }

    // ── Inline (eine Zeile) ────────────────────────────────────────────────

    private var inlineView: some View {
        let state = entry.state
        return HStack(spacing: 4) {
            Image(systemName: state.pendingCount > 0 ? "printer.fill" : state.statusIcon)
            if state.pendingCount > 0 {
                Text("\(state.pendingCount) Jobs ausstehend")
            } else if let name = state.lastFilename {
                Text(name)
                    .lineLimit(1)
            } else {
                Text("Secure Print")
            }
        }
    }
}
