import ActivityKit
import WidgetKit
import SwiftUI

// MARK: - Dynamic Island Widget

struct PrintUploadLiveActivityWidget: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: PrintUploadAttributes.self) { context in
            // Lock Screen / Notification Banner (unter Dynamic Island)
            LockScreenView(context: context)
        } dynamicIsland: { context in
            DynamicIsland {
                // Expanded (langes Drücken auf Dynamic Island)
                DynamicIslandExpandedRegion(.leading) {
                    HStack(spacing: 6) {
                        Image(systemName: "printer.fill")
                            .foregroundColor(Color(red: 0, green: 0.627, blue: 0.984))
                            .font(.title3)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(context.attributes.filename)
                                .font(.caption)
                                .fontWeight(.semibold)
                                .lineLimit(1)
                            if context.attributes.targetCount > 1 {
                                Text(String(format: NSLocalizedString("%d Ziele", comment: ""),
                                            context.attributes.targetCount))
                                    .font(.caption2)
                                    .foregroundColor(.secondary)
                            }
                        }
                    }
                    .padding(.leading, 4)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    phaseIcon(context.state.phase)
                        .padding(.trailing, 4)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    HStack(spacing: 6) {
                        Image(systemName: "tray.fill")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        Text(context.state.targetDisplay)
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .lineLimit(1)
                        Spacer()
                        phaseLabel(context.state)
                    }
                    .padding(.horizontal, 4)
                    .padding(.bottom, 4)
                }
            } compactLeading: {
                // Kompakte Darstellung — linke Seite: Drucker-Icon
                Image(systemName: "printer.fill")
                    .foregroundColor(Color(red: 0, green: 0.627, blue: 0.984))
                    .font(.caption)
            } compactTrailing: {
                // Kompakte Darstellung — rechte Seite: Status
                switch context.state.phase {
                case .uploading:
                    ProgressView()
                        .progressViewStyle(.circular)
                        .scaleEffect(0.6)
                        .tint(Color(red: 0, green: 0.627, blue: 0.984))
                case .sent:
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundColor(.green)
                        .font(.caption)
                case .failed:
                    Image(systemName: "xmark.circle.fill")
                        .foregroundColor(.red)
                        .font(.caption)
                }
            } minimal: {
                // Minimale Darstellung (zwei gleichzeitige Activities)
                switch context.state.phase {
                case .uploading:
                    Image(systemName: "arrow.up.circle")
                        .foregroundColor(Color(red: 0, green: 0.627, blue: 0.984))
                        .font(.caption)
                case .sent:
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundColor(.green)
                        .font(.caption)
                case .failed:
                    Image(systemName: "xmark.circle.fill")
                        .foregroundColor(.red)
                        .font(.caption)
                }
            }
            .keylineTint(Color(red: 0, green: 0.627, blue: 0.984))
        }
    }

    @ViewBuilder
    private func phaseIcon(_ phase: PrintUploadAttributes.ContentState.Phase) -> some View {
        switch phase {
        case .uploading:
            ProgressView()
                .progressViewStyle(.circular)
                .scaleEffect(0.8)
                .tint(Color(red: 0, green: 0.627, blue: 0.984))
        case .sent:
            Image(systemName: "checkmark.circle.fill")
                .foregroundColor(.green)
                .font(.title2)
        case .failed:
            Image(systemName: "exclamationmark.circle.fill")
                .foregroundColor(.red)
                .font(.title2)
        }
    }

    @ViewBuilder
    private func phaseLabel(_ state: PrintUploadAttributes.ContentState) -> some View {
        switch state.phase {
        case .uploading:
            Text(NSLocalizedString("Wird gesendet…", comment: ""))
                .font(.caption2)
                .foregroundColor(.secondary)
        case .sent:
            Text(NSLocalizedString("Erfolgreich gesendet", comment: ""))
                .font(.caption2)
                .fontWeight(.semibold)
                .foregroundColor(.green)
        case .failed:
            Text(state.errorMessage ?? NSLocalizedString("Fehler", comment: ""))
                .font(.caption2)
                .foregroundColor(.red)
                .lineLimit(1)
        }
    }
}

// MARK: - Lock Screen / Banner View

private struct LockScreenView: View {
    let context: ActivityViewContext<PrintUploadAttributes>

    private var cyan: Color { Color(red: 0, green: 0.627, blue: 0.984) }
    private var navy: Color { Color(red: 0, green: 0.157, blue: 0.329) }

    var body: some View {
        HStack(spacing: 14) {
            // Icon
            ZStack {
                Circle()
                    .fill(navy)
                    .frame(width: 44, height: 44)
                Image(systemName: "printer.fill")
                    .foregroundColor(cyan)
                    .font(.title3)
            }

            // Content
            VStack(alignment: .leading, spacing: 3) {
                Text(context.attributes.filename)
                    .font(.subheadline)
                    .fontWeight(.semibold)
                    .lineLimit(1)

                HStack(spacing: 4) {
                    Image(systemName: "tray.fill")
                        .font(.caption2)
                    Text(context.state.targetDisplay)
                        .font(.caption)
                        .lineLimit(1)
                }
                .foregroundColor(.secondary)
            }

            Spacer()

            // Status
            switch context.state.phase {
            case .uploading:
                ProgressView()
                    .tint(cyan)
            case .sent:
                VStack(spacing: 2) {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundColor(.green)
                        .font(.title3)
                    Text(NSLocalizedString("Gesendet", comment: ""))
                        .font(.caption2)
                        .foregroundColor(.green)
                }
            case .failed:
                VStack(spacing: 2) {
                    Image(systemName: "exclamationmark.circle.fill")
                        .foregroundColor(.red)
                        .font(.title3)
                    Text(NSLocalizedString("Fehler", comment: ""))
                        .font(.caption2)
                        .foregroundColor(.red)
                }
            }
        }
        .padding(16)
        .background(Color(.systemBackground))
    }
}
