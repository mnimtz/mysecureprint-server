import Foundation

// macOS-only: der iOS-Client nutzt die iOS Share-Extension statt
// Finder-Quick-Actions. Auf anderen Plattformen wird die gesamte
// Datei übersprungen, damit PrintixSendCore auch für iOS baut.
#if os(macOS)

// macOS-Pendant zu windows-client/PrintixSend/Services/SendToSync.cs.
//
// Erzeugt pro Target einen Finder-Dienst ("Quick Action") unter
// ~/Library/Services/. Jeder Service ruft beim Aufruf
//   /usr/local/bin/printix-send-cli --target <id> <files>
// auf. Ziel-Labels sind idempotent — alte werden vor jedem Sync gelöscht.
//
// Technisch ist ein Quick Action ein `.workflow`-Bundle mit einem
// winzigen Info.plist + document.wflow, das einen `ShellScriptAction`
// enthält. Wir bauen beide Dateien direkt aus Code, ohne Automator.

public struct QuickActionsSync {
    public let cliPath: String
    public let servicesDir: URL
    public let prefix: String

    public init(cliPath: String,
                servicesDir: URL? = nil,
                prefix: String = "Printix Send") {
        self.cliPath = cliPath
        self.prefix = prefix
        if let dir = servicesDir {
            self.servicesDir = dir
        } else {
            let home = FileManager.default.homeDirectoryForCurrentUser
            self.servicesDir = home.appendingPathComponent("Library/Services", isDirectory: true)
        }
    }

    public func sync(targets: [Target]) throws {
        try FileManager.default.createDirectory(at: servicesDir,
                                                withIntermediateDirectories: true)
        removeExisting()
        for target in targets {
            try writeWorkflow(target: target)
            AppLogger.shared.info("QuickAction geschrieben: \(prefix) — \(target.label) → target=\(target.id)")
        }
        refreshFinder()
    }

    private func removeExisting() {
        guard let entries = try? FileManager.default.contentsOfDirectory(at: servicesDir,
                                                                         includingPropertiesForKeys: nil) else { return }
        let marker = "\(prefix) —"
        for url in entries where url.lastPathComponent.hasPrefix(marker)
                                && url.pathExtension == "workflow" {
            try? FileManager.default.removeItem(at: url)
        }
    }

    private func sanitize(_ s: String) -> String {
        var out = s
        for bad in [":", "/"] { out = out.replacingOccurrences(of: bad, with: "_") }
        return out
    }

    private func writeWorkflow(target: Target) throws {
        let name = "\(prefix) — \(sanitize(target.label))"
        let bundle = servicesDir.appendingPathComponent("\(name).workflow", isDirectory: true)
        let contents = bundle.appendingPathComponent("Contents", isDirectory: true)
        try FileManager.default.createDirectory(at: contents, withIntermediateDirectories: true)

        // Info.plist — minimaler Service für Dateien (NSSendTypes: NSFilenamesPboardType)
        let info: [String: Any] = [
            "CFBundleDevelopmentRegion": "de",
            "CFBundleIdentifier":        "de.printix.send.quickaction.\(target.id)",
            "CFBundleName":              name,
            "CFBundlePackageType":       "APPL",
            "CFBundleSignature":         "????",
            "NSServices": [[
                "NSMenuItem":       ["default": name],
                "NSMessage":        "runWorkflowAsService",
                "NSPortName":       name,
                "NSSendFileTypes":  ["public.item"],
                "NSSendTypes":      ["NSFilenamesPboardType"],
                "NSRequiredContext":["NSTextContent": "FilePath"],
                "NSServiceDescription": "Sendet an Printix — \(target.label)"
            ]]
        ]
        let infoData = try PropertyListSerialization.data(fromPropertyList: info,
                                                          format: .xml, options: 0)
        try infoData.write(to: contents.appendingPathComponent("Info.plist"))

        // document.wflow — das ist das Workflow-Dokument, das Automator
        // ausführt. Wir bauen es minimal: ein RunShellScript-Action,
        // Input = "files", Output = "none". Automator frisst XML-Plist
        // mit genau diesem Schema. Felder, die Automator zur Laufzeit
        // erwartet, aber die wir nicht brauchen, bleiben leer / default.
        let script = "\"\(cliPath)\" --target \"\(target.id)\" \"$@\"\n"
        let wflow = buildWflow(script: script, serviceName: name)
        let wflowData = try PropertyListSerialization.data(fromPropertyList: wflow,
                                                           format: .xml, options: 0)
        try wflowData.write(to: contents.appendingPathComponent("document.wflow"))

        // Touch — damit Finder/pbs die Services neu einliest.
        try? FileManager.default.setAttributes([.modificationDate: Date()],
                                               ofItemAtPath: bundle.path)
    }

