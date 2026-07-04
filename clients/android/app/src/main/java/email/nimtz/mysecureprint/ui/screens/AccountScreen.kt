package email.nimtz.mysecureprint.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import android.content.pm.PackageManager
import androidx.compose.ui.platform.LocalContext
import email.nimtz.mysecureprint.data.store.SettingsStore
import email.nimtz.mysecureprint.ui.MSPColors

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AccountScreen(settings: SettingsStore, onLogout: () -> Unit) {
    val context = LocalContext.current
    var showLogoutConfirm by remember { mutableStateOf(false) }

    val versionName = remember {
        try {
            val info = context.packageManager.getPackageInfo(context.packageName, 0)
            @Suppress("DEPRECATION")
            val code = if (android.os.Build.VERSION.SDK_INT >= 28) info.longVersionCode else info.versionCode.toLong()
            "${info.versionName} ($code)"
        } catch (_: PackageManager.NameNotFoundException) { "?" }
    }

    val initials = remember(settings.userEmail, settings.username) {
        val name = settings.userEmail.ifBlank { settings.username }
        name.split(" ", "@").filter { it.isNotBlank() }
            .take(2).mapNotNull { it.firstOrNull()?.uppercaseChar()?.toString() }
            .joinToString("")
            .ifBlank { "?" }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Konto") },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MSPColors.Navy, titleContentColor = Color.White),
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState()),
        ) {
            // Avatar header
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(MSPColors.Navy)
                    .padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Box(
                    modifier = Modifier
                        .size(72.dp)
                        .background(MSPColors.NavyLight, CircleShape),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(initials, fontWeight = FontWeight.Bold, fontSize = 26.sp, color = MSPColors.Cyan)
                }
                Spacer(Modifier.height(12.dp))
                if (settings.userEmail.isNotBlank()) {
                    Text(settings.userEmail, color = Color.White, fontWeight = FontWeight.Medium, fontSize = 15.sp)
                }
                if (settings.username.isNotBlank() && settings.username != settings.userEmail) {
                    Text(settings.username, color = Color.White.copy(alpha = 0.6f), fontSize = 13.sp)
                }
                if (settings.isAdmin) {
                    Spacer(Modifier.height(8.dp))
                    SuggestionChip(
                        onClick = {},
                        label = { Text("Admin", fontSize = 11.sp) },
                        colors = SuggestionChipDefaults.suggestionChipColors(
                            containerColor = MSPColors.Cyan.copy(alpha = 0.2f),
                        ),
                    )
                }
            }

            Spacer(Modifier.height(8.dp))

            // Server section
            ListItem(
                headlineContent = { Text("Server") },
                supportingContent = { Text(settings.serverUrl, fontSize = 12.sp) },
                leadingContent = { Icon(Icons.Default.Dns, null, tint = MSPColors.Cyan) },
            )
            HorizontalDivider()

            // Delegation toggle
            ListItem(
                headlineContent = { Text("Delegation-Druck") },
                supportingContent = { Text("Senden an andere Printix-Benutzer") },
                leadingContent = { Icon(Icons.Default.People, null, tint = MSPColors.Cyan) },
                trailingContent = {
                    Switch(
                        checked = settings.delegationEnabled,
                        onCheckedChange = { settings.delegationEnabled = it },
                        colors = SwitchDefaults.colors(checkedThumbColor = MSPColors.Cyan,
                            checkedTrackColor = MSPColors.Cyan.copy(alpha = 0.4f)),
                    )
                }
            )
            HorizontalDivider()

            // Version
            ListItem(
                headlineContent = { Text("Version") },
                supportingContent = { Text(versionName) },
                leadingContent = { Icon(Icons.Default.Info, null, tint = MaterialTheme.colorScheme.onSurfaceVariant) },
            )
            HorizontalDivider()

            Spacer(Modifier.height(16.dp))

            // Logout
            Box(modifier = Modifier.padding(horizontal = 16.dp)) {
                OutlinedButton(
                    onClick = { showLogoutConfirm = true },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = MSPColors.Error),
                    shape = RoundedCornerShape(12.dp),
                ) {
                    Icon(Icons.Default.Logout, null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("Abmelden")
                }
            }
        }
    }

    if (showLogoutConfirm) {
        AlertDialog(
            onDismissRequest = { showLogoutConfirm = false },
            title = { Text("Abmelden?") },
            text = { Text("Du wirst von diesem Gerät abgemeldet.") },
            confirmButton = {
                TextButton(
                    onClick = { settings.logout(); onLogout() },
                    colors = ButtonDefaults.textButtonColors(contentColor = MSPColors.Error),
                ) { Text("Abmelden") }
            },
            dismissButton = { TextButton(onClick = { showLogoutConfirm = false }) { Text("Abbrechen") } },
        )
    }
}
