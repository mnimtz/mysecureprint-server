import WidgetKit
import SwiftUI

// MARK: - App Group ID (muss mit Haupt-App übereinstimmen)

private let appGroupID = "group.de.nimtz.mysecureprint"
private let cyan  = Color(red: 0, green: 0.627, blue: 0.984)
private let navy  = Color(red: 0, green: 0.157, blue: 0.329)

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
                .containerBackground(for: .widget) {
                    // Home Screen: Marken-Hintergrund; Lock Screen: transparent (systemBackground)
                    if entry.state.isHomeScreen {
                        navy
                    } else {
                        Color(.systemBackground)
                    }
                }
        }
        .configurationDisplayName("Druckstatus")
        .description("Letzter Druckauftrag und ausstehende Jobs.")
        .supportedFamilies([
            .systemSmall, .systemMedium,                          // Home Screen
            .accessoryRectangular, .accessoryCircular, .accessoryInline, // Lock Screen
        ])
    }
}

// MARK: - Entry View (dispatcht nach Familie)

struct PrintJobStatusWidgetView: View {
    let entry: PrintJobStatusEntry
    @Environment(\.widgetFamily) private var family

    var body: some View {
        switch family {
        case .systemSmall:          homeSmallView
        case .systemMedium:         homeMediumView
        case .accessoryRectangular: rectangularView
        case .accessoryCircular:    circularView
        case .accessoryInline:      inlineView
        default:                    homeSmallView
        }
    }

    // ── systemSmall — Home Screen klein (2×2) ─────────────────────────────

    private var homeSmallView: some View {
        let state = entry.state
        return VStack(alignment: .leading, spacing: 6) {
            // Header
            HStack(spacing: 6) {
                Image(systemName: "printer.fill")
                    .foregroundColor(cyan)
                    .font(.caption)
                Text("Secure Print")
                    .font(.caption2)
                    .fontWeight(.semibold)
                    .foregroundColor(.white.opacity(0.7))
            }

            Spacer()

            // Status
            if state.pendingCount > 0 {
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(state.pendingCount)")
                        .font(.system(size: 32, weight: .bold))
                        .foregroundColor(cyan)
                    Text(state.pendingCount == 1
                         ? NSLocalizedString("Job ausstehend", comment: "")
                         : NSLocalizedString("Jobs ausstehend", comment: ""))
                        .font(.caption2)
                        .foregroundColor(.white.opacity(0.8))
                }
            } else if let filename = state.lastFilename {
                VStack(alignment: .leading, spacing: 3) {
                    Image(systemName: state.statusIcon)
                        .foregroundColor(state.isError ? .red : .green)
                        .font(.title3)
                    Text(filename)
                        .font(.caption)
                        .foregroundColor(.white)
                        .lineLimit(2)
                }
            } else {
                VStack(alignment: .leading, spacing: 3) {
                    Image(systemName: "printer.fill")
                        .foregroundColor(cyan)
                        .font(.title3)
                    Text(NSLocalizedString("Kein Job", comment: ""))
                        .font(.caption)
                        .foregroundColor(.white.opacity(0.6))
                }
            }

            Spacer()

            // Queue
            if let queue = state.lastQueue {
                Text(queue)
                    .font(.caption2)
                    .foregroundColor(.white.opacity(0.55))
                    .lineLimit(1)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
    }

    // ── systemMedium — Home Screen breit (4×2) ────────────────────────────

    private var homeMediumView: some View {
        let state = entry.state
        return HStack(spacing: 16) {
            // Linke Spalte: Hauptstatus
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    Image(systemName: "printer.fill")
                        .foregroundColor(cyan)
                        .font(.caption)
                    Text("Secure Print")
                        .font(.caption2)
                        .fontWeight(.semibold)
                        .foregroundColor(.white.opacity(0.7))
                }

                Spacer()

                if state.pendingCount > 0 {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("\(state.pendingCount)")
                            .font(.system(size: 36, weight: .bold))
                            .foregroundColor(cyan)
                        Text(state.pendingCount == 1
                             ? NSLocalizedString("Job ausstehend", comment: "")
                             : NSLocalizedString("Jobs ausstehend", comment: ""))
                            .font(.caption2)
                            .foregroundColor(.white.opacity(0.8))
                    }
                } else {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundColor(.green)
                        .font(.largeTitle)
                }

                Spacer()
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            Divider()
                .background(Color.white.opacity(0.15))

            // Rechte Spalte: letzter Job
            VStack(alignment: .leading, spacing: 6) {
                Text(NSLocalizedString("Letzter Job", comment: ""))
                    .font(.caption2)
                    .fontWeight(.semibold)
                    .foregroundColor(.white.opacity(0.55))

                Spacer()

                if let filename = state.lastFilename {
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 6) {
                            Image(systemName: state.statusIcon)
                                .foregroundColor(state.isError ? .red : (state.isPending ? cyan : .green))
                                .font(.caption)
                            Text(statusLabel(state.lastStatus))
                                .font(.caption2)
                                .fontWeight(.semibold)
                                .foregroundColor(state.isError ? .red : (state.isPending ? cyan : .green))
                        }
                        Text(filename)
                            .font(.caption)
                            .foregroundColor(.white)
                            .lineLimit(2)
                        if let queue = state.lastQueue {
                            Text(queue)
                                .font(.caption2)
                                .foregroundColor(.white.opacity(0.55))
                                .lineLimit(1)
                        }
                    }
                } else {
                    Text(NSLocalizedString("Noch kein Druckauftrag", comment: ""))
                        .font(.caption)
                        .foregroundColor(.white.opacity(0.4))
                }

                Spacer()
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(14)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
    }

    // ── Lock Screen: Rectangular ──────────────────────────────────────────

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
                Text(NSLocalizedString("Kein Job", comment: ""))
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

    // ── Lock Screen: Circular ─────────────────────────────────────────────

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
                Image(systemName: state.statusIcon)
                    .font(.title3)
                    .widgetAccentable()
            }
        }
    }

    // ── Lock Screen: Inline ───────────────────────────────────────────────

    private var inlineView: some View {
        let state = entry.state
        return HStack(spacing: 4) {
            Image(systemName: state.pendingCount > 0 ? "printer.fill" : state.statusIcon)
            if state.pendingCount > 0 {
                Text("\(state.pendingCount) Jobs ausstehend")
            } else if let name = state.lastFilename {
                Text(name).lineLimit(1)
            } else {
                Text("Secure Print")
            }
        }
    }

    private func statusLabel(_ status: String?) -> String {
        switch status?.lowercased() {
        case "forwarded", "printed", "completed": return NSLocalizedString("Gedruckt", comment: "")
        case "queued":                            return NSLocalizedString("Warteschlange", comment: "")
        case "forwarding":                        return NSLocalizedString("Wird gesendet", comment: "")
        case "error", "send_failed", "failed":   return NSLocalizedString("Fehler", comment: "")
        default:                                  return status ?? ""
        }
    }
}

// MARK: - WidgetJobState Extension

private extension WidgetJobState {
    var isHomeScreen: Bool { true } // containerBackground-Entscheidung immer navy für Home
}
