import Foundation
import UserNotifications

// Wrapper um UNUserNotificationCenter — in der CLI und in der App
// einheitlich nutzbar. Zeigt native Benachrichtigungen ohne Badge,
// analog zu den Toast-Meldungen des Windows-Clients.

public struct Notify {
    public static func show(title: String, body: String, ok: Bool = true) {
        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.alert, .sound]) { granted, _ in
            guard granted else { return }
            let content = UNMutableNotificationContent()
            content.title = title
            content.body  = body
            content.sound = ok ? .default : UNNotificationSound.defaultCritical
            let req = UNNotificationRequest(
                identifier: "de.printix.send.\(UUID().uuidString)",
                content: content,
                trigger: nil
            )
            center.add(req, withCompletionHandler: nil)
        }
    }
}
