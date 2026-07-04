package email.nimtz.mysecureprint.ui.screens

import androidx.compose.foundation.clickable
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
import email.nimtz.mysecureprint.data.model.PrintTarget
import email.nimtz.mysecureprint.data.store.SettingsStore
import email.nimtz.mysecureprint.ui.MSPColors
import email.nimtz.mysecureprint.ui.viewmodels.TargetsUiState
import email.nimtz.mysecureprint.ui.viewmodels.TargetsViewModel
import email.nimtz.mysecureprint.ui.viewmodels.TargetsViewModelFactory

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TargetsScreen(settings: SettingsStore) {
    val vm: TargetsViewModel = viewModel(factory = TargetsViewModelFactory(settings))
    val uiState by vm.uiState.collectAsState()
    var search by remember { mutableStateOf("") }
    var snackbar by remember { mutableStateOf("") }
    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(snackbar) {
        if (snackbar.isNotBlank()) {
            snackbarHostState.showSnackbar(snackbar)
            snackbar = ""
        }
    }

    Scaffold(
        topBar = {
            Column {
                TopAppBar(
                    title = { Text("Druckziele") },
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = MSPColors.Navy, titleContentColor = Color.White),
                )
                if (uiState is TargetsUiState.Success) {
                    SearchBar(search, onValueChange = { search = it })
                }
            }
        },
        snackbarHost = { SnackbarHost(snackbarHostState) },
    ) { padding ->
        val isRefreshing = uiState is TargetsUiState.Loading
        PullToRefreshBox(
            isRefreshing = isRefreshing,
            onRefresh = { vm.load() },
            modifier = Modifier.padding(padding),
        ) {
            when (val state = uiState) {
                is TargetsUiState.Loading -> Box(Modifier.fillMaxSize(), Alignment.Center) {
                    CircularProgressIndicator(color = MSPColors.Cyan)
                }
                is TargetsUiState.Error   -> Box(Modifier.fillMaxSize(), Alignment.Center) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Icon(Icons.Default.ErrorOutline, null, tint = MSPColors.Error, modifier = Modifier.size(48.dp))
                        Spacer(Modifier.height(8.dp))
                        Text(state.message)
                        Spacer(Modifier.height(16.dp))
                        Button(onClick = { vm.load() }) { Text("Nochmal") }
                    }
                }
                is TargetsUiState.Success -> {
                    val filtered = state.queues.filter { q ->
                        search.isBlank() || q.label.contains(search, ignoreCase = true)
                                || (q.description ?: "").contains(search, ignoreCase = true)
                    }
                    // Group by type — "print_secure" first, delegates second
                    val grouped = linkedMapOf<String, MutableList<PrintTarget>>()
                    filtered.forEach { t ->
                        val group = when (t.type) {
                            "print_secure"   -> "Mein Drucker"
                            "print_delegate" -> "Delegation"
                            else             -> "Sonstige"
                        }
                        grouped.getOrPut(group) { mutableListOf() }.add(t)
                    }

                    LazyColumn(modifier = Modifier.fillMaxSize()) {
                        grouped.forEach { (site, queues) ->
                            item { SectionHeader(site) }
                            items(queues, key = { it.id }) { queue ->
                                TargetRow(
                                    queue = queue,
                                    isDefault = queue.id == settings.defaultQueueId,
                                    onSetDefault = {
                                        vm.setDefaultQueue(queue)
                                        snackbar = "Standard: ${queue.label}"
                                    },
                                )
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
private fun SearchBar(value: String, onValueChange: (String) -> Unit) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        placeholder = { Text("Suchen…") },
        leadingIcon = { Icon(Icons.Default.Search, null) },
        trailingIcon = if (value.isNotBlank()) {{ IconButton(onClick = { onValueChange("") }) {
            Icon(Icons.Default.Clear, null)
        }}} else null,
        singleLine = true,
        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
        colors = OutlinedTextFieldDefaults.colors(focusedBorderColor = MSPColors.Cyan),
    )
}

@Composable
private fun SectionHeader(title: String) {
    Text(
        title,
        fontWeight = FontWeight.SemiBold,
        fontSize = 12.sp,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 8.dp),
    )
}

@Composable
private fun TargetRow(queue: PrintTarget, isDefault: Boolean, onSetDefault: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onSetDefault)
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            imageVector = if (queue.type == "print_delegate") Icons.Default.People else Icons.Default.Print,
            contentDescription = null,
            tint = if (isDefault) MSPColors.Cyan else MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.size(24.dp),
        )
        Spacer(Modifier.width(12.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(queue.label, fontSize = 14.sp, fontWeight = FontWeight.Medium,
                color = MaterialTheme.colorScheme.onSurface,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (!queue.description.isNullOrBlank()) {
                Text(queue.description, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
        if (isDefault) {
            Icon(Icons.Default.CheckCircle, null, tint = MSPColors.Cyan, modifier = Modifier.size(20.dp))
        }
    }
}
