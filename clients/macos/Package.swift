// swift-tools-version:5.9
//
// Printix Send — Shared Swift Package
// ===================================
// Targets:
//  - PrintixSendCore  : Cross-platform lib (API, Keychain, Models, Config,
//                        Logger, optional Notifications). Wird von macOS-
//                        und iOS-Client gemeinsam genutzt.
//  - printix-send-cli : macOS-Worker für Quick-Actions (macOS-only)
//  - PrintixSendApp   : macOS-Menu-Bar-App (macOS-only)
//
// iOS-Client (MobileApp/ios-client/) linkt das Core-Target via Xcode
// als lokales SPM-Package — nur `PrintixSendCore` wird dort gebaut;
// die beiden Executable-Targets sind für iOS implizit ausgeschlossen,
// da sie AppKit-Abhängigkeiten haben.

import PackageDescription

let package = Package(
    name: "PrintixSend",
    platforms: [
        .macOS(.v13),  // macOS 13 Ventura
        .iOS(.v16),    // iOS 16 — deckt praktisch alle aktiven Geräte ab
    ],
    products: [
        .library(name: "PrintixSendCore", targets: ["PrintixSendCore"]),
        .executable(name: "printix-send-cli", targets: ["PrintixSendCLI"]),
        .executable(name: "PrintixSendApp",   targets: ["PrintixSendApp"]),
    ],
    targets: [
        .target(
            name: "PrintixSendCore",
            path: "Sources/PrintixSendCore"
        ),
        .executableTarget(
            name: "PrintixSendCLI",
            dependencies: ["PrintixSendCore"],
            path: "Sources/PrintixSendCLI"
        ),
        .executableTarget(
            name: "PrintixSendApp",
            dependencies: ["PrintixSendCore"],
            path: "Sources/PrintixSendApp"
        ),
    ]
)
