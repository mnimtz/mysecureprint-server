import Foundation
import PrintixSendCore

// `printix-send-cli` — wird von den Finder-Quick-Actions aufgerufen.
//
// Nutzung:
//   printix-send-cli --target <target-id> <file1> [file2 …]
//   printix-send-cli --targets                (listet gecachte Ziele)
//   printix-send-cli --version
//
// Liest Bearer-Token aus dem Keychain (Service "de.printix.send"),
// Server-URL aus ~/Library/Application Support/PrintixSend/config.json.
// Keine interaktiven Prompts — Login läuft in der Menu-Bar-App.

AppLogger.shared.alsoStderr = ProcessInfo.processInfo.environment["PRINTIX_VERBOSE"] != nil

func printUsage() {
    print("""
    Printix Send CLI \(ApiClient.clientVersion)

      printix-send-cli --target <target-id> <file1> [file2 …]
      printix-send-cli --targets
      printix-send-cli --version

    Konfiguration: ~/Library/Application Support/PrintixSend/config.json
    Token        : macOS-Keychain (Service „de.printix.send")
    Log          : ~/Library/Logs/PrintixSend/printix-send-YYYYMMDD.log
    """)
}

// ─── Arg-Parsing (leichtgewichtig, kein ArgumentParser-Dep) ────────────

var args = CommandLine.arguments
args.removeFirst()

if args.isEmpty || args.first == "--help" || args.first == "-h" {
    printUsage()
    exit(0)
}

if args.first == "--version" {
    print("printix-send-cli \(ApiClient.clientVersion)")
    exit(0)
}

let config = ConfigStore.shared.load()
if config.serverUrl.isEmpty {
    Notify.show(title: "Printix Send",
                body: "Nicht konfiguriert — bitte PrintixSend.app einmal starten.",
                ok: false)
    AppLogger.shared.error("CLI: serverUrl leer — Config fehlt.")
    exit(2)
}

let keychain = KeychainStore()
let token = keychain.get()
if token == nil || token?.isEmpty == true {
    Notify.show(title: "Printix Send",
                body: "Nicht angemeldet — bitte PrintixSend.app öffnen und einloggen.",
                ok: false)
    AppLogger.shared.error("CLI: Kein Token im Keychain.")
    exit(3)
}

let api: ApiClient
do {
    api = try ApiClient(baseUrl: config.serverUrl, token: token)
} catch {
    AppLogger.shared.error("CLI: \(error.localizedDescription)")
    exit(4)
}

// ─── --targets ─────────────────────────────────────────────────────────

if args.first == "--targets" {
    let sem = DispatchSemaphore(value: 0)
    Task {
        do {
            let list = try await api.targets()
            for t in list {
                print("\(t.id)\t\(t.label)")
            }
        } catch {
            fputs("Fehler: \(error.localizedDescription)\n", stderr)
        }
        sem.signal()
    }
    sem.wait()
    exit(0)
}

// ─── --target <id> <files…> ────────────────────────────────────────────

guard let tIdx = args.firstIndex(of: "--target"),
      tIdx + 1 < args.count else {
    printUsage()
    exit(1)
}
let targetId = args[tIdx + 1]
var files = args
files.removeSubrange(tIdx...(tIdx + 1))
files = files.filter { !$0.hasPrefix("--") }

if files.isEmpty {
    fputs("Keine Dateien übergeben.\n", stderr)
    exit(1)
}

AppLogger.shared.info("CLI: \(files.count) Datei(en) → target=\(targetId)")

var ok = 0, fail = 0
let sem = DispatchSemaphore(value: 0)
Task {
    for f in files {
        let url = URL(fileURLWithPath: f)
        let name = url.lastPathComponent
        do {
            let result = try await api.send(filePath: f, targetId: targetId)
            if result.ok == true {
                ok += 1
                // Bei 202 (async) noch kein Printix-Job-ID — nur „angenommen".
                let msg = result.status == "queued"
                    ? "\(name) — angenommen (läuft im Hintergrund)"
                    : "\(name) — gesendet"
                Notify.show(title: "Printix Send", body: msg, ok: true)
            } else {
                fail += 1
                let msg = result.error ?? result.message ?? "Unbekannter Fehler"
                Notify.show(title: "Printix Send — Fehler", body: "\(name): \(msg)", ok: false)
            }
        } catch {
            fail += 1
            Notify.show(title: "Printix Send — Fehler",
                        body: "\(name): \(error.localizedDescription)", ok: false)
        }
    }
    sem.signal()
}
sem.wait()

AppLogger.shared.info("CLI: fertig — \(ok) ok, \(fail) fail")
exit(fail == 0 ? 0 : 5)
