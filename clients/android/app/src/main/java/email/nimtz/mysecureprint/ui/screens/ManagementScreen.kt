package email.nimtz.mysecureprint.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import email.nimtz.mysecureprint.data.model.ManagedUser
import email.nimtz.mysecureprint.data.store.SettingsStore
import email.nimtz.mysecureprint.ui.MSPColors
import email.nimtz.mysecureprint.ui.viewmodels.ManagementUiState
import email.nimtz.mysecureprint.ui.viewmodels.ManagementViewModel
import email.nimtz.mysecureprint.ui.viewmodels.ManagementViewModelFactory

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ManagementScreen(settings: SettingsStore) {
    val vm: ManagementViewModel = viewModel(factory = ManagementViewModelFactory(settings))
    val uiState by vm.uiState.collectAsState()
    var search by remember { mutableStateOf("") }

    Scaffold(
        topBar = {
            Column {
                TopAppBar(
                    title = { Text("Management") },
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = MSPColors.Navy, titleContentColor = Color.White),
                )
                if (uiState is ManagementUiState.Success) {
                    OutlinedTextField(
                        value = search,
                        onValueChange = { search = it },
                        placeholder = { Text("Benutzer suchen…") },
                        leadingIcon = { Icon(Icons.Default.Search, null) },
                        trailingIcon = if (search.isNotBlank()) {{ IconButton(onClick = { search = "" }) {
                            Icon(Icons.Default.Clear, null)
                        }}} else null,
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
                        colors = OutlinedTextFieldDefaults.colors(focusedBorderColor = MSPColors.Cyan),
                    )
                }
            }
        }
    ) { padding ->
        val isRefreshing = uiState is ManagementUiState.Loading
        PullToRefreshBox(isRefreshing = isRefreshing, onRefresh = { vm.load() },
            modifier = Modifier.padding(padding)) {
            when (val state = uiState) {
                is ManagementUiState.Loading -> Box(Modifier.fillMaxSize(), Alignment.Center) {
                    CircularProgressIndicator(color = MSPColors.Cyan)
                }
                is ManagementUiState.Error   -> Box(Modifier.fillMaxSize(), Alignment.Center) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Icon(Icons.Default.ErrorOutline, null, tint = MSPColors.Error, modifier = Modifier.size(48.dp))
                        Spacer(Modifier.height(8.dp))
                        Text(state.message)
                        Spacer(Modifier.height(16.dp))
                        Button(onClick = { vm.load() }) { Text("Nochmal") }
                    }
                }
                is ManagementUiState.Success -> {
                    val filtered = state.users.filter { u ->
                        search.isBlank()
                                || u.username.contains(search, ignoreCase = true)
                                || u.email.contains(search, ignoreCase = true)
                    }
                    if (filtered.isEmpty()) {
                        Box(Modifier.fillMaxSize(), Alignment.Center) {
                            Text("Keine Benutzer gefunden.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    } else {
                        LazyColumn(modifier = Modifier.fillMaxSize()) {
                            item {
                                Text("${filtered.size} Benutzer", fontSize = 12.sp,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp))
                            }
                            items(filtered, key = { it.id }) { user ->
                                UserRow(user = user)
                                HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.3f),
                                    modifier = Modifier.padding(start = 56.dp))
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun UserRow(user: ManagedUser) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        // Avatar
        Surface(
            shape = androidx.compose.foundation.shape.CircleShape,
            color = MSPColors.NavyLight,
            modifier = Modifier.size(36.dp),
        ) {
            Box(contentAlignment = Alignment.Center) {
                Text(
                    user.username.firstOrNull()?.uppercaseChar()?.toString() ?: "?",
                    fontWeight = FontWeight.Bold,
                    color = MSPColors.Cyan,
                    fontSize = 14.sp,
                )
            }
        }
        Spacer(Modifier.width(12.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(user.username, fontWeight = FontWeight.Medium, fontSize = 14.sp,
                color = MaterialTheme.colorScheme.onSurface,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (user.email.isNotBlank()) {
                Text(user.email, fontSize = 12.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
        if (user.role.isNotBlank()) {
            SuggestionChip(
                onClick = {},
                label = { Text(user.role, fontSize = 11.sp) },
                colors = SuggestionChipDefaults.suggestionChipColors(
                    containerColor = if (user.isAdmin) MSPColors.Cyan.copy(alpha = 0.15f)
                                     else MaterialTheme.colorScheme.surfaceVariant
                )
            )
        }
    }
}
