package email.nimtz.mysecureprint.ui.screens

import android.net.Uri
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import email.nimtz.mysecureprint.data.store.SettingsStore
import email.nimtz.mysecureprint.ui.MSPColors

private data class TabItem(val label: String, val icon: ImageVector, val route: String)

@Composable
fun MainScreen(settings: SettingsStore, initialSharedFileUri: Uri?, onLogout: () -> Unit) {
    var selectedTab by remember { mutableStateOf(0) }

    val tabs = buildList {
        add(TabItem("Upload", Icons.Default.Upload, "upload"))
        add(TabItem("Ziele", Icons.Default.Print, "targets"))
        add(TabItem("Jobs", Icons.Default.Schedule, "jobs"))
        if (settings.isAdmin) add(TabItem("Admin", Icons.Default.AdminPanelSettings, "management"))
        add(TabItem("Konto", Icons.Default.Person, "account"))
    }

    Scaffold(
        bottomBar = {
            NavigationBar(containerColor = MSPColors.Navy) {
                tabs.forEachIndexed { idx, tab ->
                    NavigationBarItem(
                        selected = selectedTab == idx,
                        onClick = { selectedTab = idx },
                        icon = { Icon(tab.icon, tab.label) },
                        label = { Text(tab.label) },
                        colors = NavigationBarItemDefaults.colors(
                            selectedIconColor = MSPColors.Cyan,
                            selectedTextColor = MSPColors.Cyan,
                            indicatorColor = MSPColors.NavyLight,
                            unselectedIconColor = MaterialTheme.colorScheme.onSurfaceVariant,
                            unselectedTextColor = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    )
                }
            }
        }
    ) { paddingValues ->
        Box(modifier = Modifier.padding(paddingValues)) {
            when (tabs.getOrNull(selectedTab)?.route) {
                "upload"     -> UploadScreen(settings = settings, initialSharedFileUri = initialSharedFileUri)
                "targets"    -> TargetsScreen(settings = settings)
                "jobs"       -> JobsScreen(settings = settings)
                "management" -> ManagementScreen(settings = settings)
                "account"    -> AccountScreen(settings = settings, onLogout = onLogout)
                else         -> UploadScreen(settings = settings, initialSharedFileUri = initialSharedFileUri)
            }
        }
    }
}