    private func buildWflow(script: String, serviceName: String) -> [String: Any] {
        // Minimales Automator-Workflow-Plist mit einer einzigen
        // "Run Shell Script"-Aktion. Die Aktions-UUID stammt aus
        // Apples Standard-Action-Paket (/System/Library/Automator/
        // Run Shell Script.action) — stabil über macOS-Versionen.
        [
            "AMApplicationVersion": "2.10",
            "AMApplicationBuild":   "523",
            "AMDockAutoLaunch":     false,
            "AMWorkflowInputMode":  0,
            "WorkflowTypeIdentifier": "com.apple.Automator.servicesMenu",
            "actions": [[
                "action": [
                    "AMAccepts":     ["Container": "List", "Optional": true, "Types": ["com.apple.cocoa.path"]],
                    "AMActionVersion":    "2.0.3",
                    "AMApplication":      ["Automator"],
                    "AMParameterProperties": [
                        "COMMAND_STRING":  [:],
                        "CheckedForUserDefaultShell": [:],
                        "inputMethod":     [:],
                        "shell":           [:],
                        "source":          [:]
                    ],
                    "AMProvides":    ["Container": "List", "Types": ["com.apple.cocoa.path"]],
                    "ActionBundlePath":  "/System/Library/Automator/Run Shell Script.action",
                    "ActionName":        "Run Shell Script",
                    "ActionParameters": [
                        "COMMAND_STRING": script,
                        "CheckedForUserDefaultShell": true,
                        "inputMethod": 1,          // 1 = "as arguments"
                        "shell":       "/bin/zsh",
                        "source":      ""
                    ],
                    "BundleIdentifier":  "com.apple.RunShellScript",
                    "CFBundleVersion":   "2.0.3",
                    "CanShowSelectedItemsWhenRun": false,
                    "CanShowWhenRun": false,
                    "Category":    ["AMCategoryUtilities"],
                    "Class Name":  "RunShellScriptAction",
                    "InputUUID":   UUID().uuidString,
                    "Keywords":    ["Shell","Script","Command","Run","Unix"],
                    "OutputUUID":  UUID().uuidString,
                    "UUID":        UUID().uuidString,
                    "UnlocalizedApplications": ["Automator"],
                    "arguments":   [:],
                    "isViewVisible": 1,
                    "location":    "309.500000:316.000000",
                    "nameSpace":   "",
                    "shortPath":   "/System/Library/Automator/Run Shell Script.action",
                    "sourceVisible": false,
                    "vertical":    1
                ],
                "isViewVisible": 1
            ]],
            "connectors":    [String: Any](),
            "workflowMetaData": [
                "serviceApplicationBundleID": "",
                "serviceApplicationPath":     "",
                "serviceInputTypeIdentifier": "com.apple.Automator.fileSystemObject",
                "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
                "serviceProcessesInput":      0,
                "serviceName":                serviceName
            ]
        ]
    }

    private func refreshFinder() {
        // pbs neu laden, damit die Services sofort im Rechtsklick-Menü
        // erscheinen. Ohne Neustart geht auch, braucht aber evtl. bis
        // zu einer Minute; mit `pbs -flush` ist der Eintrag sofort da.
        let task = Process()
        task.launchPath = "/System/Library/CoreServices/pbs"
        task.arguments  = ["-flush"]
        try? task.run()
    }
}

#endif // os(macOS)
