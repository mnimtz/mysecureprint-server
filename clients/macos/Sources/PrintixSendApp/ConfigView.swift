import SwiftUI
import PrintixSendCore

// Konfigurations-View — wird in der Settings-Scene von
// PrintixSendMenuApp eingebettet und per Cmd+, aufgerufen.
// Enthält: Server-URL, Device-Name, Status der letzten Synchronisation.

@MainActor
struct ConfigView: View {
    @State private var serverUrl:  String = ""
    @State private var deviceName: String = ""
    @State private var saved = false
    @State private var syncMsg: String?

    private func loadIfEmpty() {
        if serverUrl.isEmpty  { serverUrl  = AppState.shared.config.serverUrl }
        if deviceName.isEmpty { deviceName = AppState.shared.config.deviceName }
    }

    var body: some View {
        Form {
            Section(header: Text("Verbindung").font(.headline)) {
                TextField("Server-URL", text: $serverUrl)
                    .textFieldStyle(.roundedBorder)
                    .help("z. B. https://printix.example.com")
                TextField("Geräte-Name", text: $deviceName)
                    .textFieldStyle(.roundedBorder)
                Text("Der Geräte-Name erscheint in der Printix-Web-Oberfläche unter „Angemeldete Geräte“.")
                    .font(.footnote).foregroundColor(.secondary)
            }

            Section(header: Text("Aktionen").font(.headline)) {
                HStack {
                    Button("Speichern") {
                        AppState.shared.updateConfig(serverUrl: serverUrl,
                                                     deviceName: deviceName)
                        saved = true
                    }
                    .buttonStyle(.borderedProminent)

                    Button("Jetzt anmelden…") {
                        LoginWindowController.shared.show()
                    }

                    Button("Quick Actions neu synchronisieren") {
                        Task {
                            await AppState.shared.refreshTargetsAndSync()
                            syncMsg = "Synchronisiert — \(AppState.shared.targets.count) Ziele"
                        }
                    }
                }
                if saved { Text("Gespeichert.").font(.footnote).foregroundColor(.secondary) }
                if let syncMsg { Text(syncMsg).font(.footnote).foregroundColor(.secondary) }
            }

            Section(header: Text("Info").font(.headline)) {
                Text("Client-Version \(ApiClient.clientVersion)")
                    .font(.footnote).foregroundColor(.secondary)
                Text("Log: ~/Library/Logs/PrintixSend/")
                    .font(.footnote).foregroundColor(.secondary)
            }
        }
        .padding(20)
        .frame(width: 480)
        .onAppear { loadIfEmpty() }
    }
}
