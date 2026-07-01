import SwiftUI
import AVFoundation
import AudioToolbox

/// Schlanker QR-Code-Scanner mit AVCaptureSession.
///
/// Erwartet als Payload entweder
///   - JSON: `{"v":1,"server":"https://..."}` (aus /my/mobile-app/qr.png)
///   - oder einen reinen URL-String (Fallback, falls jemand einen
///     einfachen Link-QR scannt).
///
/// Liefert per Closure `onResult` die entdeckte Server-URL und schließt
/// dann von selbst. Bei Kamera-Fehlern/fehlender Permission wird
/// `onResult` mit `nil` aufgerufen — der Aufrufer zeigt dann einen Hinweis.
struct QRScannerView: UIViewControllerRepresentable {
    var onResult: (String?) -> Void

    func makeUIViewController(context: Context) -> QRScannerViewController {
        let vc = QRScannerViewController()
        vc.onResult = onResult
        return vc
    }

    func updateUIViewController(_ uiViewController: QRScannerViewController, context: Context) {}
}

final class QRScannerViewController: UIViewController,
                                     AVCaptureMetadataOutputObjectsDelegate {

    var onResult: ((String?) -> Void)?

    private let session = AVCaptureSession()
    private var previewLayer: AVCaptureVideoPreviewLayer?
    private var didDeliver = false

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        configureSession()
    }

    override func viewWillAppear(_ animated: Bool) {
        super.viewWillAppear(animated)
        if session.inputs.isEmpty == false && !session.isRunning {
            // Capture-Session auf Background-Queue starten — Apples
            // eigene Empfehlung, sonst blockt der Main-Thread kurz.
            DispatchQueue.global(qos: .userInitiated).async { [weak self] in
                self?.session.startRunning()
            }
        }
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        if session.isRunning {
            session.stopRunning()
        }
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        previewLayer?.frame = view.layer.bounds
    }

    private func configureSession() {
        guard let device = AVCaptureDevice.default(for: .video),
              let input  = try? AVCaptureDeviceInput(device: device),
              session.canAddInput(input) else {
            deliver(nil)
            return
        }
        session.addInput(input)

        let output = AVCaptureMetadataOutput()
        guard session.canAddOutput(output) else { deliver(nil); return }
        session.addOutput(output)
        output.setMetadataObjectsDelegate(self, queue: DispatchQueue.main)
        output.metadataObjectTypes = [.qr]

        let layer = AVCaptureVideoPreviewLayer(session: session)
        layer.videoGravity = .resizeAspectFill
        layer.frame = view.layer.bounds
        view.layer.addSublayer(layer)
        self.previewLayer = layer

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            self?.session.startRunning()
        }
    }

    // MARK: - AVCaptureMetadataOutputObjectsDelegate

    func metadataOutput(_ output: AVCaptureMetadataOutput,
                        didOutput metadataObjects: [AVMetadataObject],
                        from connection: AVCaptureConnection) {
        guard !didDeliver,
              let obj = metadataObjects.first as? AVMetadataMachineReadableCodeObject,
              let value = obj.stringValue,
              !value.isEmpty else { return }
        // Einmalig: sobald der erste Treffer da ist, abdrehen.
        didDeliver = true
        AudioServicesPlaySystemSound(1057) // kurzes „ping"
        session.stopRunning()
        deliver(parseServer(from: value))
    }

    // MARK: - Payload-Parsing

    private func parseServer(from raw: String) -> String {
        // 1) JSON-Payload aus /my/mobile-app/qr.png
        if let data = raw.data(using: .utf8),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let server = obj["server"] as? String,
           !server.isEmpty {
            return server
        }
        // 2) Reine URL
        if raw.lowercased().hasPrefix("http://") || raw.lowercased().hasPrefix("https://") {
            return raw
        }
        // 3) Fallback — wir liefern roh und lassen den Caller entscheiden.
        return raw
    }

    private func deliver(_ value: String?) {
        let cb = onResult
        onResult = nil
        cb?(value)
    }
}

