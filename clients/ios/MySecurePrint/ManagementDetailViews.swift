import SwiftUI
import PrintixSendCore

// MARK: - Drucker-Detail

struct PrinterDetailView: View {
    let printer: MgmtPrinter

    var body: some View {
        List {
            Section {
                HStack(spacing: 16) {
                    ZStack {
                        RoundedRectangle(cornerRadius: 12)
                            .fill(MSP.navyGradient)
                            .frame(width: 56, height: 56)
                        Image(systemName: "printer.fill")
                            .font(.title2)
                            .foregroundColor(.white)
                    }
                    VStack(alignment: .leading, spacing: 5) {
                        Text(printer.name)
                            .font(.headline)
                        onlineChip(online: printer.isOnline == true)
                    }
                }
                .padding(.vertical, 4)
            }

            Section(String(localized: "Informationen")) {
                if let model = printer.model, !model.isEmpty {
                    infoRow(icon: "cpu", label: String(localized: "Modell"), value: model)
                }
                if let loc = printer.location, !loc.isEmpty {
                    infoRow(icon: "mappin", label: String(localized: "Standort"), value: loc)
                }
                if let qid = printer.queueId, !qid.isEmpty {
                    infoRow(icon: "tray.full", label: String(localized: "Queue-ID"), value: qid)
                }
                if let s = printer.status, !s.isEmpty {
                    infoRow(icon: "circle.fill",
                            label: String(localized: "Status"),
                            value: s,
                            valueColor: printer.isOnline == true ? .green : .secondary)
                }
            }
        }
        .listStyle(.insetGrouped)
        .brandNavStyle(title: printer.name)
        .tint(MSP.cyan)
    }
}

// MARK: - Benutzer-Detail

struct UserDetailView: View {
    let user: MgmtUser

    private var initials: String {
        let n = user.name ?? user.email ?? "?"
        return n.split(separator: " ")
            .prefix(2)
            .compactMap { $0.first.map(String.init) }
            .joined()
            .uppercased()
    }

    var body: some View {
        List {
            Section {
                HStack(spacing: 16) {
                    ZStack {
                        Circle()
                            .fill(MSP.navyGradient)
                            .frame(width: 56, height: 56)
                        Text(initials.isEmpty ? "?" : initials)
                            .font(.system(size: 20, weight: .bold, design: .rounded))
                            .foregroundColor(.white)
                    }
                    VStack(alignment: .leading, spacing: 5) {
                        if let n = user.name, !n.isEmpty {
                            Text(n).font(.headline)
                        }
                        if let e = user.email, !e.isEmpty {
                            Text(e)
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        }
                        if let role = user.role, !role.isEmpty {
                            roleChip(role: role)
                        }
                    }
                }
                .padding(.vertical, 4)
            }

            Section(String(localized: "Konto")) {
                if let e = user.email, !e.isEmpty {
                    infoRow(icon: "envelope", label: String(localized: "E-Mail"), value: e)
                }
                if let role = user.role, !role.isEmpty {
                    infoRow(icon: "person.badge.key", label: String(localized: "Rolle"), value: role.capitalized)
                }
            }
        }
        .listStyle(.insetGrouped)
        .brandNavStyle(title: user.name ?? user.email ?? user.id)
        .tint(MSP.cyan)
    }

    private func roleChip(role: String) -> some View {
        let isAdmin = role.lowercased().contains("admin")
        return Text(role.capitalized)
            .font(.caption2.bold())
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(isAdmin ? MSP.cyan.opacity(0.18) : Color.gray.opacity(0.15))
            .foregroundColor(isAdmin ? MSP.cyan : .secondary)
            .clipShape(Capsule())
    }
}

// MARK: - Workstation-Detail

struct WorkstationDetailView: View {
    let workstation: MgmtWorkstation

    var body: some View {
        List {
            Section {
                HStack(spacing: 16) {
                    ZStack {
                        RoundedRectangle(cornerRadius: 12)
                            .fill(MSP.navyGradient)
                            .frame(width: 56, height: 56)
                        Image(systemName: "desktopcomputer")
                            .font(.title2)
                            .foregroundColor(.white)
                    }
                    VStack(alignment: .leading, spacing: 5) {
                        Text(workstation.hostname)
                            .font(.headline)
                        onlineChip(online: workstation.isOnline == true)
                    }
                }
                .padding(.vertical, 4)
            }

            Section(String(localized: "Informationen")) {
                if let email = workstation.userEmail, !email.isEmpty {
                    infoRow(icon: "person", label: String(localized: "Benutzer"), value: email)
                }
                if let ls = workstation.lastSeen, !ls.isEmpty {
                    infoRow(icon: "clock", label: String(localized: "Zuletzt gesehen"),
                            value: formatLastSeen(ls))
                }
            }
        }
        .listStyle(.insetGrouped)
        .brandNavStyle(title: workstation.hostname)
        .tint(MSP.cyan)
    }

    private func formatLastSeen(_ raw: String) -> String {
        let formats = [
            "yyyy-MM-dd'T'HH:mm:ssZ",
            "yyyy-MM-dd'T'HH:mm:ss.SSSZ",
            "yyyy-MM-dd HH:mm:ss",
        ]
        let df = DateFormatter()
        for fmt in formats {
            df.dateFormat = fmt
            if let d = df.date(from: raw) {
                let out = DateFormatter()
                out.dateStyle = .short
                out.timeStyle = .short
                return out.string(from: d)
            }
        }
        return raw
    }
}

// MARK: - Shared Helpers

private func onlineChip(online: Bool) -> some View {
    Text(online ? String(localized: "Online") : String(localized: "Offline"))
        .font(.caption2.bold())
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .background(online ? Color.green.opacity(0.15) : Color.gray.opacity(0.15))
        .foregroundColor(online ? .green : .secondary)
        .clipShape(Capsule())
}

private func infoRow(icon: String, label: String, value: String,
                     valueColor: Color = .secondary) -> some View {
    HStack {
        Image(systemName: icon)
            .foregroundStyle(Color.accentColor)
            .frame(width: 26)
        Text(label)
        Spacer()
        Text(value)
            .foregroundStyle(valueColor)
            .font(.subheadline)
            .multilineTextAlignment(.trailing)
            .lineLimit(2)
    }
}
