//
//  PrintUploadWidgetBundle.swift
//  PrintUploadWidget
//
//  Created by Marcus Nimtz on 05.07.26.
//

import WidgetKit
import SwiftUI

@main
struct PrintUploadWidgetBundle: WidgetBundle {
    var body: some Widget {
        PrintUploadLiveActivityWidget()
        PrintJobStatusWidget()
    }
}
